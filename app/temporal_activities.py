"""Temporal Activities for Orbital Pipeline — Selenium edition with FormProof + video.
Idempotent units of work that interact with external systems.
"""
import asyncio
import logging
from typing import Dict, List, Any

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def discover_targets_activity(limit: int = 200) -> List[Dict[str, Any]]:
    """Discover targets: return DB pending first, then try search."""
    from app.search import search_firecrawl
    from app import create_app, db
    from app.models import Target

    app = create_app()
    with app.app_context():
        # ALWAYS return pending targets first so the pipeline has work immediately
        pending = Target.query.filter_by(status="pending").order_by(Target.id).limit(limit).all()
        if pending:
            logger.info("Using %d pending targets from DB", len(pending))
            return [{"id": t.id, "url": t.url, "title": t.title, "source_query": t.source_query} for t in pending]

        # Fallback search: crawl4ai first, then Firecrawl, then DuckDuckGo
        from app.constants import AUTO_QUERIES

        added = 0
        for q in AUTO_QUERIES:
            if added >= limit:
                break
            results = []
            # Try crawl4ai first
            try:
                from app.search import search_crawl4ai
                results = search_crawl4ai(q, limit=10)
            except Exception as e:
                logger.warning("crawl4ai search failed for '%s': %s", q, e)
            # Fallback to Firecrawl
            if not results:
                try:
                    results = search_firecrawl(q, limit=10)
                except Exception:
                    pass
            for r in results:
                exists = Target.query.filter_by(url=r["url"]).first()
                if exists:
                    continue
                t = Target(url=r["url"], title=r.get("title", ""), source_query=q)
                db.session.add(t)
                added += 1
                if added >= limit:
                    break

        db.session.commit()
        logger.info("Discovered %d new targets via search", added)

        pending = Target.query.filter_by(status="pending").order_by(Target.id).limit(100).all()
        return [{"id": t.id, "url": t.url, "title": t.title, "source_query": t.source_query} for t in pending]


@activity.defn
async def analyze_target_activity(target_id: int, url: str) -> Dict[str, Any]:
    """Analyze target page for forms, emails, and content. Uses Selenium BrowserPool."""
    from app import create_app, db
    from app.models import Target
    from app.browser_selenium import get_pool

    app = create_app()
    pool = None
    try:
        pool = await get_pool()
        session_id = f"target_{target_id}"
        await pool._checkout(session_id)
        await pool.navigate(url, session_id)
        await asyncio.sleep(1)

        # Live screenshot: navigating
        try:
            await pool.screenshot('static/screenshots/live_navigating.png', session_id)
        except Exception:
            pass

        page_title = await pool.title(session_id)
        html = await pool.content(session_id)
        has_form = await pool.evaluate(
            "return document.querySelectorAll('input, textarea, select').length > 2", session_id
        )
        emails = await pool.evaluate(
            "return (() => { const text = document.body.innerText; const matches = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g); return Array.from(new Set(matches || [])); })();",
            session_id,
        )
        has_captcha = bool(
            await pool.query_selector(".g-recaptcha", session_id)
            or await pool.query_selector(".h-captcha", session_id)
            or await pool.query_selector("iframe[src*='recaptcha']", session_id)
            or await pool.query_selector("iframe[src*='hcaptcha']", session_id)
        )

        with app.app_context():
            target = db.session.get(Target, target_id)
            if target:
                target.has_form = bool(has_form)
                target.has_captcha = bool(has_captcha)
                target.page_title = page_title or ""
                target.emails_found = len(emails or [])
                target.status = "analyzed"
                db.session.commit()

        logger.info("Analyzed target %s: form=%s, captcha=%s", url, has_form, has_captcha)
        return {
            "target_id": target_id,
            "url": url,
            "has_form": bool(has_form),
            "has_captcha": bool(has_captcha),
            "page_title": page_title or "",
            "emails": emails or [],
            "html": (html or "")[:4000],
        }
    finally:
        if pool:
            try:
                await pool._release(f"target_{target_id}")
            except Exception:
                pass


@activity.defn
async def generate_target_summary_activity(url: str, html: str, company_data: Dict[str, Any]) -> Dict[str, str]:
    """Generate AI summary, angle, and message for target."""
    import os
    from app.ai_engine import ai_summarize_target, ai_generate_additional_message

    disable_llm = os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true"
    if disable_llm:
        return {
            "summary": "",
            "angle": "",
            "suggested_message": company_data.get("message", ""),
        }

    try:
        summary = ai_summarize_target(url, html, company_data)
        add_msg = ai_generate_additional_message(company_data, summary)
        return {"summary": summary.get("summary", ""), "angle": summary.get("angle", ""), "suggested_message": add_msg}
    except Exception as e:
        logger.warning("LLM failed for %s: %s", url, e)
        return {"summary": "", "angle": "", "suggested_message": company_data.get("message", "")}


