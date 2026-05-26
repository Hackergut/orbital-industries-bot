"""
Async Browser Pool — Playwright async with pool of isolated contexts.
Each target gets its own context for true parallelism and no session bleed.
"""
import asyncio
import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, BrowserContext, Page

from app.config import Config

logger = logging.getLogger(__name__)

_LAUNCH_ARGS = [
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

_STEALTH_SCRIPT = """() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    try { window.chrome = { runtime: {} }; } catch (e) {}
    try {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    } catch (e) {}
    try { delete window.__playwright; } catch (e) {}
    try { delete window.__pw_manual; } catch (e) {}
}"""


class BrowserPool:
    def __init__(self, pool_size: int = None):
        self.pool_size = pool_size or int(os.getenv("BROWSER_POOL_SIZE", "3"))
        self._playwright = None
        self._browser = None
        self._contexts: List[BrowserContext] = []
        self._available: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._page_map: Dict[str, Page] = {}
        self._lock = asyncio.Lock()
        self._user_data_dir = getattr(Config, "BROWSER_DATA_DIR", "browser_data")
        self._initialized = False

    async def init(self):
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            self._playwright = await async_playwright().start()

            proxy = None
            if Config.TOR_ENABLED and Config.TOR_PROXY:
                proxy = {"server": Config.TOR_PROXY}
            elif Config.BROWSER_PROXY:
                proxy = {"server": Config.BROWSER_PROXY}

            self._browser = await self._playwright.chromium.launch(
                headless=Config.BROWSER_HEADLESS,
                args=_LAUNCH_ARGS,
                proxy=proxy,
            )

            for i in range(self.pool_size):
                ctx = await self._browser.new_context(
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
                await ctx.add_init_script(_STEALTH_SCRIPT)
                self._contexts.append(ctx)
                await self._available.put(ctx)
                logger.info("Browser context %d/%d ready", i + 1, self.pool_size)

            self._initialized = True
            logger.info("Browser pool initialized with %d contexts", self.pool_size)

    async def _checkout(self, session_id: str) -> tuple[BrowserContext, Page]:
        await self.init()
        try:
            ctx = await asyncio.wait_for(self._available.get(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Browser pool checkout timed out for %s", session_id)
            raise
        try:
            page = await asyncio.wait_for(ctx.new_page(), timeout=10)
        except asyncio.TimeoutError:
            await self._available.put(ctx)
            logger.warning("Browser pool new_page timed out for %s", session_id)
            raise
        self._page_map[session_id] = page
        return ctx, page

    async def _release(self, session_id: str, ctx: BrowserContext, page: Page):
        try:
            await asyncio.wait_for(page.close(), timeout=5)
        except Exception:
            pass
        self._page_map.pop(session_id, None)
        await self._available.put(ctx)

    def _get_page(self, session_id: str) -> Page:
        page = self._page_map.get(session_id)
        if not page:
            raise RuntimeError(f"No page for session {session_id}")
        return page

    async def analyze_target(self, url: str, session_id: str = "default") -> dict:
        ctx, page = await self._checkout(session_id)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=Config.BROWSER_TIMEOUT)
            await asyncio.sleep(1.5)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            page_title = await page.title()
            html = await page.content()
            has_form = await page.evaluate(
                """() => document.querySelectorAll('input, textarea, select').length > 2"""
            )
            emails = await page.evaluate("""() => {
                const text = document.body.innerText;
                const matches = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g);
                return Array.from(new Set(matches || []));
            }""")
            has_captcha = bool(
                await page.query_selector(".g-recaptcha") or
                await page.query_selector(".h-captcha") or
                await page.query_selector("iframe[src*='recaptcha']") or
                await page.query_selector("iframe[src*='hcaptcha']")
            )

            contact_links = await page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                const keywords = ['contact', 'about', 'reach', 'get in touch'];
                return links
                    .filter(a => keywords.some(k => a.innerText.toLowerCase().includes(k)))
                    .map(a => a.href);
            }""")
            contact_url = ""
            for link in contact_links:
                if link.startswith("http"):
                    contact_url = link
                    break

            result = {
                "url": url,
                "has_form": bool(has_form),
                "has_captcha": bool(has_captcha),
                "page_title": page_title or "",
                "emails": emails,
                "contact_url": contact_url,
                "html": html,
            }
            logger.info("Analyzed %s: form=%s captcha=%s", url, result["has_form"], result["has_captcha"])
            return result
        finally:
            await self._release(session_id, ctx, page)

    async def detect_fields(self, session_id: str = "default") -> List[Dict[str, Any]]:
        page = self._get_page(session_id)
        fields = await page.evaluate("""() => {
            const out = [];
            const inputs = document.querySelectorAll('input, textarea, select');
            inputs.forEach((el, idx) => {
                const label = document.querySelector(`label[for="${el.id}"]`);
                const aria = el.getAttribute('aria-label');
                out.push({
                    index: idx,
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    label_text: label ? label.innerText.trim() : (aria || ''),
                    aria_label: aria || '',
                    required: el.required || false,
                    frame_index: null,
                });
            });
            return out;
        }""")
        logger.info("Detected %d fields for session %s", len(fields), session_id)
        return fields

    async def ai_fill_and_submit(
        self,
        mapping: Dict[str, Any],
        session_id: str = "default",
        fields: List[Dict[str, Any]] = None,
    ) -> dict:
        page = self._get_page(session_id)
        fields = fields or await self.detect_fields(session_id)
        filled = 0
        total = len(fields)

        for idx, f in enumerate(fields):
            k = str(idx)
            if k not in mapping:
                continue
            action = mapping[k].get("action", "skip")
            val = mapping[k].get("value", "")
            if action == "skip":
                continue

            tag = f.get("tag", "")
            fid = f.get("id", "")
            name = f.get("name", "")
            sel = None
            if fid:
                sel = f"#{fid}"
            elif name:
                sel = f'[name="{name}"]'
            else:
                continue

            try:
                if action == "check":
                    await page.check(sel, timeout=2000)
                elif tag == "select":
                    await page.select_option(sel, str(val), timeout=2000)
                else:
                    await page.fill(sel, str(val), timeout=2000)
                filled += 1
            except Exception:
                if name:
                    fallback = f'[name="{name}"]'
                    if fallback != sel:
                        try:
                            if action == "check":
                                await page.check(fallback, timeout=2000)
                            elif tag == "select":
                                await page.select_option(fallback, str(val), timeout=2000)
                            else:
                                await page.fill(fallback, str(val), timeout=2000)
                            filled += 1
                            continue
                        except Exception:
                            pass
                logger.debug("Failed to fill %s", sel)

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
            "button:has-text('Go')",
        ]

        for sel in submit_selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
                if count > 0:
                    first = loc.first
                    await first.scroll_into_view_if_needed(timeout=2000)
                    await asyncio.sleep(0.2)
                    await first.click(timeout=3000)
                    submit_clicked = True
                    logger.info("Form submitted via: %s", sel)
                    break
            except Exception:
                continue

        if not submit_clicked:
            try:
                await page.evaluate("""() => { const f = document.querySelector('form'); if (f) f.submit(); }""")
                submit_clicked = True
                logger.info("Form submitted via: form.submit()")
            except Exception:
                pass

        status = "submitted" if submit_clicked else "submit_not_found"
        if submit_clicked:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

        os.makedirs(Config.SCREENSHOT_DIR, exist_ok=True)
        ss = os.path.join(Config.SCREENSHOT_DIR, f"{int(time.time())}_{status}.png")
        try:
            await page.screenshot(path=ss, full_page=True)
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            ss = ""

        result = {
            "status": status,
            "fields_filled": filled,
            "fields_total": total,
            "screenshot": ss,
            "final_url": page.url,
        }
        logger.info("Submit result: %s", result)
        return result

    async def navigate(self, url: str, session_id: str = "default"):
        page = self._get_page(session_id)
        await page.goto(url, wait_until="domcontentloaded", timeout=Config.BROWSER_TIMEOUT)
        await asyncio.sleep(0.5)

    async def solve_captcha(self, session_id: str = "default"):
        from app.captcha import _solve_captcha_inner
        page = self._get_page(session_id)
        return _solve_captcha_inner(page)

    async def click(self, x: int, y: int, session_id: str = "default"):
        page = self._get_page(session_id)
        await page.mouse.click(x, y)
        return True

    async def find_contact_url(self, base_url: str, session_id: str = "default") -> Optional[str]:
        result = await self.analyze_target(base_url, session_id=session_id)
        return result.get("contact_url")

    async def shutdown(self):
        for ctx in self._contexts:
            try:
                await ctx.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._initialized = False
        logger.info("Browser pool shut down")


# Global pool instance — lazy init
_pool: Optional[BrowserPool] = None


async def get_pool() -> BrowserPool:
    global _pool
    if _pool is None:
        _pool = BrowserPool()
        await _pool.init()
    return _pool


async def shutdown_pool():
    global _pool
    if _pool:
        await _pool.shutdown()
        _pool = None
