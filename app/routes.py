import threading

from flask import (
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app import db
from app.browser import browser_mgr
from app.config import Config
from app.models import Lead, Submission, Target
from app.pipeline import get_pipeline_status, run_high_volume_pipeline
from app.ai_engine import ai_generate_additional_message, ai_map_fields_smart, ai_summarize_target
from app.captcha import solve_captcha_if_present
import os


def _admin_logged_in():
    return session.get("admin") is True


def _require_admin():
    if not _admin_logged_in():
        return redirect(url_for("login"))
    return None


def _start_pipeline_async():
    t = threading.Thread(target=run_high_volume_pipeline, daemon=True)
    t.start()


def register_routes(app):
    from app.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp)
    
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
                session["admin"] = True
                return redirect(url_for("dashboard"))
            return render_template("login.html", error="Invalid credentials")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def dashboard():
        guard = _require_admin()
        if guard:
            return guard
        targets = Target.query.order_by(Target.id.desc()).limit(25).all()
        submissions = Submission.query.order_by(Submission.id.desc()).limit(25).all()
        leads = Lead.query.order_by(Lead.id.desc()).limit(25).all()
        return render_template(
            "dashboard.html",
            targets=targets,
            submissions=submissions,
            leads=leads,
            pipeline=get_pipeline_status(),
        )

    @app.route("/api/pipeline/start", methods=["POST"])
    def api_pipeline_start():
        guard = _require_admin()
        if guard:
            return guard
        _start_pipeline_async()
        return jsonify({"ok": True})

    @app.route("/api/pipeline/status")
    def api_pipeline_status():
        guard = _require_admin()
        if guard:
            return guard
        return jsonify(get_pipeline_status())

    @app.route("/api/targets", methods=["GET"])
    def api_targets():
        guard = _require_admin()
        if guard:
            return guard
        targets = Target.query.order_by(Target.id.desc()).limit(200).all()
        return jsonify({"targets": [
            {
                "id": t.id,
                "url": t.url,
                "status": t.status,
                "has_form": t.has_form,
                "has_captcha": t.has_captcha,
                "score": t.score,
                "page_title": t.page_title,
                "emails_found": t.emails_found,
                "source_query": t.source_query,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            } for t in targets
        ]})

    @app.route("/api/targets/<int:target_id>")
    def api_target_detail(target_id):
        guard = _require_admin()
        if guard:
            return guard
        t = db.session.get(Target, target_id)
        if not t:
            return jsonify({"error": "not found"}), 404
        subs = Submission.query.filter_by(target_id=target_id).order_by(Submission.id.desc()).all()
        leads = Lead.query.filter_by(source_url=t.url).all()
        return jsonify({
            "target": {
                "id": t.id,
                "url": t.url,
                "title": t.title,
                "status": t.status,
                "has_form": t.has_form,
                "has_captcha": t.has_captcha,
                "page_title": t.page_title,
                "emails_found": t.emails_found,
                "source_query": t.source_query,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            },
            "submissions": [
                {
                    "id": s.id,
                    "status": s.status,
                    "fields_filled": s.fields_filled,
                    "fields_total": s.fields_total,
                    "screenshot_path": s.screenshot_path,
                    "field_mapping": s.field_mapping,
                    "session_log": s.session_log,
                    "final_url": s.final_url,
                    "error_message": s.error_message,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                } for s in subs
            ],
            "leads": [
                {
                    "id": l.id,
                    "email": l.email,
                    "name": l.name,
                    "company": l.company,
                    "status": l.status,
                } for l in leads
            ],
        })

    @app.route("/api/targets/add", methods=["POST"])
    def api_targets_add():
        guard = _require_admin()
        if guard:
            return guard
        payload = request.get_json(force=True)
        url = payload.get("url")
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400
        existing = Target.query.filter_by(url=url).first()
        if existing:
            return jsonify({"ok": True, "target_id": existing.id})
        t = Target(url=url, status="pending")
        db.session.add(t)
        db.session.commit()
        return jsonify({"ok": True, "target_id": t.id})

    @app.route("/api/submissions", methods=["GET"])
    def api_submissions():
        guard = _require_admin()
        if guard:
            return guard
        subs = Submission.query.order_by(Submission.id.desc()).limit(200).all()
        return jsonify({"submissions": [
            {
                "id": s.id,
                "target_id": s.target_id,
                "status": s.status,
                "fields_filled": s.fields_filled,
                "fields_total": s.fields_total,
                "screenshot": s.screenshot_path,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            } for s in subs
        ]})

    @app.route("/api/leads")
    def api_leads():
        guard = _require_admin()
        if guard:
            return guard
        leads = Lead.query.order_by(Lead.id.desc()).limit(200).all()
        return jsonify({"leads": [
            {
                "id": l.id,
                "email": l.email,
                "name": l.name,
                "company": l.company,
                "source_url": l.source_url,
                "status": l.status,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            } for l in leads
        ]})

    @app.route("/api/browser/navigate", methods=["POST"])
    def api_browser_navigate():
        guard = _require_admin()
        if guard:
            return guard
        payload = request.get_json(force=True)
        url = payload.get("url")
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400
        final_url = browser_mgr.navigate(url)
        return jsonify({"ok": True, "url": final_url})

    @app.route("/api/browser/url")
    def api_browser_url():
        guard = _require_admin()
        if guard:
            return guard
        session_id = browser_mgr.get_last_session_id()
        return jsonify({"url": browser_mgr.get_url(session_id=session_id), "session_id": session_id})

    @app.route("/api/browser/screenshot")
    def api_browser_screenshot():
        guard = _require_admin()
        if guard:
            return guard
        session_id = request.args.get("session")
        if not session_id:
            session_id = browser_mgr.get_last_session_id()
        img = browser_mgr.screenshot(session_id=session_id)
        return Response(img, mimetype="image/jpeg")

    @app.route("/api/browser/click", methods=["POST"])
    def api_browser_click():
        guard = _require_admin()
        if guard:
            return guard
        payload = request.get_json(force=True)
        x = payload.get("x")
        y = payload.get("y")
        session_id = payload.get("session_id") or browser_mgr.get_last_session_id()
        if x is None or y is None:
            return jsonify({"ok": False, "error": "x/y required"}), 400
        browser_mgr.click(float(x), float(y), session_id=session_id)
        return jsonify({"ok": True})

    @app.route("/api/test/submit", methods=["POST"])
    def api_test_submit():
        guard = _require_admin()
        if guard:
            return guard
        payload = request.get_json(force=True)
        url = payload.get("url")
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400

        session_id = "live"
        disable_llm = os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true"
        try:
            logger.info("Test submit: navigating to %s", url)
            analysis = browser_mgr.analyze_target(url, session_id=session_id)
            html = analysis.get("html", "")
            logger.info("Test submit: analysis form=%s captcha=%s", analysis.get("has_form"), analysis.get("has_captcha"))

            if not analysis.get("has_form"):
                contact_url = browser_mgr.find_contact_url(url, session_id=session_id)
                if contact_url:
                    logger.info("Test submit: found contact page %s", contact_url)
                    analysis = browser_mgr.analyze_target(contact_url, session_id=session_id)
                    html = analysis.get("html", "")
                    url = contact_url

            if analysis.get("has_captcha"):
                try:
                    solve_captcha_if_present(session_id, browser_mgr)
                except Exception as e:
                    logger.warning("CAPTCHA solve failed: %s", e)

            company_data = dict(Config.COMPANY_DATA)
            if disable_llm:
                summary = {"summary": "", "angle": "", "suggested_message": company_data["message"]}
            else:
                try:
                    summary = ai_summarize_target(url, html[:4000], Config.COMPANY_DATA)
                    add_msg = ai_generate_additional_message(Config.COMPANY_DATA, summary)
                    company_data["message"] = add_msg
                except Exception as e:
                    logger.warning("LLM failed, using default message: %s", e)
                    summary = {"summary": "", "angle": "", "suggested_message": company_data["message"]}

            fields = browser_mgr.detect_fields(session_id=session_id)
            logger.info("Test submit: detected %d fields", len(fields))
            if not fields:
                return jsonify({"ok": False, "error": "no_fields_detected", "url": url})

            mapping = ai_map_fields_smart(fields, company_data, summary)
            logger.info("Test submit: mapping generated, filling form...")
            submit_result = browser_mgr.ai_fill_and_submit(mapping, session_id=session_id, fields=fields)
            logger.info("Test submit: result=%s", submit_result)

            # Build field mapping log
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

            # Save to DB
            existing = Target.query.filter_by(url=url).first()
            if not existing:
                t = Target(url=url, status="submitted", has_form=True)
                db.session.add(t)
            else:
                existing.status = "submitted"
            import json
            sub = Submission(
                status=submit_result.get("status"),
                fields_filled=submit_result.get("fields_filled", 0),
                fields_total=submit_result.get("fields_total", 0),
                screenshot_path=submit_result.get("screenshot", ""),
                field_mapping=json.dumps(mapping_log),
                final_url=submit_result.get("final_url", ""),
            )
            db.session.add(sub)
            db.session.commit()

            return jsonify({"ok": True, "url": url, "result": submit_result})
        except Exception as e:
            logger.error("Test submit failed: %s", e)
            return jsonify({"ok": False, "error": str(e), "url": url}), 500

    @app.route("/api/browser/session")
    def api_browser_session():
        guard = _require_admin()
        if guard:
            return guard
        return jsonify({"session_id": browser_mgr.get_last_session_id()})

    @app.route("/api/logs/tail")
    def api_logs_tail():
        guard = _require_admin()
        if guard:
            return guard
        lines = int(request.args.get("lines", 40))
        log_path = os.path.join("logs", "orbital.log")
        if not os.path.exists(log_path):
            return jsonify({"lines": []})
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                data = f.read().splitlines()
            return jsonify({"lines": data[-lines:]})
        except Exception:
            return jsonify({"lines": []})
