"""Celery background tasks for Orbital pipeline."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from celery.signals import worker_process_init, worker_process_shutdown

from app import db
from app.ai_engine import ai_generate_additional_message, ai_map_fields_smart, ai_summarize_target
from app.browser_selenium import BrowserPool
from app.celery_app import celery_app
from app.config import Config
from app.models import Lead, PipelineStat, Submission, Target
from app.search import search_primary

logger = logging.getLogger(__name__)

# Per-process browser pool (one context per worker process)
_worker_pool: BrowserPool = None


@worker_process_init.connect
def init_worker(**kwargs):
    global _worker_pool
    try:
        pool_size = int(os.getenv("WORKER_BROWSER_POOL_SIZE", "1"))
        _worker_pool = BrowserPool(pool_size=pool_size)
        asyncio.run(_worker_pool.init())
        logger.info("Worker browser pool initialized (size=%d)", pool_size)
    except Exception as e:
        logger.error("Failed to init worker browser pool: %s", e)
        _worker_pool = None


@worker_process_shutdown.connect
def shutdown_worker(**kwargs):
    global _worker_pool
    if _worker_pool:
        try:
            asyncio.run(_worker_pool.shutdown())
            logger.info("Worker browser pool shut down")
        except Exception as e:
            logger.error("Error shutting down worker pool: %s", e)
        _worker_pool = None


def _domain_key(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return url


async def _process_single_target_async(target_id: int, pool: BrowserPool):
    from app import create_app
    app = create_app()
    with app.app_context():
        target = db.session.get(Target, target_id)
        if not target:
            logger.warning("Target %s not found", target_id)
            return

        try:
            result = await pool.analyze_target(target.url, session_id=f"target_{target_id}")
            target.has_form = result["has_form"]
            target.has_captcha = result["has_captcha"]
            target.page_title = result.get("page_title", "")
            target.emails_found = len(result.get("emails", []))
            target.status = "analyzed"
            db.session.commit()

            if not result["has_form"]:
                target.status = "no_form"
                db.session.commit()
                logger.info("No form on %s", target.url)
                return

            page_html = result.get("html", "")
            disable_llm = os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true"
            if disable_llm:
                summary = {"summary": "", "angle": "", "suggested_message": Config.COMPANY_DATA["message"]}
                add_msg = Config.COMPANY_DATA["message"]
            else:
                try:
                    summary = ai_summarize_target(target.url, page_html[:4000], Config.COMPANY_DATA)
                    add_msg = ai_generate_additional_message(Config.COMPANY_DATA, summary)
                except Exception as e:
                    logger.warning("LLM failed for %s: %s, using defaults", target.url, e)
                    summary = {"summary": "", "angle": "", "suggested_message": Config.COMPANY_DATA["message"]}
                    add_msg = Config.COMPANY_DATA["message"]

            company_data = dict(Config.COMPANY_DATA)
            company_data["message"] = add_msg

            await pool.navigate(target.url, session_id=f"target_{target_id}")
            await asyncio.sleep(1)

            if target.has_captcha:
                from app.captcha_async import solve_captcha_if_present
                solved = await solve_captcha_if_present(f"target_{target_id}", pool)
                logger.info("CAPTCHA solved=%s for %s", solved, target.url)

            fields = await pool.detect_fields(session_id=f"target_{target_id}")
            mapping = ai_map_fields_smart(fields, company_data, summary)

            submit_result = await pool.ai_fill_and_submit(
                mapping, session_id=f"target_{target_id}", fields=fields
            )

            sub = Submission(
                target_id=target.id,
                status=submit_result["status"],
                fields_filled=submit_result["fields_filled"],
                fields_total=submit_result["fields_total"],
                screenshot_path=submit_result["screenshot"],
            )
            db.session.add(sub)
            target.status = submit_result["status"]
            db.session.commit()

            for email_addr in result.get("emails", []):
                existing = Lead.query.filter_by(email=email_addr).first()
                if not existing:
                    lead = Lead(email=email_addr, source_url=target.url, status="new")
                    db.session.add(lead)
            db.session.commit()

            logger.info("Target %s processed: %s", target.url, submit_result["status"])

        except Exception as e:
            target.status = "error"
            db.session.commit()
            logger.warning("Target %s failed: %s", target.url, e)
            raise


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def process_target_task(self, target_id: int):
    global _worker_pool
    if _worker_pool is None:
        raise RuntimeError("Browser pool not initialized in worker")
    try:
        asyncio.run(_process_single_target_async(target_id, _worker_pool))
    except Exception as exc:
        logger.error("Task failed for target %s: %s", target_id, exc)
        raise self.retry(exc=exc)


# Excluded mega-brands (always filtered out post-discovery)
EXCLUDED_MEGA_BRANDS = {
    "blackrock", "vanguard", "fidelity", "state street", "goldman sachs",
    "jp morgan", "morgan stanley", "bridgewater", "citadel", "two sigma",
    "renaissance", "point72", "millennium", "baupost", "elliott",
    "soros", "d.e. shaw", "marshall wace", "man group", "tci", "third point",
    "brevan howard", "paulson", "pershing square", "icahn", "ackman",
    "bain capital", "kkr", "carlyle", "blackstone", "apollo", "warburg",
    "tpg", "softbank", "sequoia", "benchmark", "accel", "andreessen",
    "bessemer", "index ventures", "insight partners", "lightspeed",
    " Coatue", "tiger global", "d1", "viking", "glenview", "lone pine",
    "valeant", "fairfax", "loeb", "greenlight", "tudor", "moore", "aqr",
    "canyon", "oaktree", "oak hill", "silver lake", "thoma bravo",
    "veritas", "general atlantic", "berkshire hathaway", "berkshire",
    "nextera", "invesco", "pimco", "franklin templeton", "capital group",
    "prudential", "metlife", "allianz", "axa", "ubs", "credit suisse",
    "deutsche bank", "barclays", "hsbc", "bnpp", "societe generale",
    "nomura", "mizuho", "daiwa", "macquarie", "lazard", "evercore",
    "perella", "jefferies", "rbc", "td", "scotiabank", "bmo",
    "wells fargo", "bank of america", "citi", "citigroup",
}


def _is_mega_brand(url: str, title: str = "") -> bool:
    """Return True if URL/title contains a mega-brand we want to skip."""
    combined = (url + " " + title).lower()
    for brand in EXCLUDED_MEGA_BRANDS:
        if brand in combined:
            return True
    return False


AUTO_QUERIES = [
    # ── Original queries that actually return results ─────────────────
    "hedge fund contact form",
    "family office contact us",
    "crypto fund manager contact",
    "venture capital firm contact form",
    "fund administrator request demo",
    "institutional digital asset platform contact",
    "asset management firm contact us",
    "prime brokerage crypto contact",
    # Boutique-focused variants
    "boutique hedge fund contact",
    "emerging manager hedge fund contact form",
    "mid-size family office contact",
    "emerging venture capital firm contact",
    "seed stage crypto fund contact",
    "boutique investment manager contact",
    "alternative investment boutique contact",
    "boutique digital asset manager contact",
]


@celery_app.task
def discover_targets_task(limit: int = None):
    from app import create_app
    app = create_app()
    limit = limit or Config.PIPELINE_TARGET_DAILY
    with app.app_context():
        added = 0
        skipped_big = 0
        for q in AUTO_QUERIES:
            results = search_primary(q, limit=10)
            for r in results:
                url = r["url"]
                title = r.get("title", "")
                if _is_mega_brand(url, title):
                    skipped_big += 1
                    logger.info("Skipped mega-brand target: %s (%s)", url, title)
                    continue
                exists = Target.query.filter_by(url=url).first()
                if exists:
                    continue
                t = Target(url=url, title=title, source_query=q)
                db.session.add(t)
                added += 1
                if added >= limit:
                    break
            if added >= limit:
                break
        db.session.commit()
        logger.info("Discovered %d new targets (skipped %d mega-brands)", added, skipped_big)
        return {"added": added, "skipped_mega": skipped_big}


@celery_app.task
def run_pipeline_task(batch_size: int = None, max_concurrent: int = None):
    from app import create_app
    app = create_app()
    batch_size = batch_size or Config.PIPELINE_BATCH_SIZE
    max_concurrent = max_concurrent or Config.PIPELINE_MAX_CONCURRENT

    with app.app_context():
        # Ensure targets exist
        pending = Target.query.filter_by(status="pending").order_by(Target.id).all()
        if not pending:
            discover_targets_task.delay(limit=Config.PIPELINE_TARGET_DAILY)
            logger.info("No pending targets; discovery queued")
            return {"status": "discovery_queued"}

        # Launch target tasks in groups
        from celery import group
        job = group(
            process_target_task.s(t.id) for t in pending[:batch_size]
        )
        result = job.apply_async()
        logger.info("Pipeline batch launched: %d targets", min(batch_size, len(pending)))
        return {
            "status": "started",
            "batch_size": min(batch_size, len(pending)),
            "task_id": result.id,
        }
