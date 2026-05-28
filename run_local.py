#!/usr/bin/env python3
"""
Local runner — full autonomous pipeline.
Runs directly on the host: VPN, DNS, Ollama cloud all work.
SQLite for zero-dependency startup.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# ── Load .env manually ──────────────────────────────────────
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                os.environ.setdefault(key, val)

load_env()

# Force SQLite + headless browser (screenshots shown in dashboard)
os.environ["DATABASE_URL"] = "sqlite:///orbital_local.db"
os.environ.setdefault("BROWSER_HEADLESS", "true")

LOG_PATH = os.path.join("logs", "orbital.log")
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger("orbital")

from app import create_app, db
from app.models import Lead, Submission, Target, PipelineStat
from app.browser import browser_mgr
from app.config import Config
from app.ai_engine import ai_map_fields_smart
from app.search import search_firecrawl

app = create_app()

# ── Seed domains if empty ──────────────────────────────────
SEED_DOMAINS = [
    "https://www.blueskycapitalmanagement.com/invest/",
    "https://www.rentec.com/Contact.action",
    "https://www.bridgeassociates.com/contact/",
    "https://www.citadel.com/contact/",
    "https://www.two-sigma.com/contact/",
    "https://www.deshaw.com/contact/",
    "https://www.point72.com/contact/",
    "https://www.bridgewater.com/contact/",
    "https://www.aqr.com/contact/",
    "https://www.millenniumllc.com/contact/",
    "https://www.man.com/contact",
    "https://www.winton.com/contact",
    "https://www.capula.com/contact",
    "https://www.marshallwace.com/contact",
    "https://www.balyasny.com/contact",
    "https://www.ellipse-vc.com/contact/",
    "https://www.polychain.capital/contact",
    "https://www.panteracapital.com/contact",
    "https://www.a16z.com/contact/",
    "https://www.sequoiacap.com/contact",
]

def _seed_targets():
    existing = Target.query.count()
    if existing > 0:
        logger.info("DB has %d targets, skipping seed", existing)
        return
    logger.info("Seeding %d targets...", len(SEED_DOMAINS))
    for url in SEED_DOMAINS:
        t = Target(url=url, status="pending", source_query="seed")
        db.session.add(t)
    db.session.commit()
    logger.info("Seeded %d targets", len(SEED_DOMAINS))

# ── Discover new targets via search ────────────────────────
DISCOVER_QUERIES = [
    "hedge fund contact form",
    "family office contact us",
    "venture capital contact form",
    "crypto fund contact",
    "asset management contact us",
    "fund administrator demo request",
    "institutional finance contact form",
    "digital asset platform contact",
    "private equity firm contact",
    "commodity trading advisor contact",
]

def discover_targets(limit=50):
    added = 0
    for q in DISCOVER_QUERIES:
        results = search_firecrawl(q, limit=10)
        for r in results:
            url = r.get("url", "")
            if not url:
                continue
            exists = Target.query.filter_by(url=url).first()
            if exists:
                continue
            t = Target(url=url, title=r.get("title", ""), status="pending", source_query=q)
            db.session.add(t)
            added += 1
            if added >= limit:
                break
        if added >= limit:
            break
    db.session.commit()
    logger.info("Discovered %d new targets", added)
    return added

# ── Process single target ──────────────────────────────────
def process_target(target):
    session_id = f"target_{target.id}"
    disable_llm = os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true"

    try:
        logger.info("Processing: %s", target.url)

        # 1) Analyze
        result = browser_mgr.analyze_target(target.url, session_id=session_id)
        target.has_form = result.get("has_form", False)
        target.has_captcha = result.get("has_captcha", False)
        target.page_title = result.get("page_title", "")
        target.emails_found = len(result.get("emails", []))
        target.status = "analyzed"
        db.session.commit()

        if not result.get("has_form"):
            # Try contact page
            contact_url = browser_mgr.find_contact_url(target.url, session_id=session_id)
            if contact_url:
                logger.info("Found contact page: %s -> %s", target.url, contact_url)
                result = browser_mgr.analyze_target(contact_url, session_id=session_id)
                target.url = contact_url
                target.has_form = result.get("has_form", False)
                target.has_captcha = result.get("has_captcha", False)
                db.session.commit()

            if not result.get("has_form"):
                target.status = "no_form"
                db.session.commit()
                logger.info("No form found: %s", target.url)
                return {"status": "no_form"}

        # 2) Map fields and fill
        company_data = dict(Config.COMPANY_DATA)
        summary = {"summary": "", "angle": "", "suggested_message": company_data["message"]}

        if not disable_llm:
            try:
                from app.ai_engine import ai_summarize_target, ai_generate_additional_message
                summary = ai_summarize_target(target.url, result.get("html", "")[:4000], company_data)
                add_msg = ai_generate_additional_message(company_data, summary)
                company_data["message"] = add_msg
            except Exception as e:
                logger.warning("LLM failed, using defaults: %s", e)

        fields = browser_mgr.detect_fields(session_id=session_id)
        logger.info("Detected %d fields for %s", len(fields), target.url)

        if not fields:
            target.status = "no_fields"
            db.session.commit()
            return {"status": "no_fields"}

        mapping = ai_map_fields_smart(fields, company_data, summary)
        submit_result = browser_mgr.ai_fill_and_submit(mapping, session_id=session_id, fields=fields)

        # Build field mapping log: what was written in each field
        mapping_log = []
        for idx, f in enumerate(fields):
            k = str(idx)
            m = mapping.get(k, {})
            if m.get("action") != "skip":
                mapping_log.append({
                    "field": f.get("name") or f.get("id") or f"field_{idx}",
                    "type": f.get("type") or f.get("tag", ""),
                    "label": f.get("label_text", ""),
                    "value_written": m.get("value", ""),
                    "action": m.get("action", ""),
                })

        # 3) Save result
        sub = Submission(
            target_id=target.id,
            status=submit_result.get("status"),
            fields_filled=submit_result.get("fields_filled", 0),
            fields_total=submit_result.get("fields_total", 0),
            screenshot_path=submit_result.get("screenshot", ""),
            field_mapping=json.dumps(mapping_log),
            final_url=submit_result.get("final_url", ""),
        )
        db.session.add(sub)
        target.status = submit_result.get("status", "unknown")
        db.session.commit()

        # 4) Extract leads
        for email_addr in result.get("emails", []):
            existing = Lead.query.filter_by(email=email_addr).first()
            if not existing:
                lead = Lead(email=email_addr, source_url=target.url, status="new")
                db.session.add(lead)
        db.session.commit()

        logger.info("Target %s -> %s (%d/%d fields)",
                    target.url, submit_result.get("status"),
                    submit_result.get("fields_filled", 0),
                    submit_result.get("fields_total", 0))
        return submit_result

    except Exception as e:
        logger.error("Target %s failed: %s", target.url, e)
        db.session.rollback()
        try:
            target.status = "error"
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {"status": "error", "error": str(e)}

# ── Main processing loop ────────────────────────────────────
def run_pipeline_loop():
    """Process all pending targets one by one."""
    with app.app_context():
        pending = Target.query.filter_by(status="pending").order_by(Target.id).all()
        if not pending:
            logger.info("No pending targets, discovering...")
            discover_targets()
            pending = Target.query.filter_by(status="pending").order_by(Target.id).all()

        logger.info("Processing %d pending targets", len(pending))
        for target in pending:
            try:
                process_target(target)
            except Exception as e:
                logger.error("Target %d crashed: %s", target.id, e)
                db.session.rollback()
            time.sleep(2)  # Rate limiting

        logger.info("Pipeline batch complete")

# ── Scheduler ───────────────────────────────────────────────
def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()

    def _discover():
        with app.app_context():
            try:
                discover_targets()
            except Exception as e:
                logger.error("Discovery failed: %s", e)

    def _process():
        with app.app_context():
            try:
                run_pipeline_loop()
            except Exception as e:
                logger.error("Pipeline failed: %s", e)

    # Discover every 10 minutes
    scheduler.add_job(_discover, "interval", minutes=10, next_run_time=datetime.now(timezone.utc))
    # Process every 2 minutes
    scheduler.add_job(_process, "interval", minutes=2, next_run_time=datetime.now(timezone.utc))

    scheduler.start()
    logger.info("Scheduler started: discover=10min, process=2min")
    return scheduler


# ── Init ────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    # Fix SQLite: WAL mode prevents lock errors
    db.session.execute(db.text("PRAGMA journal_mode=WAL"))
    db.session.execute(db.text("PRAGMA busy_timeout=30000"))
    db.session.commit()
    # Ensure new columns exist (SQLite migration)
    try:
        db.session.execute(db.text("ALTER TABLE submission ADD COLUMN field_mapping TEXT"))
    except Exception:
        pass
    try:
        db.session.execute(db.text("ALTER TABLE submission ADD COLUMN session_log TEXT"))
    except Exception:
        pass
    try:
        db.session.execute(db.text("ALTER TABLE submission ADD COLUMN final_url VARCHAR(2048)"))
    except Exception:
        pass
    db.session.commit()
    _seed_targets()
    logger.info("Database initialized (SQLite + WAL)")
    logger.info("AI provider=%s model=%s host=%s DISABLE_LLM=%s",
                os.getenv("AI_PROVIDER"), os.getenv("AI_MODEL"),
                os.getenv("OLLAMA_HOST"), os.getenv("DISABLE_LLM_FORMS"))

scheduler = start_scheduler()

if __name__ == "__main__":
    logger.info("Starting Orbital on http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
