"""
Robust Selenium Browser Pool with retry logic, health checks, and crash recovery.
"""
import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementNotInteractableException,
    WebDriverException,
    StaleElementReferenceException,
)

from app.config import Config

logger = logging.getLogger(__name__)

_MAX_POOL = int(os.getenv("BROWSER_POOL_SIZE", "3"))
_BROWSER_TIMEOUT = int(getattr(Config, "BROWSER_TIMEOUT", "25"))
_RETRY_ATTEMPTS = int(os.getenv("SELENIUM_RETRY_ATTEMPTS", "3"))
_RETRY_DELAY = float(os.getenv("SELENIUM_RETRY_DELAY", "1.0"))
_HEALTH_CHECK_INTERVAL = int(os.getenv("SELENIUM_HEALTH_CHECK_INTERVAL", "60"))


class DriverState(Enum):
    """Driver lifecycle state."""
    HEALTHY = "healthy"
    RECOVERING = "recovering"
    CRASHED = "crashed"
    RETIRED = "retired"


@dataclass
class DriverMetrics:
    """Track driver health metrics."""
    created_at: float
    last_used_at: float
    navigation_count: int = 0
    form_fill_count: int = 0
    error_count: int = 0
    state: DriverState = DriverState.HEALTHY
    session_id: Optional[str] = None


def _make_options(headless: bool = None) -> ChromeOptions:
    """Create resilient Chrome options."""
    headless = headless if headless is not None else Config.BROWSER_HEADLESS
    opts = ChromeOptions()
    
    if headless:
        opts.add_argument("--headless=new")
    
    # Memory and stability
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-software-rasterizer")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-plugins")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-ipc-flooding-protection")
    opts.add_argument("--disable-hang-monitor")
    
    # Improve stability under high load
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-web-resources")
    opts.add_argument("--disable-features=TranslateUI")
    opts.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
    
    # First run settings
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--disable-component-update")
    
    # UX settings
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-prompt-on-repost")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--lang=en-US")
    
    # Connection pooling
    opts.add_argument("--enable-automation")
    opts.add_argument("--dns-prefetch-disable")
    opts.add_argument("--disable-features=LazyFrameLoading")
    opts.set_capability("pageLoadStrategy", "eager")
    
    # User agent
    opts.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    # Stealth options
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("w3c", True)
    
    # Prefs for stability
    prefs = {
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.images": 2,
        "profile.default.content_setting_values.notifications": 2,
        "profile.default_content_settings.cookies": 1,
    }
    opts.add_experimental_option("prefs", prefs)
    
    return opts


def _inject_stealth(driver):
    """Inject stealth scripts to avoid detection."""
    stealth_js = """
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    Object.defineProperty(navigator, 'permissions', {
        get: () => ({
            query: () => Promise.resolve({ state: 'denied' })
        })
    });
    try { window.chrome = { runtime: {} }; } catch (e) {}
    """
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
    except Exception as e:
        logger.debug("Stealth injection failed (non-critical): %s", e)


class _Session:
    """Represents an active browser session."""
    
    def __init__(self, driver, session_id: str, metrics: DriverMetrics):
        self.driver = driver
        self.session_id = session_id
        self.metrics = metrics
        self.last_error: Optional[Exception] = None
    
    def mark_error(self, exc: Exception):
        """Record an error for this session."""
        self.metrics.error_count += 1
        self.last_error = exc
        logger.warning("Session %s error (count=%d): %s", self.session_id, self.metrics.error_count, exc)


def _retry_operation(func, attempts: int = _RETRY_ATTEMPTS, delay: float = _RETRY_DELAY):
    """Decorator for retryable operations with exponential backoff."""
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(attempts):
            try:
                return func(*args, **kwargs)
            except (TimeoutException, WebDriverException, StaleElementReferenceException) as e:
                last_exc = e
                if attempt < attempts - 1:
                    wait_time = delay * (2 ** attempt)
                    logger.debug("Operation failed (attempt %d/%d), retrying in %.1fs: %s", 
                                attempt + 1, attempts, wait_time, e)
                    time.sleep(wait_time)
                else:
                    logger.warning("Operation failed after %d attempts: %s", attempts, e)
        raise last_exc
    return wrapper


