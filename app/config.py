import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "orbital-industries-2026")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///orbital.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Orbital Industries Identity ──────────────────────────────
    # Every form fill, email, and outreach references this identity.
    COMPANY_DATA = {
        "first_name": os.getenv("COMPANY_FIRST_NAME", "Stephen"),
        "last_name": os.getenv("COMPANY_LAST_NAME", "McCool"),
        "full_name": os.getenv("COMPANY_FULL_NAME", "Stephen Andrew McCool"),
        "email": os.getenv("COMPANY_EMAIL", "contact@orbitaltech.pro"),
        "phone": os.getenv("COMPANY_PHONE", "+441304700329"),
        "company": os.getenv("COMPANY_NAME", "Orbital Industries Limited"),
        "company_url": os.getenv("COMPANY_URL", "https://orbitaltech.pro"),
        "job_title": os.getenv("COMPANY_JOB_TITLE", "Head of Business Development"),
        "industry": os.getenv("COMPANY_INDUSTRY", "Institutional Finance / Digital Assets / Strategic Partnerships"),
        "country": os.getenv("COMPANY_COUNTRY", "United Kingdom"),
        "city": os.getenv("COMPANY_CITY", "Tamworth, Staffordshire"),
        "employees": os.getenv("COMPANY_EMPLOYEES", "11-50"),
        "address": os.getenv("COMPANY_ADDRESS", "Unit 5, Ariane, Lichfield Road Industrial Estate, Tamworth, Staffordshire, England, B79 7XF"),
        "registration": os.getenv("COMPANY_REGISTRATION", ""),
        "bio": os.getenv(
            "COMPANY_BIO",
            "Orbital Industries Limited is a UK-based firm focused on institutional partnerships, "
            "hedge fund relationships, family office advisory, venture capital opportunities, and "
            "digital asset strategy. We partner with institutional allocators, fund managers, and "
            "crypto/fintech operators globally to originate deals and strategic partnerships.",
        ),
        "message": os.getenv(
            "COMPANY_MESSAGE",
            "Hi, I'm Stephen from Orbital Industries in the UK. We partner with hedge funds, family offices, "
            "crypto and venture firms on strategic opportunities and institutional relationships. I came across "
            "your platform and think there may be strong alignment. Would you be open to a brief call to explore "
            "potential collaboration?",
        ),
    }

    # Admin
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "orbital2024")

    # Paths
    SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "static/screenshots")
    BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")
    BROWSER_DATA_DIR = os.getenv("BROWSER_DATA_DIR", "browser_data")

    # Browser
    BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "45000"))
    BROWSER_START_URL = os.getenv("BROWSER_START_URL", "https://www.google.com")
    BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
    BROWSER_SCREENCAST_QUALITY = int(os.getenv("BROWSER_SCREENCAST_QUALITY", "60"))

    # AI — Ollama primary, OpenAI fallback
    AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama")
    AI_API_KEY = os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    AI_MODEL = os.getenv("AI_MODEL", "llama3.1:8b")
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    # CAPTCHA
    TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")

    # SMTP
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASS = os.getenv("SMTP_PASS", "")
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Orbital Industries")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "contact@orbitaltech.pro")

    # Proxy / Tor
    BROWSER_PROXY = os.getenv("BROWSER_PROXY", "")
    TOR_PROXY = os.getenv("TOR_PROXY", "socks5://127.0.0.1:9050")
    TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
    TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "orbitaltor")
    TOR_ENABLED = os.getenv("TOR_ENABLED", "false").lower() == "true"

    # Redis / RQ
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    QUEUE_NAME = os.getenv("QUEUE_NAME", "orbital")

    # Pipeline
    PIPELINE_BATCH_SIZE = int(os.getenv("PIPELINE_BATCH_SIZE", "20"))
    PIPELINE_MAX_CONCURRENT = int(os.getenv("PIPELINE_MAX_CONCURRENT", "5"))
    PIPELINE_TARGET_DAILY = int(os.getenv("PIPELINE_TARGET_DAILY", "1500"))
    DOMAIN_COOLDOWN_SECONDS = int(os.getenv("DOMAIN_COOLDOWN_SECONDS", "20"))

    # Firecrawl (optional, for target discovery)
    FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
