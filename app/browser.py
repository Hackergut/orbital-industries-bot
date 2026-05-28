"""
Persistent Browser Manager — Selenium with worker thread.
Wraps browser_selenium.BrowserPool in a background event loop
so Flask routes and sync pipeline code keep working.
"""
import asyncio
import logging
import os
import queue
import threading
from concurrent.futures import Future
from urllib.parse import urljoin

from app.browser_selenium import BrowserPool
from app.config import Config

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._pool = None
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
            target=self._worker_loop, daemon=True, name="selenium-worker"
        )
        self._worker_thread.start()
        self._worker_started.wait(timeout=10)

    def _worker_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._pool = BrowserPool(pool_size=1)
        loop.run_until_complete(self._pool.init())
        self._worker_started.set()
        while not self._shutdown:
            try:
                job = self._job_queue.get(timeout=1)
                if job is None:
                    continue
                future, func, args, kwargs = job
                try:
                    result = loop.run_until_complete(func(*args, **kwargs))
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

    # ── public sync API ───────────────────────────────────────────────

    def navigate(self, url, session_id="default", timeout=None):
        self._last_session_id = session_id
        return self._run_on_worker(self._navigate_async, url, session_id)

    async def _navigate_async(self, url, session_id):
        if session_id not in self._pool._session_map:
            await self._pool._checkout(session_id)
        await self._pool.navigate(url, session_id)
        return url

    def get_url(self, session_id="default"):
        return self._run_on_worker(
            self._pool.evaluate, "return window.location.href;", session_id
        )

    def screenshot(self, session_id="default", quality=70):
        path = os.path.join(Config.SCREENSHOT_DIR, f"{int(time.time())}.png")
        os.makedirs(Config.SCREENSHOT_DIR, exist_ok=True)
        self._run_on_worker(self._pool.screenshot, path, session_id, True)
        return path

    def get_last_session_id(self):
        return self._last_session_id

    def click(self, x, y, session_id="default"):
        script = f"document.elementFromPoint({x}, {y}).click();"
        return self._run_on_worker(self._pool.evaluate, script, session_id)

    def title(self, session_id="default"):
        return self._run_on_worker(self._pool.title, session_id)

    def content(self, session_id="default"):
        return self._run_on_worker(self._pool.content, session_id)

    def evaluate(self, script, session_id="default"):
        return self._run_on_worker(self._pool.evaluate, script, session_id)

    def query_selector(self, selector, session_id="default"):
        return self._run_on_worker(self._pool.query_selector, selector, session_id)

    def detect_fields(self, session_id="default"):
        return self._run_on_worker(self._pool.detect_fields, session_id)

    def ai_fill_and_submit(self, mapping, session_id="default", keep_open=True, fields=None):
        return self._run_on_worker(self._pool.ai_fill_and_submit, mapping, session_id, fields)

    def analyze_target(self, url, session_id="default", timeout=None):
        self._last_session_id = session_id
        return self._run_on_worker(self._pool.analyze_target, url, session_id)

    def find_contact_url(self, base_url, session_id="default"):
        return self._run_on_worker(self._pool.find_contact_url, base_url, session_id)

    def solve_captcha(self, session_id="default"):
        return self._run_on_worker(self._pool.solve_captcha, session_id)

    def shutdown(self):
        self._shutdown = True
        if self._pool:
            self._run_on_worker(self._pool.shutdown)


browser_mgr = BrowserManager()
