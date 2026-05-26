"""
Persistent Browser Manager — Playwright with stealth, field detection,
form fill + submit, CAPTCHA solve, and keep-open verification.
"""

import base64
import logging
import os
import queue
import re
import threading
import time
from concurrent.futures import Future
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from app.config import Config

logger = logging.getLogger(__name__)


def _on_worker(method):
    from functools import wraps

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        return self._run_on_worker(method, self, *args, **kwargs)

    return wrapped


class BrowserManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._playwright = None
        self._persist_ctx = None
        self._user_data_dir = getattr(Config, "BROWSER_DATA_DIR", "browser_data")
        self._contexts = {}
        self._pages = {}
        self._last_session_id = "default"
        self._job_queue = queue.Queue()
        self._worker_thread = None
        self._worker_started = threading.Event()
        self._shutdown = False
        self._start_worker()

    def _start_worker(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="playwright-worker"
        )
        self._worker_thread.start()
        self._worker_started.wait(timeout=5)

    def _worker_loop(self):
        self._worker_started.set()
        while not self._shutdown:
            try:
                job = self._job_queue.get(timeout=1)
                if job is None:
                    continue
                future, func, args, kwargs = job
                try:
                    result = func(*args, **kwargs)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
            except queue.Empty:
                continue
            except Exception:
                logger.exception("Worker loop error")

    def _run_on_worker(self, func, *args, **kwargs):
        if threading.current_thread() is self._worker_thread:
            return func(*args, **kwargs)
        future = Future()
        self._job_queue.put((future, func, args, kwargs))
        return future.result(timeout=60)

    def _launch_browser(self):
        if threading.current_thread() is not self._worker_thread:
            return self._run_on_worker(self._launch_browser)
        if self._playwright is None:
            self._playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-component-update",
            "--disable-checker-imaging",
            "--js-flags=--max_old_space_size=4096",
            "--disable-features=Translate",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--disable-hang-monitor",
        ]

        proxy_config = None
        if Config.TOR_ENABLED and Config.TOR_PROXY:
            proxy_config = {"server": Config.TOR_PROXY}
        elif Config.BROWSER_PROXY:
            proxy_config = {"server": Config.BROWSER_PROXY}

        os.makedirs(self._user_data_dir, exist_ok=True)
        self._persist_ctx = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            headless=Config.BROWSER_HEADLESS,
            args=launch_args,
            proxy=proxy_config,
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Europe/London",
            geolocation={"latitude": 52.6333, "longitude": -1.6959},
            permissions=["geolocation"],
            color_scheme="dark",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
            bypass_csp=True,
            ignore_https_errors=True,
        )

        self._persist_ctx.add_init_script("""() => {
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            delete window.__playwright;
            delete window.__pw_manual;
        }""")

        page = self._persist_ctx.pages[0] if self._persist_ctx.pages else self._persist_ctx.new_page()
        try:
            page.goto(Config.BROWSER_START_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

        self._contexts["default"] = self._persist_ctx
        self._pages["default"] = page
        logger.info("Browser launched (persistent context)")

    def _ensure_browser(self):
        if threading.current_thread() is not self._worker_thread:
            return self._run_on_worker(self._ensure_browser)
        if self._persist_ctx:
            try:
                _ = self._persist_ctx.pages
                return True
            except Exception:
                try:
                    self._persist_ctx.close()
                except Exception:
                    pass
                self._persist_ctx = None
                self._contexts.clear()
                self._pages.clear()

        try:
            self._launch_browser()
            return True
        except Exception as e:
            logger.error("Browser launch failed: %s", e)
            return False

    def _create_context(self, session_id):
        if threading.current_thread() is not self._worker_thread:
            return self._run_on_worker(self._create_context, session_id)
        if self._persist_ctx:
            try:
                page = self._persist_ctx.new_page()
                try:
                    page.goto(Config.BROWSER_START_URL, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                self._contexts[session_id] = self._persist_ctx
                self._pages[session_id] = page
                return self._persist_ctx
            except Exception as e:
                logger.warning("Context creation failed, restarting browser: %s", e)
                self._restart_browser()
                if self._persist_ctx:
                    try:
                        page = self._persist_ctx.new_page()
                        self._contexts[session_id] = self._persist_ctx
                        self._pages[session_id] = page
                        return self._persist_ctx
                    except Exception:
                        pass
        return None

    def _restart_browser(self):
        """Kill and restart the browser after crash."""
        try:
            if self._persist_ctx:
                self._persist_ctx.close()
        except Exception:
            pass
        self._persist_ctx = None
        self._contexts.clear()
        self._pages.clear()
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._launch_browser()
        logger.info("Browser restarted after crash")

    def _get_page_impl(self, session_id):
        if not self._ensure_browser():
            raise RuntimeError("Browser not available")
        self._last_session_id = session_id
        if session_id not in self._contexts:
            self._create_context(session_id)
        try:
            ctx = self._contexts[session_id]
            if session_id in self._pages:
                page = self._pages[session_id]
                # Verify page is alive
                page.url  # This throws if page is closed
                return page
        except Exception:
            logger.warning("Page/context dead for session %s, recreating", session_id)
            # Clean up dead session
            self._contexts.pop(session_id, None)
            self._pages.pop(session_id, None)
            self._create_context(session_id)

        ctx = self._contexts.get(session_id)
        if not ctx:
            self._create_context(session_id)
            ctx = self._contexts[session_id]
        if ctx.pages:
            self._pages[session_id] = ctx.pages[0]
            return ctx.pages[0]
        page = ctx.new_page()
        self._pages[session_id] = page
        return page

    @_on_worker
    def navigate(self, url, session_id="default", timeout=None):
        timeout = timeout or Config.BROWSER_TIMEOUT
        if not url.startswith("http"):
            url = "https://" + url
        page = self._get_page_impl(session_id)
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout, 5000))
                except Exception:
                    pass
                logger.info(f"Navigate success: {url}")
                return page.url
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Navigate attempt {attempt + 1} failed for {url}: {e}, retrying...")
                    time.sleep(1)
                else:
                    logger.error(f"Navigate failed after {max_retries} attempts for {url}: {e}")
                    raise

    @_on_worker
    def get_content(self, session_id="default"):
        page = self._get_page_impl(session_id)
        return page.content()

    @_on_worker
    def get_url(self, session_id="default"):
        page = self._get_page_impl(session_id)
        return page.url

    @_on_worker
    def detect_fields(self, session_id="default"):
        page = self._get_page_impl(session_id)
        try:
            fields = page.eval_on_selector_all(
                "input:not([type=hidden]),textarea,select",
                """els => els.map((el, idx) => {
                    let label = '';
                    if (el.id) {
                        const l = document.querySelector('label[for="' + el.id + '"]');
                        if (l) label = l.innerText.trim();
                    }
                    if (!label) {
                        const p = el.closest('label,div,p,fieldset');
                        if (p) label = p.innerText.split(String.fromCharCode(10))[0].trim().substring(0, 120);
                    }
                    const tag = el.tagName.toLowerCase();
                    let selector;
                    if (el.id) {
                        selector = tag + '#' + CSS.escape(el.id);
                    } else if (el.name) {
                        selector = tag + '[name="' + CSS.escape(el.name) + '"]';
                    } else if (el.placeholder) {
                        selector = tag + '[placeholder="' + CSS.escape(el.placeholder) + '"]';
                    } else {
                        selector = tag + ':nth-of-type(' + (idx + 1) + ')';
                    }
                    return {
                        index: idx,
                        tag: tag,
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        aria_label: el.getAttribute('aria-label') || '',
                        label_text: label,
                        required: !!el.required,
                        selector: selector,
                    };
                })""",
            )
            if fields:
                return fields
        except Exception as e:
            logger.warning("Field detection failed (main frame): %s", e)

        try:
            for frame_idx, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                try:
                    frame_fields = frame.eval_on_selector_all(
                        "input:not([type=hidden]),textarea,select",
                        """els => els.map((el, idx) => {
                            let label = '';
                            if (el.id) {
                                const l = document.querySelector('label[for="' + el.id + '"]');
                                if (l) label = l.innerText.trim();
                            }
                            if (!label) {
                                const p = el.closest('label,div,p,fieldset');
                                if (p) label = p.innerText.split(String.fromCharCode(10))[0].trim().substring(0, 120);
                            }
                            const tag = el.tagName.toLowerCase();
                            let selector;
                            if (el.id) {
                                selector = tag + '#' + CSS.escape(el.id);
                            } else if (el.name) {
                                selector = tag + '[name="' + CSS.escape(el.name) + '"]';
                            } else if (el.placeholder) {
                                selector = tag + '[placeholder="' + CSS.escape(el.placeholder) + '"]';
                            } else {
                                selector = tag + ':nth-of-type(' + (idx + 1) + ')';
                            }
                            return {
                                index: idx,
                                tag: tag,
                                type: el.type || '',
                                name: el.name || '',
                                id: el.id || '',
                                placeholder: el.placeholder || '',
                                aria_label: el.getAttribute('aria-label') || '',
                                label_text: label,
                                required: !!el.required,
                                selector: selector,
                                frame_index: %d,
                            };
                        })""" % frame_idx,
                    )
                    if frame_fields:
                        return frame_fields
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Field detection failed (iframes): %s", e)

        return []

    @_on_worker
    def analyze_target(self, url, session_id="default", timeout=None):
        timeout = timeout or Config.BROWSER_TIMEOUT
        if not url.startswith("http"):
            url = "https://" + url
        page = self._get_page_impl(session_id)
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as e:
            logger.warning(f"Page load timeout for {url}, continuing: {e}")
        
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        try:
            has_form = page.locator("form, input:not([type=hidden]), textarea").count() > 0
            has_captcha = page.locator(
                "iframe[src*='recaptcha'], .g-recaptcha, [data-sitekey], iframe[src*='hcaptcha'], .h-captcha"
            ).count() > 0
            html = page.content()
            emails = list(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html)))
            page_title = page.title()

            logger.info(f"Analysis: {url} - form={has_form}, captcha={has_captcha}, emails={len(emails)}")
            
            return {
                "has_form": has_form,
                "has_captcha": has_captcha,
                "emails": emails[:10],
                "page_title": page_title,
                "html": html,
            }
        except Exception as e:
            logger.error(f"Analysis failed for {url}: {e}")
            return {
                "has_form": False,
                "has_captcha": False,
                "emails": [],
                "page_title": "",
                "html": "",
            }

    @_on_worker
    def find_contact_url(self, base_url, session_id="default"):
        page = self._get_page_impl(session_id)
        try:
            hrefs = page.eval_on_selector_all(
                "a",
                """els => els.map(el => ({
                    href: el.getAttribute('href') || '',
                    text: (el.innerText || '').toLowerCase().trim(),
                }))""",
            )
        except Exception:
            return None

        keywords = ["contact", "contact us", "request", "demo", "get started", "inquire", "inquiry", "book"]
        for item in hrefs:
            href = (item.get("href") or "").strip()
            text = item.get("text") or ""
            if not href or href.startswith("javascript"):
                continue
            if any(k in text for k in keywords) or any(k in href.lower() for k in ["contact", "request", "demo", "inquire", "book"]):
                return urljoin(base_url, href)
        return None

    @_on_worker
    def screenshot(self, session_id="default", quality=70):
        page = self._get_page_impl(session_id)
        return page.screenshot(type="jpeg", quality=quality, full_page=False)

    def get_last_session_id(self):
        return self._last_session_id

    @_on_worker
    def ai_fill_and_submit(self, mapping, session_id="default", keep_open=True, fields=None):
        page = self._get_page_impl(session_id)
        if fields is None:
            fields = self.detect_fields(session_id=session_id)
        if fields is None:
            fields = []
        filled = 0
        total = len(fields)

        def _truthy(v):
            if isinstance(v, bool):
                return v
            s = str(v).strip().lower()
            return s in {"1", "true", "yes", "y", "on", "checked"}

        for idx, f in enumerate(fields):
            k = str(idx)
            if k not in mapping or mapping[k].get("action") == "skip":
                continue
            sel = f.get("selector", "")
            if not sel:
                continue
            val = mapping[k].get("value", "")
            tag = f.get("tag", "input")
            ftype = (f.get("type") or "").lower()
            action = (mapping[k].get("action") or "fill").lower()
            frame_idx = mapping[k].get("frame_index") or f.get("frame_index")
            target = page
            if frame_idx is not None:
                try:
                    target = page.frames[int(frame_idx)]
                except Exception:
                    target = page
            
            try:
                target.locator(sel).scroll_into_view_if_needed(timeout=2000)

                if action == "check" or (ftype in {"checkbox", "radio"} and action != "fill"):
                    if _truthy(val) or action == "check":
                        target.check(sel, timeout=2000)
                        filled += 1
                elif tag == "select":
                    target.select_option(sel, str(val), timeout=2000)
                    filled += 1
                else:
                    target.fill(sel, str(val), timeout=2000)
                    filled += 1
            except Exception as e:
                # Fallback: try by name attribute
                name = f.get("name", "")
                fid = f.get("id", "")
                fallback_sel = None
                if name:
                    fallback_sel = f'[name="{name}"]'
                elif fid:
                    fallback_sel = f'#{fid}'
                if fallback_sel and fallback_sel != sel:
                    try:
                        if action == "check":
                            target.check(fallback_sel, timeout=2000)
                        elif tag == "select":
                            target.select_option(fallback_sel, str(val), timeout=2000)
                        else:
                            target.fill(fallback_sel, str(val), timeout=2000)
                        filled += 1
                        logger.debug(f"Filled via fallback {fallback_sel}")
                        continue
                    except Exception:
                        pass
                logger.debug(f"Failed to fill {sel}: {e}")
            
            time.sleep(0.15)

        logger.info(f"Filled {filled}/{total} fields")

        submit_clicked = False
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit')",
            "button:has-text('Send')",
            "button:has-text('Contact')",
            "button:has-text('Request')",
            "button:has-text('Get Started')",
            "button:has-text('Request Demo')",
            "button:has-text('Send Message')",
            "a:has-text('Submit')",
            "button:has-text('Go')",
            "button:has-text('Send')",
        ]

        for sel in submit_selectors:
            try:
                locator = page.locator(sel)
                if locator.count() > 0:
                    locator.first.scroll_into_view_if_needed(timeout=2000)
                    time.sleep(0.2)
                    locator.first.click(timeout=3000)
                    submit_clicked = True
                    logger.info(f"Form submitted via: {sel}")
                    break
            except Exception as e:
                logger.debug(f"Submit attempt failed ({sel}): {e}")
                continue

        if not submit_clicked:
            for frame in page.frames:
                try:
                    for sel in submit_selectors:
                        locator = frame.locator(sel)
                        if locator.count() > 0:
                            locator.first.scroll_into_view_if_needed(timeout=2000)
                            time.sleep(0.2)
                            locator.first.click(timeout=3000)
                            submit_clicked = True
                            logger.info(f"Form submitted via frame: {sel}")
                            break
                    if submit_clicked:
                        break
                except Exception:
                    continue

        if not submit_clicked:
            try:
                page.evaluate("""() => { const f = document.querySelector('form'); if (f) f.submit(); }""")
                submit_clicked = True
                logger.info("Form submitted via: form.submit()")
            except Exception:
                pass

        status = "submitted" if submit_clicked else "submit_not_found"
        
        if submit_clicked:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

        os.makedirs(Config.SCREENSHOT_DIR, exist_ok=True)
        ss = os.path.join(Config.SCREENSHOT_DIR, f"{int(time.time())}_{status}.png")
        try:
            page.screenshot(path=ss, full_page=True)
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            ss = ""

        result = {
            "status": status,
            "fields_filled": filled,
            "fields_total": total,
            "screenshot": ss,
            "final_url": page.url,
        }
        logger.info(f"Submit result: {result}")
        return result

    @_on_worker
    def solve_captcha(self, session_id="default"):
        from app.captcha import _solve_captcha_inner

        page = self._get_page_impl(session_id)
        return _solve_captcha_inner(page)

    @_on_worker
    def click(self, x, y, session_id="default"):
        page = self._get_page_impl(session_id)
        page.mouse.click(x, y)
        return True

    def shutdown(self):
        self._shutdown = True
        with self._lock:
            if self._persist_ctx:
                try:
                    self._persist_ctx.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass


browser_mgr = BrowserManager()
