"""Async pipeline — background task using BrowserPool."""
import asyncio
import concurrent.futures
import json
import logging
import os
from datetime import datetime, timezone

from app import create_app, db
from app.ai_engine import ai_generate_additional_message, ai_map_fields_smart, ai_summarize_target
from app.browser_selenium import get_pool
from app.config import Config
from app.models import Lead, PipelineStat, Submission, Target
from app.pipeline_stats import pipeline_stats
from app.form_proof import build_proof_for_target
from app.search import search_primary
from app.constants import EXCLUDED_MEGA_BRANDS, AUTO_QUERIES

logger = logging.getLogger(__name__)


def _is_mega_brand(url: str, title: str = "") -> bool:
    combined = (url + " " + title).lower()
    for brand in EXCLUDED_MEGA_BRANDS:
        if brand in combined:
            return True
    return False



# Shared app instance — avoid re-creating Flask app on every batch
_pipeline_app = None

def _get_app():
    global _pipeline_app
    if _pipeline_app is None:
        _pipeline_app = create_app()
    return _pipeline_app


async def _discover_targets(limit: int = 200):
    """Discover new targets via parallel search queries."""
    app = _get_app()
    added = 0
    seen_urls = set()
    
    def _sync_search_one(q: str):
        """Run a single search query and return results."""
        try:
            return search_primary(q, limit=15)
        except Exception as e:
            logger.warning("Discovery query '%s' failed: %s", q, e)
            return []
    
    def _sync_persist(results_list):
        nonlocal added
        with app.app_context():
            for results in results_list:
                for r in results:
                    if added >= limit:
                        break
                    url = r["url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = r.get("title", "")
                    if _is_mega_brand(url, title):
                        continue
                    exists = Target.query.filter_by(url=url).first()
                    if exists:
                        continue
                    t = Target(url=url, title=title, source_query=r.get("source_query", ""))
                    db.session.add(t)
                    added += 1
                if added >= limit:
                    break
            db.session.commit()
        return added
    
    loop = asyncio.get_event_loop()
    
    # Phase 1: Run all search queries in parallel (max 4 concurrent)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_sync_search_one, q) for q in AUTO_QUERIES]
        all_results = [f.result() for f in concurrent.futures.as_completed(futures, timeout=60)]
    
    # Phase 2: Persist all results in one transaction
    added = await loop.run_in_executor(None, _sync_persist, all_results)
    
    logger.info("Discovered %d new targets via parallel search", added)
    return added


async def _process_single_target_async(target_id: int, attempt: int = 1):
    """Process one target with a single browser checkout/release cycle."""
    app = _get_app()
    pool = await get_pool()
    session_id = f"target_{target_id}_a{attempt}"
    session_log_lines = []

    def _log(msg: str):
        session_log_lines.append(f"{datetime.now(timezone.utc).isoformat()} {msg}")
        logger.info(msg)

    with app.app_context():
        target = db.session.get(Target, target_id)
        if not target:
            logger.warning("Target %s not found", target_id)
            return

        try:
            # Checkout one context/page for this target
            sess = await pool._checkout(session_id)
            _log(f"CHECKOUT {target.url}")

            # Navigate with longer timeout
            _log(f"NAVIGATING {target.url}")
            await asyncio.wait_for(
                pool.navigate(target.url, session_id),
                timeout=40,
            )
            await asyncio.sleep(0.1)

            # Take live screenshot immediately after DOM ready
            try:
                os.makedirs(Config.SCREENSHOT_DIR, exist_ok=True)
                await pool.screenshot(
                    path=os.path.join(Config.SCREENSHOT_DIR, "live_current.png"),
                    session_id=session_id,
                    full_page=False,
                )
            except Exception:
                pass

            # Analyze page
            page_title = await pool.title(session_id)
            html = await pool.content(session_id)
            has_form = await pool.evaluate(
                "return document.querySelectorAll('input, textarea, select').length > 2", session_id
            )
            emails = await pool.evaluate(r"""return (() => {
                const text = document.body.innerText;
                const matches = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g);
                return Array.from(new Set(matches || []));
            })();""", session_id)
            has_captcha = bool(
                await pool.query_selector(".g-recaptcha", session_id)
                or await pool.query_selector(".h-captcha", session_id)
                or await pool.query_selector("iframe[src*='recaptcha']", session_id)
                or await pool.query_selector("iframe[src*='hcaptcha']", session_id)
            )

            target.has_form = bool(has_form)
            target.has_captcha = bool(has_captcha)
            target.page_title = page_title or ""
            target.emails_found = len(emails)
            target.status = "analyzed"
            db.session.commit()
            _log(f"ANALYZED form={has_form} captcha={has_captcha} emails={len(emails)}")

            if not has_form:
                target.status = "no_form"
                db.session.commit()
                pipeline_stats.record_skip()
                _log("NO_FORM")
                return

            # AI analysis
            page_html = html
            disable_llm = os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true"
            if disable_llm:
                summary = {"summary": "", "angle": "", "suggested_message": Config.COMPANY_DATA["message"]}
                add_msg = Config.COMPANY_DATA["message"]
            else:
                try:
                    summary = ai_summarize_target(target.url, page_html[:4000], Config.COMPANY_DATA)
                    add_msg = ai_generate_additional_message(Config.COMPANY_DATA, summary)
                except Exception as e:
                    logger.warning("LLM failed for %s: %s", target.url, e)
                    summary = {"summary": "", "angle": "", "suggested_message": Config.COMPANY_DATA["message"]}
                    add_msg = Config.COMPANY_DATA["message"]

            company_data = dict(Config.COMPANY_DATA)
            company_data["message"] = add_msg

            # CAPTCHA
            if target.has_captcha:
                from app.captcha_async import solve_captcha_if_present
                try:
                    await solve_captcha_if_present(session_id, pool)
                    pipeline_stats.record_captcha(solved=True)
                    _log("CAPTCHA_SOLVED")
                except Exception as e:
                    pipeline_stats.record_captcha(solved=False)
                    _log(f"CAPTCHA_FAILED {e}")

            # Detect and fill fields
            fields = await pool.detect_fields(session_id=session_id)
            _log(f"DETECTED_FIELDS {len(fields)}")
            if not fields:
                _log("NO_FIELDS")
                target.status = "no_fields"
                db.session.commit()
                pipeline_stats.record_skip()
                return

            mapping = ai_map_fields_smart(fields, company_data, summary)
            _log(f"MAPPING_KEYS {list(mapping.keys())}")

            submit_result = await pool.ai_fill_and_submit(
                mapping, session_id=session_id, fields=fields
            )

            # ── Video Recording ────────────────────────────────
            video_path = ""
            try:
                from app.form_proof import VideoRecorder
                recorder = VideoRecorder()
                video_path = recorder.start(
                    f"static/videos/proof_{target_id}_submit.mp4",
                    display=":99",
                    duration=25,
                )
                _log(f'VIDEO_STARTED {video_path}')
            except Exception as vr_err:
                logger.warning('Video recording start failed: %s', vr_err)
            # ── /Video Recording ─────────────────────────────

            submit_result = await pool.ai_fill_and_submit(
                mapping, session_id=session_id, fields=fields
            )

            # Stop video recording
            try:
                if video_path:
                    recorder.stop()
                    _log(f'VIDEO_STOPPED {video_path}')
            except Exception as vr_err:
                logger.warning('Video recording stop failed: %s', vr_err)

            # ── Form Proof Capture ─────────────────────────────
            try:
                from app.form_proof import FormProofBuilder
                builder = FormProofBuilder(target_id, target.url, session_id=session_id)
                # Capture pre-fill screenshot (form state before fill)
                pre_ss = await builder.capture_pre(pool)
                _log(f'PROOF_PRE_SS {pre_ss}')
                # Capture post-submit screenshot
                post_ss = submit_result.get('screenshot', '')
                if not post_ss:
                    post_ss = await builder.capture_post(pool)
                # Capture confirmation after page settles
                confirmation_ss = await builder.capture_confirmation(pool, wait_seconds=4)
                # Extract what was actually written
                actual_values = await builder.extract_actual_values(pool)
                final_url = await builder.extract_final_url(pool)
                # Save full proof record
                proof_id = builder.save(
                    pre_screenshot=pre_ss,
                    post_screenshot=post_ss,
                    confirmation_screenshot=confirmation_ss,
                    video_path=video_path,
                    detected_fields=fields,
                    ai_mapping=mapping,
                    actual_values=actual_values,
                    submitted_message=company_data.get('message', ''),
                    final_url=final_url,
                    status=submit_result.get('status', 'unknown'),
                    session_log='\n'.join(session_log_lines),
                )
                _log(f'PROOF_SAVED id={proof_id}')
            except Exception as proof_err:
                logger.warning('FormProof capture failed for %s: %s', target.url, proof_err)
            # ── /Form Proof Capture ────────────────────────────

            # Save submission with full details
            sub = Submission(
                target_id=target.id,
                status=submit_result["status"],
                fields_filled=submit_result["fields_filled"],
                fields_total=submit_result["fields_total"],
                screenshot_path=submit_result["screenshot"],
                field_mapping=json.dumps(mapping),
                session_log="\n".join(session_log_lines),
                final_url=submit_result.get("final_url", await pool.evaluate("return window.location.href", session_id=session_id)),
            )
            db.session.add(sub)
            db.session.commit()

            if submit_result["status"] == "submitted":
                pipeline_stats.record_submit()
                _log(f"SUBMITTED {submit_result['fields_filled']}/{submit_result['fields_total']} fields")
            else:
                pipeline_stats.record_fail()
                _log(f"NOT_SUBMITTED {submit_result['status']}")

            target.status = submit_result["status"]
            db.session.commit()

            # Save leads with exact form data that was submitted
            leads_added = 0
            leads_updated = 0
            form_json = json.dumps(mapping)
            msg_sent = company_data.get("message", "")
            for email_addr in emails:
                existing = Lead.query.filter_by(email=email_addr).first()
                if not existing:
                    lead = Lead(
                        email=email_addr,
                        source_url=target.url,
                        status="new",
                        submitted_form_data=form_json,
                        submitted_message=msg_sent,
                        submission_id=sub.id,
                        target_id=target.id,
                    )
                    db.session.add(lead)
                    leads_added += 1
                else:
                    # Update existing lead with latest form data
                    if not existing.submitted_form_data:
                        existing.submitted_form_data = form_json
                    if not existing.submitted_message:
                        existing.submitted_message = msg_sent
                    if not existing.submission_id:
                        existing.submission_id = sub.id
                    if not existing.target_id:
                        existing.target_id = target.id
                    existing.source_url = target.url
                    existing.status = "new"
                    leads_updated += 1
            if leads_added or leads_updated:
                db.session.commit()
                _log(f"LEADS_ADDED {leads_added} UPDATED {leads_updated} with form data")

            # Screenshot after submit
            try:
                await pool.screenshot(
                    path=os.path.join(Config.SCREENSHOT_DIR, "live_submitted.png"),
                    session_id=session_id,
                    full_page=False,
                )
            except Exception:
                pass

            # Keep browser open for verification with periodic live screenshots
            keep_open_seconds = int(os.getenv("KEEP_OPEN_SECONDS", "12"))
            if keep_open_seconds > 0:
                _log(f"KEEP_OPEN {keep_open_seconds}s")
                for _ in range(keep_open_seconds // 3):
                    await asyncio.sleep(3)
                    try:
                        await pool.screenshot(
                            path=os.path.join(Config.SCREENSHOT_DIR, "live_current.png"),
                            full_page=False,
                        )
                    except Exception:
                        pass

        except Exception as e:
            pipeline_stats.record_fail()
            _log(f"ERROR {e}")
            target.status = "error"
            target.error_message = str(e)[:500]
            db.session.commit()
            
            # Retry once for transient errors
            if attempt < 2 and "timeout" not in str(e).lower():
                logger.info("Retrying target %s (attempt %d)", target.url, attempt + 1)
                await asyncio.sleep(0.5)
                await _process_single_target_async(target_id, attempt=attempt + 1)
        finally:
            try:
                await pool._release(session_id)
                _log("RELEASED")
            except Exception as re:
                logger.debug("Release error: %s", re)


# Global flag to prevent multiple pipeline runs
_pipeline_running = False
_last_discovery_time = None


async def run_pipeline_async(batch_size: int = None, max_concurrent: int = None):
    """Infinite pipeline loop — runs continuously until container stops."""
    global _pipeline_running
    if _pipeline_running:
        logger.info("Pipeline already running, skipping duplicate start")
        return
    
    _pipeline_running = True
    batch_size = batch_size or Config.PIPELINE_BATCH_SIZE
    max_concurrent = max_concurrent or Config.PIPELINE_MAX_CONCURRENT

    app = _get_app()
    loop = asyncio.get_event_loop()
    
    def _get_pending():
        with app.app_context():
            return Target.query.filter_by(status="pending").order_by(Target.id).all()
    
    def _count_pending():
        with app.app_context():
            return Target.query.filter_by(status="pending").count()
    
    def _persist_stats(stats):
        with app.app_context():
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
    
    try:
        while True:
            # ── Phase 1: Check / discover targets ──────────────────────────
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = await loop.run_in_executor(pool, _get_pending)
            
            if not pending:
                import time
                global _last_discovery_time
                now = time.time()
                if _last_discovery_time and (now - _last_discovery_time) < 300:
                    logger.info("No pending targets — discovery cooldown active (%.0fs left)", 300 - (now - _last_discovery_time))
                    await asyncio.sleep(30)
                    continue
                _last_discovery_time = now
                logger.info("No pending targets — running discovery...")
                try:
                    await asyncio.wait_for(_discover_targets(limit=Config.PIPELINE_TARGET_DAILY), timeout=60)
                except asyncio.TimeoutError:
                    logger.warning('Target discovery timed out after 90s')
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pending = await loop.run_in_executor(pool, _get_pending)
                
                if not pending:
                    logger.info("Still no targets after discovery — waiting 30s before retry")
                    await asyncio.sleep(30)
                    continue
            
            # ── Phase 2: Process batch ─────────────────────────────────────
            pipeline_stats.start(len(pending))
            semaphore = asyncio.Semaphore(max_concurrent)
            
            async def _run(target_id):
                async with semaphore:
                    try:
                        await asyncio.wait_for(
                            _process_single_target_async(target_id),
                            timeout=90,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Target %s timed out after 90s", target_id)
                        pipeline_stats.record_fail()
                        with app.app_context():
                            t = db.session.get(Target, target_id)
                            if t:
                                t.status = "timeout"
                                db.session.commit()
            
            tasks = [asyncio.create_task(_run(t.id)) for t in pending[:batch_size]]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            stats = pipeline_stats.to_dict()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                await loop.run_in_executor(pool, _persist_stats, stats)
            logger.info("Pipeline batch completed: %s", stats)
            
            # ── Phase 3: Brief pause before next cycle ─────────────────────
            await asyncio.sleep(5)
    
    except asyncio.CancelledError:
        logger.info("Pipeline cancelled (container shutting down)")
    except Exception as e:
        logger.exception("Pipeline crashed: %s", e)
    finally:
        _pipeline_running = False
_last_discovery_time = None