@activity.defn
async def detect_and_fill_form_activity(
    target_id: int,
    url: str,
    company_data: Dict[str, Any],
    summary: Dict[str, str],
    has_captcha: bool,
) -> Dict[str, Any]:
    """Detect form fields, fill them, submit, capture video + FormProof."""
    import os
    from app import create_app, db
    from app.models import Target, Submission, FormProof
    from app.browser_selenium import get_pool
    from app.ai_engine import ai_map_fields_smart
    from app.form_proof import FormProofBuilder, VideoRecorder

    app = create_app()
    pool = None
    session_id = f"target_{target_id}"
    try:
        pool = await get_pool()
        await pool._checkout(session_id)

        company_data_copy = dict(company_data)
        company_data_copy["message"] = summary.get("suggested_message", company_data.get("message", ""))

        # CAPTCHA
        if has_captcha:
            from app.captcha_async import solve_captcha_if_present
            try:
                await solve_captcha_if_present(session_id, pool)
            except Exception as e:
                logger.warning("CAPTCHA failed for %s: %s", url, e)

        # Detect fields
        fields = await pool.detect_fields(session_id=session_id)
        logger.info("Detected %d fields in %s", len(fields), url)

        # Live screenshot: detected
        try:
            await pool.screenshot('static/screenshots/live_detected.png', session_id)
        except Exception:
            pass

        # AI mapping
        mapping = ai_map_fields_smart(fields, company_data_copy, summary)

        # ── Video recording ────────────────────────────────
        video_path = ""
        recorder = None
        try:
            recorder = VideoRecorder()
            video_path = recorder.start(
                f"static/videos/proof_{target_id}_submit.mp4",
                display=":99",
                duration=25,
            )
            logger.info("VIDEO_STARTED %s", video_path)
        except Exception as vr_err:
            logger.warning("Video recording start failed: %s", vr_err)

        # Live screenshot: filling
        try:
            await pool.screenshot('static/screenshots/live_filling.png', session_id)
        except Exception:
            pass

        # Fill and submit
        submit_result = await pool.ai_fill_and_submit(mapping, session_id=session_id, fields=fields)

        # Live screenshot: submitted
        try:
            await pool.screenshot('static/screenshots/live_submitted.png', session_id)
        except Exception:
            pass

        # Stop video
        if recorder:
            try:
                recorder.stop()
                logger.info("VIDEO_STOPPED %s", video_path)
            except Exception as vr_err:
                logger.warning("Video recording stop failed: %s", vr_err)

        # ── FormProof capture ─────────────────────────────
        try:
            builder = FormProofBuilder(target_id, url, session_id=session_id)
            pre_ss = await builder.capture_pre(pool)
            post_ss = submit_result.get("screenshot", "")
            if not post_ss:
                post_ss = await builder.capture_post(pool)
            confirmation_ss = await builder.capture_confirmation(pool, wait_seconds=4)
            actual_values = await builder.extract_actual_values(pool)
            final_url = await builder.extract_final_url(pool)

            builder.save(
                pre_screenshot=pre_ss,
                post_screenshot=post_ss,
                confirmation_screenshot=confirmation_ss,
                video_path=video_path,
                detected_fields=fields,
                ai_mapping=mapping,
                actual_values=actual_values,
                submitted_message=company_data_copy.get("message", ""),
                final_url=final_url,
                status=submit_result.get("status", "unknown"),
                session_log=f"Temporal activity for target {target_id}",
            )
        except Exception as proof_err:
            logger.warning("FormProof capture failed for %s: %s", url, proof_err)

        # Save submission
        with app.app_context():
            target = db.session.get(Target, target_id)
            if target:
                target.status = submit_result["status"]
                db.session.commit()

            sub = Submission(
                target_id=target_id,
                status=submit_result["status"],
                fields_filled=submit_result["fields_filled"],
                fields_total=submit_result["fields_total"],
                screenshot_path=submit_result.get("screenshot", ""),
            )
            db.session.add(sub)
            db.session.commit()

        logger.info("Form submission for %s: %s (%d/%d fields)",
                    url, submit_result["status"], submit_result["fields_filled"], submit_result["fields_total"])
        return {
            "target_id": target_id,
            "status": submit_result["status"],
            "fields_filled": submit_result["fields_filled"],
            "fields_total": submit_result["fields_total"],
            "screenshot": submit_result.get("screenshot", ""),
            "video_path": video_path,
        }
    finally:
        if pool:
            try:
                await pool._release(session_id)
            except Exception:
                pass


@activity.defn
async def save_leads_activity(target_id: int, emails: List[str]) -> Dict[str, int]:
    """Save discovered emails as leads."""
    from app import create_app, db
    from app.models import Lead, Target

    app = create_app()
    with app.app_context():
        target = db.session.get(Target, target_id)
        if not target:
            raise ValueError(f"Target {target_id} not found")

        added = 0
        for email_addr in emails:
            existing = Lead.query.filter_by(email=email_addr).first()
            if not existing:
                lead = Lead(email=email_addr, source_url=target.url, status="new")
                db.session.add(lead)
                added += 1

        db.session.commit()
        logger.info("Saved %d new leads from %s", added, target.url)
        return {"added": added, "total": len(emails)}


@activity.defn
async def update_target_status_activity(target_id: int, status: str, error_msg: str = None) -> bool:
    """Update target status in database."""
    from app import create_app, db
    from app.models import Target

    app = create_app()
    with app.app_context():
        target = db.session.get(Target, target_id)
        if not target:
            logger.warning("Target %d not found for status update", target_id)
            return False

        target.status = status
        if error_msg:
            target.error_message = error_msg[:500]
        db.session.commit()
        logger.info("Updated target %d status to %s", target_id, status)
        return True