class BrowserPool:
    """Thread-safe pool of Selenium Chrome drivers with recovery and health checks."""

    def __init__(self, pool_size: int = None):
        self.pool_size = pool_size or _MAX_POOL
        self._drivers: List[webdriver.Chrome] = []
        self._metrics: Dict[str, DriverMetrics] = {}
        self._available: asyncio.Queue[_Session] = asyncio.Queue()
        self._session_map: Dict[str, _Session] = {}
        self._lock = asyncio.Lock()
        self._health_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=self.pool_size + 2)
        self._initialized = False
        self._health_check_task: Optional[asyncio.Task] = None

    def _create_driver(self) -> webdriver.Chrome:
        """Create and initialize a Chrome driver."""
        opts = _make_options()
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
        
        if os.path.exists(chromedriver_path):
            service = ChromeService(executable_path=chromedriver_path)
        else:
            service = ChromeService()
        
        try:
            driver = webdriver.Chrome(service=service, options=opts)
            driver.set_script_timeout(_BROWSER_TIMEOUT)
            driver.set_page_load_timeout(_BROWSER_TIMEOUT)
            driver.implicitly_wait(10)
            _inject_stealth(driver)
            logger.info("Driver created successfully")
            return driver
        except WebDriverException as e:
            logger.error("Failed to create driver: %s", e)
            raise

    def _test_driver_health(self, driver: webdriver.Chrome) -> bool:
        """Test if driver is responsive."""
        try:
            driver.execute_script("return 1;")
            return True
        except Exception as e:
            logger.debug("Health check failed: %s", e)
            return False

    def _recover_driver(self, session: _Session) -> bool:
        """Attempt to recover a crashed driver."""
        try:
            logger.info("Attempting to recover driver %s", session.session_id)
            session.metrics.state = DriverState.RECOVERING
            
            # Try to get it responsive
            try:
                session.driver.execute_script("return 1;")
                session.metrics.state = DriverState.HEALTHY
                session.metrics.error_count = 0
                logger.info("Driver %s recovered", session.session_id)
                return True
            except:
                pass
            
            # If recovery fails, quit and create new
            try:
                session.driver.quit()
            except:
                pass
            
            new_driver = self._create_driver()
            session.driver = new_driver
            session.metrics.state = DriverState.HEALTHY
            session.metrics.error_count = 0
            logger.info("Driver %s replaced", session.session_id)
            return True
        except Exception as e:
            logger.error("Driver recovery failed: %s", e)
            session.metrics.state = DriverState.CRASHED
            return False

    async def _run_health_checks(self):
        """Periodically check driver health and recover crashed drivers."""
        while self._initialized:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            async with self._health_lock:
                for session_id, session in list(self._session_map.items()):
                    if session.metrics.state == DriverState.CRASHED:
                        loop = asyncio.get_event_loop()
                        if await loop.run_in_executor(self._executor, self._recover_driver, session):
                            logger.info("Recovered crashed driver: %s", session_id)
                        else:
                            logger.warning("Could not recover driver: %s", session_id)

    async def init(self):
        """Initialize the browser pool."""
        if self._initialized:
            return
        
        async with self._lock:
            if self._initialized:
                return
            
            loop = asyncio.get_event_loop()
            for i in range(self.pool_size):
                try:
                    driver = await loop.run_in_executor(self._executor, self._create_driver)
                    metrics = DriverMetrics(created_at=time.time(), last_used_at=time.time())
                    sess = _Session(driver, f"pool_{i}", metrics)
                    self._drivers.append(driver)
                    self._metrics[f"pool_{i}"] = metrics
                    await self._available.put(sess)
                    logger.info("Selenium driver %d/%d ready", i + 1, self.pool_size)
                except Exception as e:
                    logger.error("Failed to initialize driver %d: %s", i, e)
            
            self._initialized = True
            
            # Start health check task
            if not self._health_check_task:
                self._health_check_task = asyncio.create_task(self._run_health_checks())
                logger.info("Health check task started")

    async def _checkout(self, session_id: str) -> _Session:
        """Checkout a driver session with retry logic."""
        await self.init()
        
        existing = self._session_map.get(session_id)
        if existing:
            existing.metrics.last_used_at = time.time()
            return existing
        
        try:
            sess = await asyncio.wait_for(self._available.get(), timeout=30)
        except asyncio.TimeoutError:
            logger.error("Browser pool checkout timed out (all drivers busy)")
            raise
        
        sess.session_id = session_id
        sess.metrics.session_id = session_id
        sess.metrics.last_used_at = time.time()
        self._session_map[session_id] = sess
        return sess

    async def _release(self, session_id: str, clear: bool = False):
        """Release a driver session back to the pool."""
        sess = self._session_map.pop(session_id, None)
        if not sess:
            return
        
        loop = asyncio.get_event_loop()
        
        if clear:
            try:
                await loop.run_in_executor(self._executor, sess.driver.get, "about:blank")
            except Exception:
                pass
        
        await self._available.put(sess)

    @_retry_operation
    def _navigate(self, session: _Session, url: str):
        """Navigate to URL with document ready wait."""
        driver = session.driver
        
        try:
            driver.get(url)
            session.metrics.navigation_count += 1
            
            # Wait for DOM ready
            WebDriverWait(driver, _BROWSER_TIMEOUT).until(
                lambda d: d.execute_script("return document.readyState") in ["complete", "interactive"]
            )
        except Exception as e:
            session.mark_error(e)
            raise

    @_retry_operation
    def _detect_fields_sync(self, session: _Session) -> List[Dict[str, Any]]:
        """Detect form fields on current page."""
        driver = session.driver
        fields = []
        
        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR,
                "input:not([type='hidden']):not([type='submit']):not([type='button']), textarea, select"
            )
            
            for idx, el in enumerate(elements):
                try:
                    tag = el.tag_name.lower()
                    ftype = el.get_attribute("type") or ""
                    fid = el.get_attribute("id") or ""
                    name = el.get_attribute("name") or ""
                    placeholder = el.get_attribute("placeholder") or ""
                    required = el.get_attribute("required") is not None
                    
                    label_text = ""
                    if fid:
                        try:
                            labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{fid}']")
                            if labels:
                                label_text = labels[0].text
                        except:
                            pass
                    
                    fields.append({
                        "index": idx,
                        "tag": tag,
                        "type": ftype,
                        "id": fid,
                        "name": name,
                        "placeholder": placeholder,
                        "label_text": label_text,
                        "required": required,
                    })
                except StaleElementReferenceException:
                    continue
        except Exception as e:
            session.mark_error(e)
            logger.warning("Field detection failed: %s", e)
        
        return fields

    @_retry_operation
    def _fill_and_submit_sync(self, session: _Session, mapping: Dict[str, Any], fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Fill form fields and submit using fast JS injection."""
        driver = session.driver
        filled = 0
        total = len(fields)

        js_fill = []
        for idx, f in enumerate(fields):
            k = str(idx)
            if k not in mapping:
                continue
            action = mapping[k].get("action", "skip")
            val = mapping[k].get("value", "")
            if action == "skip":
                continue
            fid = f.get("id", "")
            fname = f.get("name", "")
            tag = f.get("tag", "")
            selector = None
            if fid:
                selector = "#" + fid
            elif fname:
                selector = "[name='" + fname + "']"
            else:
                continue
            if action == "check":
                js_fill.append("const el_" + str(idx) + "=document.querySelector('" + selector + "');if(el_" + str(idx) + "){el_" + str(idx) + ".checked=true;}")
            elif tag == "select":
                js_fill.append("const el_" + str(idx) + "=document.querySelector('" + selector + "');if(el_" + str(idx) + "){el_" + str(idx) + ".value='" + str(val) + "';}")
            else:
                safe_val = str(val).replace("'", "\'")
                js_fill.append("const el_" + str(idx) + "=document.querySelector('" + selector + "');if(el_" + str(idx) + "){el_" + str(idx) + ".value='" + safe_val + "';}")
            filled += 1

        if js_fill:
            try:
                driver.execute_script("\n".join(js_fill))
            except Exception as e:
                logger.debug("JS fill failed: %s", e)

        submit_clicked = False
        try:
            result = driver.execute_script("""
                const btns = document.querySelectorAll("button[type='submit'], input[type='submit']");
                if (btns.length > 0) { btns[0].click(); return "button"; }
                const forms = document.querySelectorAll("form");
                if (forms.length > 0) { forms[0].submit(); return "form"; }
                return "none";
            """)
            if result != "none":
                submit_clicked = True
                logger.info("Form submitted via JS (%s)", result)
        except Exception as e:
            session.mark_error(e)
            logger.warning("Form submission failed: %s", e)

        session.metrics.form_fill_count += 1
        status = "submitted" if submit_clicked else "submit_not_found"

        try:
            final_url = driver.current_url
        except Exception:
            final_url = ""

        return {
            "status": status,
            "fields_filled": filled,
            "fields_total": total,
            "screenshot": "",
            "final_url": final_url,
        }

    def _take_screenshot(self, session: _Session, path: str, full_page: bool = False):
        """Take a screenshot."""
        driver = session.driver
        try:
            if full_page:
                original_size = driver.get_window_size()
                total_height = driver.execute_script("return document.body.scrollHeight")
                driver.set_window_size(1366, max(total_height, 768))
                driver.save_screenshot(path)
                driver.set_window_size(original_size["width"], original_size["height"])
            else:
                driver.save_screenshot(path)
            return path
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return ""

    # ── async public API ───────────────────────────────────────────────

    async def navigate(self, url: str, session_id: str = "default"):
        """Navigate to URL."""
        sess = await self._checkout(session_id)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._navigate, sess, url)

    async def detect_fields(self, session_id: str = "default") -> List[Dict[str, Any]]:
        """Detect form fields."""
        sess = self._session_map.get(session_id)
        if not sess:
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._detect_fields_sync, sess)

    async def ai_fill_and_submit(
        self,
        mapping: Dict[str, Any],
        session_id: str = "default",
        fields: List[Dict[str, Any]] = None,
    ) -> dict:
        """Fill and submit form."""
        sess = self._session_map.get(session_id)
        if not sess:
            return {"status": "error", "fields_filled": 0, "fields_total": 0, "screenshot": "", "final_url": ""}
        
        if fields is None:
            fields = await self.detect_fields(session_id)
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(self._executor, self._fill_and_submit_sync, sess, mapping, fields)
        
        # Take screenshot
        try:
            ss_dir = getattr(Config, "SCREENSHOT_DIR", "static/screenshots")
            os.makedirs(ss_dir, exist_ok=True)
            ss = os.path.join(ss_dir, f"{int(time.time())}_{result['status']}.png")
            await loop.run_in_executor(self._executor, self._take_screenshot, sess, ss, False)
            result["screenshot"] = ss
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
        
        return result

    async def screenshot(self, path: str, session_id: str = "default", full_page: bool = False):
        """Take screenshot."""
        sess = self._session_map.get(session_id)
        if not sess:
            return
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._take_screenshot, sess, path, full_page)

    async def evaluate(self, script: str, session_id: str = "default"):
        """Execute JavaScript."""
        sess = self._session_map.get(session_id)
        if not sess:
            return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: sess.driver.execute_script(script))

    async def query_selector(self, selector: str, session_id: str = "default") -> bool:
        """Check if selector exists."""
        sess = self._session_map.get(session_id)
        if not sess:
            return False
        loop = asyncio.get_event_loop()
        def _check():
            try:
                return len(sess.driver.find_elements(By.CSS_SELECTOR, selector)) > 0
            except Exception:
                return False
        return await loop.run_in_executor(self._executor, _check)

    async def title(self, session_id: str = "default") -> str:
        """Get page title."""
        sess = self._session_map.get(session_id)
        if not sess:
            return ""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: sess.driver.title)

    async def content(self, session_id: str = "default") -> str:
        """Get page source."""
        sess = self._session_map.get(session_id)
        if not sess:
            return ""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: sess.driver.page_source)

    async def analyze_target(self, url: str, session_id: str = "default") -> dict:
        """Analyze target URL."""
        if not url.startswith("http"):
            url = "https://" + url
        
        await self._checkout(session_id)
        try:
            await self.navigate(url, session_id)
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.warning("Page load failed for %s: %s", url, e)

        try:
            page_title = await self.title(session_id)
            html = await self.content(session_id)
            has_form = await self.evaluate(
                "return document.querySelectorAll('input, textarea, select').length > 2",
                session_id
            )
            emails = await self.evaluate(
                """() => {
                    const text = document.body.innerText;
                    const matches = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g);
                    return Array.from(new Set(matches || []));
                }""",
                session_id
            )
            has_captcha = bool(
                await self.query_selector(".g-recaptcha", session_id)
                or await self.query_selector(".h-captcha", session_id)
                or await self.query_selector("iframe[src*='recaptcha']", session_id)
                or await self.query_selector("iframe[src*='hcaptcha']", session_id)
            )

            contact_links = await self.evaluate(
                """return Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => ['contact', 'about', 'reach'].some(k => a.innerText.toLowerCase().includes(k)))
                    .map(a => a.href);""",
                session_id
            )
            
            contact_url = ""
            for link in (contact_links or []):
                if link and link.startswith("http"):
                    contact_url = link
                    break

            return {
                "url": url,
                "has_form": bool(has_form),
                "has_captcha": bool(has_captcha),
                "emails": emails or [],
                "page_title": page_title or "",
                "html": html[:5000] if html else "",  # Limit HTML size
                "contact_url": contact_url,
            }
        except Exception as e:
            logger.error("Analysis failed for %s: %s", url, e)
            return {
                "url": url,
                "has_form": False,
                "has_captcha": False,
                "emails": [],
                "page_title": "",
                "html": "",
                "contact_url": "",
            }

    async def get_metrics(self) -> Dict[str, Any]:
        """Get pool health metrics."""
        return {
            "pool_size": self.pool_size,
            "active_sessions": len(self._session_map),
            "available_drivers": self._available.qsize(),
            "drivers": {
                sid: {
                    "state": m.state.value,
                    "navigations": m.navigation_count,
                    "forms": m.form_fill_count,
                    "errors": m.error_count,
                }
                for sid, m in self._metrics.items()
            }
        }

    async def shutdown(self):
        """Shutdown the pool."""
        logger.info("Shutting down Selenium browser pool...")
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        async with self._lock:
            self._initialized = False
            
            for sess in list(self._session_map.values()):
                try:
                    sess.driver.quit()
                except Exception:
                    pass
            
            for driver in self._drivers:
                try:
                    driver.quit()
                except Exception:
                    pass
            
            self._drivers.clear()
            self._session_map.clear()
            self._metrics.clear()
            
            while not self._available.empty():
                try:
                    self._available.get_nowait()
                except asyncio.QueueEmpty:
                    break
        
        self._executor.shutdown(wait=False)
        logger.info("Selenium pool shut down.")


# ── singleton ─────────────────────────────────────────────────────────
_pool_instance: Optional[BrowserPool] = None


async def get_pool() -> BrowserPool:
    """Get or create browser pool."""
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = BrowserPool()
        await _pool_instance.init()
    return _pool_instance


async def shutdown_pool():
    """Shutdown browser pool."""
    global _pool_instance
    if _pool_instance:
        await _pool_instance.shutdown()
        _pool_instance = None
