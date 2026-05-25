import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
import fcntl

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from app import create_app, db
from app.ai_engine import _call_llm
from app.browser import browser_mgr
from app.config import Config
from app.models import Target
from app.search import search_firecrawl


LOG_PATH = os.path.join("logs", "orbital.log")
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger("orbital")

WHOISXML_API_KEY = os.getenv("WHOISXML_API_KEY", "")
WEBHOOK_TELEGRAM_URL = os.getenv("WEBHOOK_TELEGRAM_URL", "")
DISCOVER_INTERVAL_MINUTES = int(os.getenv("DISCOVER_INTERVAL_MINUTES", "10"))
PROCESS_INTERVAL_MINUTES = int(os.getenv("PROCESS_INTERVAL_MINUTES", "2"))


def _notify_critical(message):
    logger.error(message)
    if WEBHOOK_TELEGRAM_URL:
        try:
            requests.post(WEBHOOK_TELEGRAM_URL, json={"text": message}, timeout=10)
        except Exception:
            pass


def _init_domains_table():
    sql = text(
        """
        CREATE TABLE IF NOT EXISTS domains (
            id SERIAL PRIMARY KEY,
            domain TEXT UNIQUE NOT NULL,
            source TEXT,
            last_checked TIMESTAMP,
            status TEXT DEFAULT 'discovered',
            metadata JSONB DEFAULT '{}'::jsonb
        );
        """
    )
    db.session.execute(sql)
    db.session.commit()


def _extract_domains_from_html(html):
    domains = set()
    for match in re.findall(r'href=["\"]([^"\"]+)["\"]', html, re.IGNORECASE):
        if match.startswith("/"):
            continue
        try:
            parsed = urlparse(match)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                domains.add(parsed.netloc.lower())
        except Exception:
            continue
    return list(domains)


