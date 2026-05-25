"""
High-volume pipeline — 1000-2000 submissions/day.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import current_app

from app import db
from app.ai_engine import (
    ai_generate_additional_message,
    ai_map_fields_smart,
    ai_summarize_target,
)
from app.browser import browser_mgr
from app.config import Config
from app.models import Lead, PipelineStat, Submission, Target
from app.search import search_firecrawl
from app.captcha import solve_captcha_if_present

logger = logging.getLogger(__name__)


class PipelineStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.started_at = None
        self.total_targets = 0
        self.processed = 0
        self.submitted = 0
        self.failed = 0
        self.skipped = 0
        self.captchas_solved = 0
        self.captchas_failed = 0

    def start(self, total):
        with self._lock:
            self.started_at = datetime.now(timezone.utc)
            self.total_targets = total
            self.processed = 0
            self.submitted = 0
            self.failed = 0
            self.skipped = 0
            self.captchas_solved = 0
            self.captchas_failed = 0

    def record_submit(self):
        with self._lock:
            self.processed += 1
            self.submitted += 1

    def record_fail(self):
        with self._lock:
            self.processed += 1
            self.failed += 1

    def record_skip(self):
        with self._lock:
            self.processed += 1
            self.skipped += 1

    def record_captcha(self, solved=True):
        with self._lock:
            if solved:
                self.captchas_solved += 1
            else:
                self.captchas_failed += 1

    def to_dict(self):
        with self._lock:
            elapsed = 0
            rate = 0
            if self.started_at:
                elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
                rate = (self.processed / elapsed * 3600) if elapsed > 0 else 0
            return {
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "total_targets": self.total_targets,
                "processed": self.processed,
                "submitted": self.submitted,
                "failed": self.failed,
                "skipped": self.skipped,
                "captchas_solved": self.captchas_solved,
                "captchas_failed": self.captchas_failed,
                "rate_per_hour": round(rate, 1),
                "elapsed_seconds": round(elapsed, 1),
            }


pipeline_stats = PipelineStats()


AUTO_QUERIES = [
    "hedge fund contact form",
    "family office contact us",
    "crypto fund manager contact",
    "venture capital firm contact form",
    "fund administrator request demo",
    "institutional digital asset platform contact",
    "asset management firm contact us",
    "prime brokerage crypto contact",
]


def run_high_volume_pipeline(batch_size=None, max_concurrent=None):
    try:
        app = current_app._get_current_object()
    except RuntimeError:
        from app import create_app
        app = create_app()

    batch_size = batch_size or Config.PIPELINE_BATCH_SIZE
    max_concurrent = max_concurrent or Config.PIPELINE_MAX_CONCURRENT

    with app.app_context():
        _discover_targets(app, limit=Config.PIPELINE_TARGET_DAILY)
        pending = Target.query.filter_by(status="pending").order_by(Target.id).all()
        if not pending:
            return

        pipeline_stats.start(len(pending))
        semaphore = threading.Semaphore(max_concurrent)
        threads = []

        def process_target(target_id):
            semaphore.acquire()
            try:
                _process_single_target(target_id, app)
            finally:
                semaphore.release()

        for target in pending:
            t = threading.Thread(target=process_target, args=(target.id,), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(0.3)

        for t in threads:
            t.join(timeout=300)

        _persist_stats(app)


def _discover_targets(app, limit=200):
    with app.app_context():
        added = 0
        for q in AUTO_QUERIES:
            results = search_firecrawl(q, limit=10)
            for r in results:
                exists = Target.query.filter_by(url=r["url"]).first()
                if exists:
                    continue
                t = Target(url=r["url"], title=r.get("title", ""), source_query=q)
                db.session.add(t)
                added += 1
                if added >= limit:
                    break
            if added >= limit:
                break
        db.session.commit()
        logger.info("Discovered %d new targets", added)


def _domain_key(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return url


def _process_single_target(target_id, app):
    with app.app_context():
        target = db.session.get(Target, target_id)
        if not target:
            return

        try:
            result = browser_mgr.analyze_target(target.url, session_id=f"target_{target_id}")
            target.has_form = result["has_form"]
            target.has_captcha = result["has_captcha"]
            target.page_title = result.get("page_title", "")
            target.emails_found = len(result.get("emails", []))
            target.status = "analyzed"
            db.session.commit()

            if not result["has_form"]:
                pipeline_stats.record_skip()
                target.status = "no_form"
                db.session.commit()
                return

            page_html = result.get("html", "")
            summary = ai_summarize_target(target.url, page_html[:4000], Config.COMPANY_DATA)
            add_msg = ai_generate_additional_message(Config.COMPANY_DATA, summary)

            company_data = dict(Config.COMPANY_DATA)
            company_data["message"] = add_msg

            browser_mgr.navigate(target.url, session_id=f"target_{target_id}")
            time.sleep(1)

            if target.has_captcha:
                solved = solve_captcha_if_present(f"target_{target_id}", browser_mgr)
                pipeline_stats.record_captcha(solved=bool(solved))

            fields = browser_mgr.detect_fields(session_id=f"target_{target_id}")
            mapping = ai_map_fields_smart(fields, company_data, summary)

            submit_result = browser_mgr.ai_fill_and_submit(
                mapping, session_id=f"target_{target_id}", keep_open=True
            )

            sub = Submission(
                target_id=target.id,
                status=submit_result["status"],
                fields_filled=submit_result["fields_filled"],
                fields_total=submit_result["fields_total"],
                screenshot_path=submit_result["screenshot"],
            )
            db.session.add(sub)

            if submit_result["status"] == "submitted":
                pipeline_stats.record_submit()
            else:
                pipeline_stats.record_fail()

            target.status = submit_result["status"]
            db.session.commit()

            for email_addr in result.get("emails", []):
                existing = Lead.query.filter_by(email=email_addr).first()
                if not existing:
                    lead = Lead(email=email_addr, source_url=target.url, status="new")
                    db.session.add(lead)
            db.session.commit()

        except Exception as e:
            pipeline_stats.record_fail()
            target.status = "error"
            db.session.commit()
            logger.warning("Target %s failed: %s", target.url, e)


def _persist_stats(app):
    with app.app_context():
        stats = pipeline_stats.to_dict()
        rec = PipelineStat.query.order_by(PipelineStat.id.desc()).first()
        if not rec:
            rec = PipelineStat()
            db.session.add(rec)
        rec.started_at = pipeline_stats.started_at
        rec.total_targets = stats["total_targets"]
        rec.processed = stats["processed"]
        rec.submitted = stats["submitted"]
        rec.failed = stats["failed"]
        rec.skipped = stats["skipped"]
        rec.captchas_solved = stats["captchas_solved"]
        rec.captchas_failed = stats["captchas_failed"]
        rec.rate_per_hour = stats["rate_per_hour"]
        db.session.commit()


def get_pipeline_status():
    return pipeline_stats.to_dict()
