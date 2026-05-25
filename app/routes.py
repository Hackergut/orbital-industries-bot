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
                "created_at": t.created_at.isoformat() if t.created_at else None,
            } for t in targets
        ]})

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

    @app.route("/api/browser/session")
    def api_browser_session():
        guard = _require_admin()
        if guard:
            return guard
        return jsonify({"session_id": browser_mgr.get_last_session_id()})
