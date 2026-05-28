"""Selenium configuration and monitoring utilities."""
import os
from dataclasses import dataclass


@dataclass
class SeleniumConfig:
    """Selenium browser pool configuration."""
    
    # Pool settings
    POOL_SIZE: int = int(os.getenv("SELENIUM_POOL_SIZE", "3"))
    MAX_SESSIONS: int = int(os.getenv("SELENIUM_MAX_SESSIONS", "10"))
    
    # Timeout settings (seconds)
    PAGE_LOAD_TIMEOUT: int = int(os.getenv("SELENIUM_PAGE_LOAD_TIMEOUT", "45"))
    SCRIPT_TIMEOUT: int = int(os.getenv("SELENIUM_SCRIPT_TIMEOUT", "45"))
    WAIT_TIMEOUT: int = int(os.getenv("SELENIUM_WAIT_TIMEOUT", "20"))
    
    # Retry settings
    RETRY_ATTEMPTS: int = int(os.getenv("SELENIUM_RETRY_ATTEMPTS", "3"))
    RETRY_DELAY: float = float(os.getenv("SELENIUM_RETRY_DELAY", "1.0"))
    RETRY_BACKOFF: float = float(os.getenv("SELENIUM_RETRY_BACKOFF", "2.0"))
    
    # Health check settings
    HEALTH_CHECK_INTERVAL: int = int(os.getenv("SELENIUM_HEALTH_CHECK_INTERVAL", "60"))
    HEALTH_CHECK_TIMEOUT: int = int(os.getenv("SELENIUM_HEALTH_CHECK_TIMEOUT", "10"))
    
    # Driver settings
    HEADLESS: bool = os.getenv("SELENIUM_HEADLESS", "true").lower() == "true"
    WINDOW_WIDTH: int = int(os.getenv("SELENIUM_WINDOW_WIDTH", "1366"))
    WINDOW_HEIGHT: int = int(os.getenv("SELENIUM_WINDOW_HEIGHT", "768"))
    
    # Chrome/Chromium settings
    CHROME_BIN: str = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    CHROMEDRIVER_PATH: str = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    
    # Screenshot settings
    SCREENSHOT_DIR: str = os.getenv("SELENIUM_SCREENSHOT_DIR", "static/screenshots")
    SCREENSHOT_ON_ERROR: bool = os.getenv("SELENIUM_SCREENSHOT_ON_ERROR", "true").lower() == "true"
    
    # Performance settings
    DISABLE_IMAGES: bool = os.getenv("SELENIUM_DISABLE_IMAGES", "false").lower() == "true"
    DISABLE_CSS: bool = os.getenv("SELENIUM_DISABLE_CSS", "false").lower() == "true"
    
    # Connection pool settings
    CONNECTION_TIMEOUT: int = int(os.getenv("SELENIUM_CONNECTION_TIMEOUT", "30"))
    KEEP_ALIVE: bool = os.getenv("SELENIUM_KEEP_ALIVE", "true").lower() == "true"
    
    @classmethod
    def to_dict(cls):
        """Export config as dictionary."""
        return {
            "pool_size": cls.POOL_SIZE,
            "page_load_timeout": cls.PAGE_LOAD_TIMEOUT,
            "retry_attempts": cls.RETRY_ATTEMPTS,
            "health_check_interval": cls.HEALTH_CHECK_INTERVAL,
            "headless": cls.HEADLESS,
        }
