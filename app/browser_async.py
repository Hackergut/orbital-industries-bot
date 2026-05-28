"""
Async Browser Pool — re-export from browser_selenium.
Playwright version removed; Selenium is now the default.
"""
from app.browser_selenium import BrowserPool, get_pool, shutdown_pool