def _whois_check(domain):
    if not WHOISXML_API_KEY:
        return {"status": "unknown"}
    try:
        r = requests.get(
            "https://www.whoisxmlapi.com/whoisserver/WhoisService",
            params={
                "apiKey": WHOISXML_API_KEY,
                "domainName": domain,
                "outputFormat": "JSON",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _ollama_query(prompt):
    response = _call_llm(
        "You are an SEO discovery assistant. Generate one short search query.",
        prompt,
        temperature=0.7,
        max_tokens=64,
    )
    if not response:
        return "hedge fund contact form"
    return response.strip().strip('"')


def _ollama_relevance(domain, snippet):
    system = """Is this domain relevant for institutional finance outreach
(hedge funds, family offices, VC, crypto, fund administrators)?
Return JSON: {"relevant": "yes"|"no"|"unclear", "reason": "..."}.
"""
    user = f"Domain: {domain}\nSnippet: {snippet[:1200]}"
    response = _call_llm(system, user, temperature=0.2, max_tokens=120)
    if not response:
        return {"relevant": "unclear", "reason": "LLM unavailable"}
    try:
        return json.loads(response)
    except Exception:
        return {"relevant": "unclear", "reason": response[:200]}


def _discover_domains():
    prompt = "Generate a new search query to find institutional finance firms with contact forms."
    query = _ollama_query(prompt)
    results = search_firecrawl(query, limit=10)
    urls = [r.get("url") for r in results if r.get("url")]
    domains = set()

    for url in urls:
        try:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                domains.add(parsed.netloc.lower())
        except Exception:
            continue

    html_blobs = []
    for url in urls[:3]:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                html_blobs.append(r.text)
        except Exception:
            continue

    for html in html_blobs:
        domains.update(_extract_domains_from_html(html))

    added = 0
    for domain in domains:
        exists = db.session.execute(
            text("SELECT 1 FROM domains WHERE domain = :domain"),
            {"domain": domain},
        ).fetchone()
        if exists:
            continue

        whois_info = _whois_check(domain)
        db.session.execute(
            text(
                "INSERT INTO domains (domain, source, status, metadata) VALUES (:domain, :source, :status, :metadata)"
            ),
            {
                "domain": domain,
                "source": query,
                "status": "discovered",
                "metadata": json.dumps({"whois": whois_info}),
            },
        )
        added += 1

    db.session.commit()
    logger.info("Discovered %d new domains from query '%s' (results=%d)", added, query, len(results))
    if added > 0:
        _process_domain()


def _process_domain():
    row = db.session.execute(
        text("SELECT id, domain FROM domains WHERE status = 'discovered' ORDER BY id ASC LIMIT 1")
    ).fetchone()
    if not row:
        return

    domain_id, domain = row
    url = f"https://{domain}"
    session_id = "live"
    max_attempts = 2
    
    for attempt in range(max_attempts):
        try:
            logger.info(f"Processing domain {domain} (attempt {attempt + 1}/{max_attempts})")
            analysis = browser_mgr.analyze_target(url, session_id=session_id)
            html = analysis.get("html", "")
            
            if not html:
                logger.warning(f"No HTML content for {domain}, will retry")
                if attempt < max_attempts - 1:
                    time.sleep(2)
                    continue
                raise Exception("No HTML content")
            
            relevance = _ollama_relevance(domain, html[:2000])
            if relevance.get("relevant") != "yes":
                db.session.execute(
                    text("UPDATE domains SET status='irrelevant', last_checked=:ts, metadata=:meta WHERE id=:id"),
                    {
                        "ts": datetime.now(timezone.utc),
                        "meta": json.dumps({"relevance": relevance}),
                        "id": domain_id,
                    },
                )
                db.session.commit()
                logger.info(f"Domain {domain} marked irrelevant")
                return

            # If no form found on home page, try to locate contact/demo page
            if not analysis.get("has_form"):
                contact_url = browser_mgr.find_contact_url(url, session_id=session_id)
                if contact_url:
                    logger.info("Found contact page for %s -> %s", url, contact_url)
                    analysis = browser_mgr.analyze_target(contact_url, session_id=session_id)
                    html = analysis.get("html", "")
                    url = contact_url

            # Add to targets table for pipeline tracking
            existing = Target.query.filter_by(url=url).first()
            if not existing:
                db.session.add(Target(url=url, status="pending"))
                db.session.commit()

            screenshot_path = os.path.join("static", "screenshots", f"{domain}_check.png")
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            try:
                img = browser_mgr.screenshot(session_id=session_id)
                with open(screenshot_path, "wb") as f:
                    f.write(img)
            except Exception as e:
                logger.warning(f"Screenshot failed for {domain}: {e}")

            has_form = analysis.get("has_form", False)
            status = "no_form"
            if has_form:
                from app.ai_engine import ai_generate_additional_message, ai_map_fields_smart, ai_summarize_target

                summary = ai_summarize_target(url, html[:4000], Config.COMPANY_DATA)
                add_msg = ai_generate_additional_message(Config.COMPANY_DATA, summary)
                company_data = dict(Config.COMPANY_DATA)
                company_data["message"] = add_msg

                fields = browser_mgr.detect_fields(session_id=session_id)
                if not fields:
                    logger.warning(f"No fields detected for {domain}")
                    status = "no_fields"
                else:
                    try:
                        mapping = ai_map_fields_smart(fields, company_data, summary)
                        submit_result = browser_mgr.ai_fill_and_submit(mapping, session_id=session_id)
                        status = submit_result.get("status", "submit_not_found")
                        logger.info(f"Domain {domain} -> {status} ({submit_result.get('fields_filled')}/{submit_result.get('fields_total')} fields)")
                    except Exception as e:
                        logger.error(f"Fill/submit failed for {domain}: {e}")
                        status = "fill_error"

            db.session.execute(
                text("UPDATE domains SET status=:status, last_checked=:ts WHERE id=:id"),
                {"status": status, "ts": datetime.now(timezone.utc), "id": domain_id},
            )
            db.session.commit()
            logger.info(f"Domain {domain} completed with status: {status}")
            return  # Success, exit retry loop

        except Exception as e:
            logger.error(f"Domain processing attempt {attempt + 1} failed for {domain}: {e}")
            if attempt < max_attempts - 1:
                logger.info(f"Retrying domain {domain}...")
                time.sleep(2)
            else:
                _notify_critical(f"Domain processing failed for {domain} after {max_attempts} attempts: {e}")
                db.session.execute(
                    text("UPDATE domains SET status='error', last_checked=:ts WHERE id=:id"),
                    {"ts": datetime.now(timezone.utc), "id": domain_id},
                )
                db.session.commit()


def _start_scheduler(app):
    lock_path = "/tmp/orbital_scheduler.lock"
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("Scheduler already running in another worker")
        return None

    scheduler = BackgroundScheduler()

    def _discover_job():
        with app.app_context():
            _discover_domains()

    def _process_job():
        with app.app_context():
            _process_domain()

    scheduler.add_job(
        _discover_job,
        "interval",
        minutes=DISCOVER_INTERVAL_MINUTES,
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.add_job(
        _process_job,
        "interval",
        minutes=PROCESS_INTERVAL_MINUTES,
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    return scheduler


app = create_app()

with app.app_context():
    _init_domains_table()
    _start_scheduler(app)
    if os.getenv("FIRECRAWL_API_KEY"):
        logger.info("Firecrawl API key detected")
    else:
        logger.warning("No Firecrawl API key detected; discovery may fail")
    logger.info("AI provider=%s model=%s host=%s", os.getenv("AI_PROVIDER"), os.getenv("AI_MODEL"), os.getenv("OLLAMA_HOST"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
