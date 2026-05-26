import asyncio


from datetime import datetime, timezone
"""FastAPI routes for Orbital Industries."""
import json
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app import create_app, db
from app.ai_engine import ai_generate_additional_message, ai_map_fields_smart, ai_summarize_target
from app.browser_async import get_pool, shutdown_pool
from app.config import Config
from app.models import Lead, PipelineStat, Submission, Target
from app.pipeline import get_pipeline_status
from app.tasks import run_pipeline_task

logger = logging.getLogger(__name__)
router = APIRouter()

# Keep references to background tasks to prevent GC
_background_tasks = []

flask_app = create_app()


def _admin_logged_in(request: Request) -> bool:
    return request.session.get("admin") is True


def _require_admin(request: Request, redirect: bool = False):
    if not _admin_logged_in(request):
        if redirect:
            return None
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── HTML Views ──────────────────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbital Login</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0a0e27;color:#e0e0e0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#1a1e3f;border:1px solid #00ff88;border-radius:8px;padding:32px;width:320px}
h2{color:#00ff88;margin-bottom:20px;text-align:center}
input{width:100%;padding:10px;margin:8px 0;border:1px solid #333;background:#0a0e27;color:#fff;border-radius:4px;box-sizing:border-box}
button{width:100%;padding:12px;background:#00ff88;color:#0a0e27;border:none;border-radius:4px;font-weight:bold;cursor:pointer}
.error{color:#ff3333;font-size:14px;margin-top:8px;text-align:center}
</style></head>
<body>
<div class="card"><h2>Orbital Login</h2>
<form method="post" action="/login">
<input type="text" name="username" placeholder="Username" required>
<input type="password" name="password" placeholder="Password" required>
<button type="submit">Login</button>
</form>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
</div></body></html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbital Dashboard</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0a0e27;color:#e0e0e0;margin:0;padding:20px}
.container{max-width:1400px;margin:0 auto}
h1{color:#00ff88}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:#1a1e3f;border:1px solid #00ff88;border-radius:8px;padding:20px}
.card h2{color:#00ff88;font-size:1.1em;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #333}
th{color:#00ff88}
.status-badge{padding:4px 8px;border-radius:4px;font-size:12px;font-weight:bold}
.status-submitted{background:#004d00;color:#00ff88}
.status-error{background:#4d0000;color:#ff3333}
.status-pending{background:#4d4d00;color:#ffff00}
.btn{padding:10px 16px;background:#00ff88;color:#0a0e27;border:none;border-radius:4px;font-weight:bold;cursor:pointer;margin-right:8px}
.logs{max-height:300px;overflow-y:auto;background:#0a0e27;border:1px solid #00ff88;border-radius:4px;padding:12px;font-family:monospace;font-size:12px;line-height:1.6}
</style></head>
<body>
<div class="container">
<h1>Orbital Industries Dashboard</h1>
<p><a href="/logout" style="color:#00ff88">Logout</a> | <a href="/dashboard/" style="color:#00ff88">Real-time View</a></p>
<div style="margin:16px 0">
<form method="post" action="/api/pipeline/start" style="display:inline"><button class="btn">Start Pipeline</button></form>
<span id="pipeStatus"></span>
</div>
<div class="grid">
<div class="card"><h2>Recent Targets ({{ targets | length }})</h2>
<table><thead><tr><th>ID</th><th>URL</th><th>Status</th><th>Form</th></tr></thead><tbody>
{% for t in targets %}<tr><td>{{ t.id }}</td><td><a href="{{ t.url }}" target="_blank" style="color:#00ccff">{{ t.url[:60] }}</a></td>
<td><span class="status-badge status-{{ t.status }}">{{ t.status }}</span></td><td>{{ 'Yes' if t.has_form else 'No' }}</td></tr>
{% endfor %}</tbody></table></div>
<div class="card"><h2>Recent Submissions ({{ submissions | length }})</h2>
<table><thead><tr><th>ID</th><th>Status</th><th>Fields</th><th>Screenshot</th></tr></thead><tbody>
{% for s in submissions %}<tr><td>{{ s.id }}</td><td><span class="status-badge status-{{ s.status }}">{{ s.status }}</span></td><td>{{ s.fields_filled }}/{{ s.fields_total }}</td>
<td>{% if s.screenshot_path %}<a href="/{{ s.screenshot_path }}" target="_blank" style="color:#00ccff">View</a>{% else %}-{% endif %}</td></tr>
{% endfor %}</tbody></table></div>
<div class="card"><h2>Recent Leads ({{ leads | length }})</h2>
<table><thead><tr><th>Email</th><th>Source</th><th>Status</th></tr></thead><tbody>
{% for l in leads %}<tr><td>{{ l.email }}</td><td>{{ l.source_url[:50] if l.source_url else '' }}</td><td>{{ l.status }}</td></tr>
{% endfor %}</tbody></table></div>
<div class="card"><h2>Pipeline Stats</h2>
<pre>{{ pipeline_json }}</pre></div>
</div>
<script>
async function refreshStatus(){
  try{const r=await fetch('/api/pipeline/status');const d=await r.json();document.getElementById('pipeStatus').textContent=JSON.stringify(d,null,2);}catch(e){}
}
refreshStatus();setInterval(refreshStatus,5000);
</script>
</body></html>
"""


@router.get("/login", response_class=HTMLResponse, name="login")
async def login_get(request: Request, error: str = ""):
    if _admin_logged_in(request):
        return RedirectResponse(url="/", status_code=302)
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    # Fallback inline if template missing
    return HTMLResponse(LOGIN_HTML.replace("{% if error %}<div class=\"error\">{{ error }}</div>{% endif %}",
        f'<div class="error">{error}</div>' if error else ''))


@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(""), password: str = Form("")):
    if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
        request.session["admin"] = True
        return RedirectResponse(url="/", status_code=302)
    return await login_get(request, error="Invalid credentials")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@router.get("/", response_class=HTMLResponse, name="dashboard")
async def dashboard(request: Request):
    # Redirect to live browser view
    return RedirectResponse(url="/live", status_code=302)

    def _query():
        with flask_app.app_context():
            targets = Target.query.order_by(Target.id.desc()).limit(25).all()
            submissions = Submission.query.order_by(Submission.id.desc()).limit(25).all()
            leads = Lead.query.order_by(Lead.id.desc()).limit(25).all()
            return targets, submissions, leads

    targets, submissions, leads = await run_in_threadpool(_query)
    pipe = get_pipeline_status()

    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    try:
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "targets": targets,
            "submissions": submissions,
            "leads": leads,
            "pipeline": pipe,
            "pipeline_json": json.dumps(pipe, indent=2, default=str),
        })
    except Exception:
        # Fallback inline
        return HTMLResponse(DASHBOARD_HTML.replace("{{ targets | length }}", str(len(targets)))
            .replace("{{ submissions | length }}", str(len(submissions)))
            .replace("{{ leads | length }}", str(len(leads)))
            .replace("{{ pipeline_json }}", json.dumps(pipe, indent=2, default=str)))


# ── API Routes ──────────────────────────────────────────────────────────

@router.post("/api/pipeline/start")
async def api_pipeline_start(request: Request):
    _require_admin(request)
    import subprocess
    import sys
    import os
    
    venv_python = os.path.join(os.path.dirname(__file__), '..', 'venv', 'bin', 'python')
    if not os.path.exists(venv_python):
        venv_python = sys.executable
    
    cmd = [
        venv_python, '-c',
        'import asyncio; from app.pipeline_async import run_pipeline_async; asyncio.run(run_pipeline_async())'
    ]
    
    subprocess.Popen(
        cmd,
        cwd='/Volumes/HRD2T/ORBITAL TECH 2',
        stdout=open('/tmp/pipeline.log', 'a'),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/", status_code=302)
    return {"ok": True, "status": "pipeline_started"}


@router.get("/api/pipeline/status")
async def api_pipeline_status(request: Request):
    _require_admin(request)
    return get_pipeline_status()


@router.get("/api/targets")
async def api_targets(request: Request):
    _require_admin(request)
    def _query():
        with flask_app.app_context():
            return Target.query.order_by(Target.id.desc()).limit(200).all()
    targets = await run_in_threadpool(_query)
    return {"targets": [
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
    ]}


@router.get("/api/targets/{target_id}")
async def api_target_detail(request: Request, target_id: int):
    _require_admin(request)
    def _query():
        with flask_app.app_context():
            t = db.session.get(Target, target_id)
            if not t:
                return None
            subs = Submission.query.filter_by(target_id=target_id).order_by(Submission.id.desc()).all()
            leads = Lead.query.filter_by(source_url=t.url).all()
            return t, subs, leads

    result = await run_in_threadpool(_query)
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    t, subs, leads = result
    return {
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
                "source_url": l.source_url,
                "status": l.status,
            } for l in leads
        ],
    }


@router.post("/api/test-submit")
async def api_test_submit(request: Request):
    _require_admin(request)
    from app.captcha_async import solve_captcha_if_present
    payload = await request.json()
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url required")

    session_id = "live"
    disable_llm = os.getenv("DISABLE_LLM_FORMS", "false").lower() == "true"
    pool = await get_pool()
    ctx = None
    page = None

    try:
        logger.info("Test submit: navigating to %s", url)
        ctx, page = await pool._checkout(session_id)
        await page.goto(url, wait_until="domcontentloaded", timeout=Config.BROWSER_TIMEOUT)
        await asyncio.sleep(1.5)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        html = await page.content()
        has_form = await page.evaluate("""() => document.querySelectorAll('input, textarea, select').length > 2""")
        logger.info("Test submit: has_form=%s", has_form)

        if not has_form:
            contact_links = await page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                const keywords = ['contact', 'about', 'reach'];
                return links.filter(a => keywords.some(k => a.innerText.toLowerCase().includes(k))).map(a => a.href);
            }""")
            contact_url = ""
            for link in contact_links:
                if link.startswith("http"):
                    contact_url = link
                    break
            if contact_url:
                logger.info("Test submit: found contact page %s", contact_url)
                await page.goto(contact_url, wait_until="domcontentloaded", timeout=Config.BROWSER_TIMEOUT)
                await asyncio.sleep(1)
                html = await page.content()
                url = contact_url

        has_captcha = bool(
            await page.query_selector(".g-recaptcha") or
            await page.query_selector(".h-captcha") or
            await page.query_selector("iframe[src*='recaptcha']") or
            await page.query_selector("iframe[src*='hcaptcha']")
        )
        if has_captcha:
            try:
                await solve_captcha_if_present(session_id, pool)
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

        fields = await pool.detect_fields(session_id=session_id)
        logger.info("Test submit: detected %d fields", len(fields))
        if not fields:
            return {"ok": False, "error": "no_fields_detected", "url": url}

        mapping = ai_map_fields_smart(fields, company_data, summary)
        logger.info("Test submit: mapping generated, filling form...")
        submit_result = await pool.ai_fill_and_submit(mapping, session_id=session_id, fields=fields)
        logger.info("Test submit: result=%s", submit_result)

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

        def _save():
            with flask_app.app_context():
                existing = Target.query.filter_by(url=url).first()
                if not existing:
                    t = Target(url=url, status="submitted", has_form=True)
                    db.session.add(t)
                else:
                    existing.status = "submitted"
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

        await run_in_threadpool(_save)
        return {"ok": True, "url": url, "result": submit_result}
    except Exception as e:
        logger.error("Test submit failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if ctx and page:
            await pool._release(session_id, ctx, page)


@router.get("/api/browser/session")
async def api_browser_session(request: Request):
    _require_admin(request)
    pool = await get_pool()
    return {"session_id": "async_pool", "contexts": pool.pool_size}


@router.get("/api/logs/tail")
async def api_logs_tail(request: Request, lines: int = 40):
    _require_admin(request)
    log_path = os.path.join("logs", "orbital.log")
    if not os.path.exists(log_path):
        return {"lines": []}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read().splitlines()
        return {"lines": data[-lines:]}
    except Exception:
        return {"lines": []}


@router.get("/api/submissions")
async def api_submissions(request: Request, limit: int = 50):
    _require_admin(request)
    def _query():
        with flask_app.app_context():
            subs = Submission.query.order_by(Submission.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": s.id,
                    "target_id": s.target_id,
                    "status": s.status,
                    "fields_filled": s.fields_filled,
                    "fields_total": s.fields_total,
                    "screenshot_path": s.screenshot_path,
                    "final_url": s.final_url,
                    "field_mapping": s.field_mapping,
                    "target_url": s.target.url if s.target else None,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in subs
            ]
    return {"submissions": await run_in_threadpool(_query)}


@router.get("/api/leads")
async def api_leads(request: Request, limit: int = 50):
    _require_admin(request)
    def _query():
        with flask_app.app_context():
            leads = Lead.query.order_by(Lead.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": l.id,
                    "email": l.email,
                    "source_url": l.source_url,
                    "status": l.status,
                    "submitted_message": l.submitted_message,
                    "submitted_form_data": l.submitted_form_data,
                    "submission_id": l.submission_id,
                    "target_id": l.target_id,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                }
                for l in leads
            ]
    return {"leads": await run_in_threadpool(_query)}


@router.get("/api/targets")
async def api_targets_list(request: Request, limit: int = 100, status: str = None):
    _require_admin(request)
    def _query():
        with flask_app.app_context():
            q = Target.query
            if status:
                q = q.filter_by(status=status)
            targets = q.order_by(Target.updated_at.desc()).limit(limit).all()
            return [
                {
                    "id": t.id,
                    "url": t.url,
                    "title": t.title,
                    "status": t.status,
                    "has_form": t.has_form,
                    "has_captcha": t.has_captcha,
                    "emails_found": t.emails_found,
                    "source_query": t.source_query,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                }
                for t in targets
            ]
    return {"targets": await run_in_threadpool(_query)}


@router.get("/api/activity/recent")
async def api_activity_recent(request: Request):
    _require_admin(request)
    def _query():
        with flask_app.app_context():
            # Recent submissions
            subs = Submission.query.order_by(Submission.created_at.desc()).limit(20).all()
            # Recent leads
            leads = Lead.query.order_by(Lead.created_at.desc()).limit(20).all()
            # Recent targets
            targets = Target.query.order_by(Target.updated_at.desc()).limit(20).all()
            # Stats
            total_targets = Target.query.count()
            total_submissions = Submission.query.count()
            total_leads = Lead.query.count()
            submitted_count = Target.query.filter_by(status="submitted").count()
            failed_count = Target.query.filter_by(status="error").count()
            
            return {
                "submissions": [
                    {"id": s.id, "status": s.status, "fields_filled": s.fields_filled, 
                     "fields_total": s.fields_total, "created_at": s.created_at.isoformat() if s.created_at else None,
                     "target_url": s.target.url if s.target else None,
                     "field_mapping": s.field_mapping}
                    for s in subs
                ],
                "leads": [
                    {"id": l.id, "email": l.email, "source_url": l.source_url,
                     "submitted_message": l.submitted_message,
                     "submitted_form_data": l.submitted_form_data,
                     "submission_id": l.submission_id,
                     "target_id": l.target_id,
                     "created_at": l.created_at.isoformat() if l.created_at else None}
                    for l in leads
                ],
                "targets": [
                    {"id": t.id, "url": t.url, "status": t.status, "has_form": t.has_form,
                     "updated_at": t.updated_at.isoformat() if t.updated_at else None}
                    for t in targets
                ],
                "stats": {
                    "total_targets": total_targets,
                    "total_submissions": total_submissions,
                    "total_leads": total_leads,
                    "submitted": submitted_count,
                    "failed": failed_count,
                    "pending": Target.query.filter_by(status="pending").count(),
                }
            }
    return await run_in_threadpool(_query)


async def _get_live_data():
    """Aggregate all live data for SSE or unified endpoint."""
    def _query():
        with flask_app.app_context():
            # Force fresh read from SQLite WAL
            db.session.expire_all()

            # Pipeline status
            rec = PipelineStat.query.order_by(PipelineStat.id.desc()).first()
            status = {
                "started_at": rec.started_at.isoformat() if rec and rec.started_at else None,
                "total_targets": rec.total_targets if rec else 0,
                "processed": rec.processed if rec else 0,
                "submitted": rec.submitted if rec else 0,
                "failed": rec.failed if rec else 0,
                "skipped": rec.skipped if rec else 0,
                "captchas_solved": rec.captchas_solved if rec else 0,
                "captchas_failed": rec.captchas_failed if rec else 0,
                "rate_per_hour": rec.rate_per_hour if rec else 0,
            }

            # Recent submissions
            subs = Submission.query.order_by(Submission.created_at.desc()).limit(15).all()
            submissions = [
                {"id": s.id, "status": s.status, "fields_filled": s.fields_filled,
                 "fields_total": s.fields_total, "target_url": s.target.url if s.target else None,
                 "field_mapping": s.field_mapping,
                 "created_at": s.created_at.isoformat() if s.created_at else None}
                for s in subs
            ]

            # Recent leads
            leads_q = Lead.query.order_by(Lead.created_at.desc()).limit(15).all()
            leads = []
            for l in leads_q:
                fd = l.submitted_form_data
                # Summarize form data: count filled fields
                filled_count = 0
                if fd:
                    try:
                        import json as _json
                        parsed = _json.loads(fd)
                        filled_count = sum(1 for v in parsed.values() if isinstance(v, dict) and v.get("action") != "skip")
                    except Exception:
                        pass
                leads.append({
                    "id": l.id, "email": l.email, "source_url": l.source_url,
                    "status": l.status, "submission_id": l.submission_id,
                    "target_id": l.target_id,
                    "submitted_message": (l.submitted_message or "")[:120],
                    "submitted_form_data": fd,
                    "form_filled_count": filled_count,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                })

            # Recent targets
            targets = Target.query.order_by(Target.updated_at.desc()).limit(15).all()
            target_list = [
                {"id": t.id, "url": t.url, "status": t.status, "has_form": t.has_form,
                 "updated_at": t.updated_at.isoformat() if t.updated_at else None}
                for t in targets
            ]

            # Stats
            stats = {
                "total_targets": Target.query.count(),
                "total_submissions": Submission.query.count(),
                "total_leads": Lead.query.count(),
                "pending": Target.query.filter_by(status="pending").count(),
                "submitted": Target.query.filter_by(status="submitted").count(),
                "failed": Target.query.filter_by(status="error").count(),
                "skipped": Target.query.filter_by(status="no_form").count(),
            }

            # Logs
            log_lines = []
            log_path = os.path.join("logs", "orbital.log")
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        log_lines = f.read().splitlines()[-40:]
                except Exception:
                    pass

            # Screenshot
            ss_info = {"filename": None, "url": None, "stage": None}
            desktop_path = "static/desktop.png"
            if os.path.exists(desktop_path):
                ss_info = {"filename": "desktop.png", "url": "/static/desktop.png", "stage": "Live Desktop"}
            else:
                ss_dir = "static/screenshots"
                for fname in ["live_current.png", "live_submitted.png", "live_navigating.png"]:
                    fpath = os.path.join(ss_dir, fname)
                    if os.path.exists(fpath):
                        ss_info = {"filename": fname, "url": f"/static/screenshots/{fname}", "stage": fname.replace("live_", "").replace(".png", "").title()}
                        break

            return {
                "status": status,
                "submissions": submissions,
                "leads": leads,
                "targets": target_list,
                "stats": stats,
                "logs": log_lines,
                "screenshot": ss_info,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    return await run_in_threadpool(_query)


@router.get("/api/live/stream")
async def api_live_stream(request: Request):
    _require_admin(request)
    async def event_generator():
        while True:
            try:
                data = await _get_live_data()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/live/all")
async def api_live_all(request: Request):
    _require_admin(request)
    return await _get_live_data()


# ── Real-time Dashboard ─────────────────────────────────────────────────

RT_DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbital Real-Time Processing</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Monaco','Menlo',monospace;background:#0a0e27;color:#e0e0e0;overflow-x:hidden}
.container{max-width:1400px;margin:0 auto;padding:20px}
.header{text-align:center;margin-bottom:40px}
h1{font-size:2em;color:#00ff88;text-shadow:0 0 10px #00ff88;margin-bottom:10px}
.status{display:flex;justify-content:center;gap:30px;margin-top:20px;font-size:14px}
.stat{display:flex;align-items:center;gap:10px}
.stat-value{font-weight:bold;color:#00ff88;font-size:1.3em}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:30px}
@media(max-width:1000px){.grid{grid-template-columns:1fr}}
.card{background:#1a1e3f;border:1px solid #00ff88;border-radius:8px;padding:20px}
.card h2{color:#00ff88;margin-bottom:15px;font-size:1.2em}
.screenshot-container img{width:100%;border-radius:4px;border:1px solid #00ff88;max-height:400px;object-fit:contain}
.screenshot-label{font-size:12px;color:#888;margin-top:8px;text-align:center}
.log-container{max-height:500px;overflow-y:auto;background:#0a0e27;border:1px solid #00ff88;border-radius:4px;padding:12px;font-size:12px;line-height:1.6}
.log-line{margin:4px 0}
.log-processing{color:#00ff88}
.log-detected{color:#00ccff}
.log-filled{color:#ffaa00}
.log-submitted{color:#00ff00}
.log-error{color:#ff3333}
.log-warning{color:#ffff00}
.progress-bar{width:100%;height:8px;background:#333;border-radius:4px;overflow:hidden;margin:10px 0}
.progress-fill{height:100%;background:linear-gradient(90deg,#00ff88,#00ccff);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.7}}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid #00ff88;border-top:2px solid transparent;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
<div class="container">
<div class="header"><h1>Orbital Real-Time Processing</h1>
<div class="status"><div class="stat"><div class="spinner"></div><span>Status: <span id="status">Monitoring...</span></span></div>
<div class="stat">Forms Submitted: <span class="stat-value" id="submitted">0</span></div>
<div class="stat">Domains Processed: <span class="stat-value" id="processed">0</span></div></div>
<div class="progress-bar"><div class="progress-fill" id="progress"></div></div></div>
<div class="grid">
<div class="card"><h2>Latest Screenshot</h2><div class="screenshot-container">
<img id="latestScreenshot" src="/static/screenshots/placeholder.png" alt="Latest" onerror="this.src='/static/placeholder.png'">
<div class="screenshot-label" id="screenshotLabel">Waiting for first submission...</div></div></div>
<div class="card"><h2>Live Logs</h2><div class="log-container" id="logContainer"></div></div></div>
<div class="card"><h2>Submitted Forms</h2><div id="submittedList" style="font-size:13px;line-height:2;max-height:300px;overflow-y:auto;"></div></div></div>
<script>
let logs=[], submitted=[];
function escapeHtml(text){const d=document.createElement('div');d.textContent=text;return d.innerHTML;}
async function updateDashboard(){
  try{
    const r=await fetch('/dashboard/api/status');const d=await r.json();
    document.getElementById('status').textContent=d.status;
    document.getElementById('submitted').textContent=d.submitted_count;
    document.getElementById('processed').textContent=d.processed_count;
    if(d.latest_screenshot){document.getElementById('latestScreenshot').src='/static/screenshots/'+d.latest_screenshot+'?t='+Date.now();document.getElementById('screenshotLabel').textContent='Last: '+d.latest_screenshot;}
    if(d.logs&&d.logs.length>logs.length){logs=d.logs;updateLogs();}
    if(d.submitted&&d.submitted.length>submitted.length){submitted=d.submitted;updateSubmitted();}
  }catch(e){console.error(e);}
}
function updateLogs(){
  const c=document.getElementById('logContainer');const t=logs.slice(-50);
  c.innerHTML=t.map(l=>{let cls='log-line';if(l.includes('submitted'))cls+=' log-submitted';else if(l.includes('Filled'))cls+=' log-filled';else if(l.includes('Detected'))cls+=' log-detected';else if(l.includes('Processing'))cls+=' log-processing';else if(l.includes('ERROR'))cls+=' log-error';else if(l.includes('WARNING'))cls+=' log-warning';return `<div class="${cls}">${escapeHtml(l)}</div>`;}).join('');c.scrollTop=c.scrollHeight;
}
function updateSubmitted(){
  const list=document.getElementById('submittedList');
  list.innerHTML=submitted.slice(-20).reverse().map((s,i)=>`<div>✅ <strong>${i+1}.</strong> <code>${escapeHtml(s.domain)}</code> - ${s.fields_filled}/${s.fields_total} fields</div>`).join('');
}
updateDashboard();setInterval(updateDashboard,1000);
</script></body></html>
"""


@router.get("/dashboard/", response_class=HTMLResponse)
async def dashboard_rt(request: Request):
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(RT_DASHBOARD_HTML)


@router.get("/dashboard/api/status")
async def dashboard_api_status(request: Request):
    if not _admin_logged_in(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    latest_screenshot = None
    if os.path.exists("static/screenshots"):
        files = sorted([f for f in os.listdir("static/screenshots") if f.endswith(".png")])
        if files:
            latest_screenshot = files[-1]

    logs = []
    log_file = os.path.join("logs", "orbital.log")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                logs = [line.strip() for line in lines[-100:] if line.strip()]
        except Exception:
            pass

    def _query():
        with flask_app.app_context():
            # Real submissions from DB
            subs = Submission.query.order_by(Submission.created_at.desc()).limit(20).all()
            submissions_list = [
                {
                    "id": s.id,
                    "domain": s.target.url if s.target else "unknown",
                    "status": s.status,
                    "fields_filled": s.fields_filled,
                    "fields_total": s.fields_total,
                    "screenshot": s.screenshot_path,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in subs
            ]
            
            # Real leads from DB
            leads = Lead.query.order_by(Lead.created_at.desc()).limit(20).all()
            leads_list = [
                {
                    "id": l.id,
                    "email": l.email,
                    "source_url": l.source_url,
                    "status": l.status,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                }
                for l in leads
            ]
            
            # Real targets from DB
            targets = Target.query.order_by(Target.updated_at.desc()).limit(20).all()
            targets_list = [
                {
                    "id": t.id,
                    "url": t.url,
                    "status": t.status,
                    "has_form": t.has_form,
                    "has_captcha": t.has_captcha,
                    "emails_found": t.emails_found,
                }
                for t in targets
            ]
            
            return submissions_list, leads_list, targets_list

    submissions_list, leads_list, targets_list = await run_in_threadpool(_query)

    # Read persistent pipeline stats from DB
    pipe_status = get_pipeline_status()

    return {
        "status": "Running" if pipe_status.get("started_at") else "Idle",
        "latest_screenshot": latest_screenshot,
        "logs": logs,
        "submissions": submissions_list,
        "submitted_count": pipe_status.get("submitted", 0),
        "processed_count": pipe_status.get("processed", 0),
        "leads": leads_list,
        "targets": targets_list,
        "pipeline": pipe_status,
    }


@router.get("/api/screenshots/latest")
async def api_screenshots_latest(request: Request):
    _require_admin(request)
    ss_dir = "static/screenshots"
    
    # PRIORITY 1: Live desktop screenshot from worker Xvfb (scrot)
    desktop_path = "static/desktop.png"
    if os.path.exists(desktop_path):
        desktop_mtime = os.path.getmtime(desktop_path)
        # Return desktop.png as the primary live view
        return {"filename": "desktop.png", "url": "/static/desktop.png", "stage": "Live Desktop", "timestamp": desktop_mtime}
    
    # Fallback: live stage screenshots from pipeline
    live_files = {
        "live_navigating.png": "Navigating...",
        "live_detected.png": "Detecting fields...",
        "live_filling.png": "Filling form...",
        "live_submitted.png": "Submitted!",
    }
    
    best_live = None
    best_stage = None
    best_time = 0
    for fname, stage in live_files.items():
        fpath = os.path.join(ss_dir, fname)
        if os.path.exists(fpath):
            mtime = os.path.getmtime(fpath)
            if mtime > best_time:
                best_time = mtime
                best_live = fname
                best_stage = stage
    
    # Also check regular result screenshots
    result_files = []
    if os.path.exists(ss_dir):
        result_files = sorted(
            [f for f in os.listdir(ss_dir) if f.endswith(".png") and not f.startswith("live_") and f != "placeholder.png"],
            key=lambda x: os.path.getmtime(os.path.join(ss_dir, x))
        )
    
    if result_files:
        latest_result = result_files[-1]
        rtime = os.path.getmtime(os.path.join(ss_dir, latest_result))
        if rtime > best_time:
            return {"filename": latest_result, "url": f"/static/screenshots/{latest_result}", "stage": "Done"}
    
    if best_live:
        return {"filename": best_live, "url": f"/static/screenshots/{best_live}", "stage": best_stage}
    
    return {"filename": None, "url": None, "stage": None}
