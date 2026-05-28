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
from app.browser_selenium import get_pool, shutdown_pool
from app.config import Config
from app.models import FormProof, Lead, PipelineStat, Submission, Target
from app.form_proof import get_proof_detail, list_proofs
from app.pipeline import get_pipeline_status
from app.pipeline_async import run_pipeline_async
from app.tasks import run_pipeline_task

logger = logging.getLogger(__name__)
router = APIRouter()

# Keep references to background tasks to prevent GC
_background_tasks = []

flask_app = create_app()


def _admin_logged_in(request: Request) -> bool:
    if request.session.get("admin") is True:
        return True
    # Optional localhost bypass (disabled in production by default)
    if os.getenv("ADMIN_BYPASS_LOCAL", "false").lower() == "true":
        host = request.headers.get("host", "")
        if host.startswith("127.") or host.startswith("localhost") or host.startswith("192.168."):
            return True
    return False


def _require_admin(request: Request, redirect: bool = False):
    if not _admin_logged_in(request):
        if redirect:
            return None
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── HTML Views ──────────────────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Orbital Login</title><link rel="stylesheet" href="/static/orbital.css"><style>body{display:flex;align-items:center;justify-content:center;height:100vh}</style></head><body><div class="orbital-container" style="max-width:360px;width:100%"><div style="text-align:center;margin-bottom:24px"><div class="orbital-dot" style="margin:0 auto 12px;width:12px;height:12px"></div><h1 style="font-size:20px">Orbital Command</h1><p style="color:var(--text2);font-size:13px">Sign in to continue</p></div><div class="orbital-card"><div class="orbital-card-body" style="padding:24px"><form method="post" action="/login"><label class="orbital-label">Username</label><input class="orbital-input" type="text" name="username" placeholder="admin" required><label class="orbital-label">Password</label><input class="orbital-input" type="password" name="password" placeholder="••••••••" required><button class="orbital-btn orbital-btn-primary" type="submit" style="width:100%;margin-top:16px;padding:10px;font-size:14px">Sign In</button></form>{% if error %}<p style="color:var(--danger);font-size:13px;text-align:center;margin-top:12px">{{ error }}</p>{% endif %}</div></div></div></body></html>
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
<div style="display:flex;gap:16px;align-items:center;margin-bottom:16px;flex-wrap:wrap;">
<a href="/dashboard/" style="color:#00ff88;text-decoration:none;font-size:15px;">🏠 Dashboard</a>
<a href="/live" style="color:#00ff88;text-decoration:none;font-size:15px;">📡 Live Browser</a>
<a href="/history" style="color:#00ff88;text-decoration:none;font-size:15px;">📜 History</a>
<a href="/temporal/ui" style="color:#00ff88;text-decoration:none;font-size:15px;">⏳ Temporal</a>
<a href="/logout" style="color:#ff3333;text-decoration:none;font-size:15px;">🔒 Logout</a>
</div>
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
    return RedirectResponse(url="/dashboard/", status_code=302)

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
async def api_pipeline_start(request: Request, background_tasks: BackgroundTasks):
    _require_admin(request)
    import asyncio
    asyncio.create_task(run_pipeline_async())
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

            # Proofs
            proofs_q = FormProof.query.order_by(FormProof.created_at.desc()).limit(20).all()
            proofs = [
                {
                    "id": p.id,
                    "target_url": p.target_url,
                    "status": p.status,
                    "pre_screenshot": p.pre_screenshot,
                    "post_screenshot": p.post_screenshot,
                    "confirmation_screenshot": p.confirmation_screenshot,
                    "video_path": p.video_path,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                }
                for p in proofs_q
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
            import time
            ss_info = {"filename": None, "url": None, "stage": None}
            desktop_path = "static/desktop.png"
            now = time.time()
            if os.path.exists(desktop_path) and (now - os.path.getmtime(desktop_path)) < 120:
                ss_info = {"filename": "desktop.png", "url": "/static/desktop.png", "stage": "Live Desktop"}
            else:
                ss_dir = "static/screenshots"
                candidates = sorted(
                    [f for f in os.listdir(ss_dir) if f.endswith(".png") and f != "placeholder.png"],
                    key=lambda x: os.path.getmtime(os.path.join(ss_dir, x)),
                    reverse=True
                )
                for fname in candidates[:5]:
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
    from fastapi.responses import StreamingResponse
    import asyncio, json

    async def event_generator():
        while True:
            try:
                data = await _get_live_data()
                payload = json.dumps(data)
                yield "data: " + payload + chr(10) + chr(10)
            except Exception as e:
                payload = json.dumps({'error': str(e)})
                yield "data: " + payload + chr(10) + chr(10)
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/api/live/all")
async def api_live_all(request: Request):
    _require_admin(request)
    return await _get_live_data()


# ── Real-time Dashboard ─────────────────────────────────────────────────

RT_DASHBOARD_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orbital — Real-Time</title><link rel="stylesheet" href="/static/orbital.css">
</head><body><div class="oc-container">
<div class="oc-header"><div class="oc-brand"><div class="oc-dot"></div><h1>Orbital Command</h1></div>
<div class="oc-status live">● Pipeline Running</div></div>
<div class="oc-nav">
<a href="/dashboard/" class="active">Dashboard</a>
<a href="/live">Live</a>
<a href="/history">History</a>
<a href="/admin">Command</a>
<a href="/admin/test-email">Email</a>
<a href="/logout">Logout</a>
</div>
<div class="oc-stats">
<div class="oc-stat"><div class="oc-stat-label">Status</div><div class="oc-stat-value" id="rt-status">—</div></div>
<div class="oc-stat"><div class="oc-stat-label">Submitted</div><div class="oc-stat-value accent" id="rt-submitted">0</div></div>
<div class="oc-stat"><div class="oc-stat-label">Processed</div><div class="oc-stat-value" id="rt-processed">0</div></div>
<div class="oc-stat"><div class="oc-stat-label">Targets</div><div class="oc-stat-value" id="rt-total">0</div></div>
</div>
<div class="oc-grid">
<div class="oc-card"><div class="oc-card-hd"><h2>Latest Screenshot</h2></div>
<div class="oc-card-bd" style="padding:16px"><div class="oc-live-view">
<img id="latestScreenshot" src="/static/screenshots/placeholder.png" alt="Latest" onerror="this.src='/static/placeholder.png'" style="width:100%;max-height:360px;object-fit:contain">
<div class="oc-live-label" id="screenshotLabel">Waiting...</div></div></div></div>
<div class="oc-card"><div class="oc-card-hd"><h2>Live Logs</h2></div><div class="oc-card-bd"><div class="oc-log-panel" id="logContainer"></div></div></div>
</div>
<div class="oc-card oc-full"><div class="oc-card-hd"><h2>Submitted Forms</h2></div>
<div class="oc-card-bd" style="padding:16px"><div id="submittedList" class="oc-pre"></div></div></div>
</div>
<script>let logs=[],submitted=[];
function esc(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML;}
async function up(){
try{const r=await fetch('/dashboard/api/status');const d=await r.json();
document.getElementById('rt-status').textContent=d.status;
document.getElementById('rt-submitted').textContent=d.submitted_count;
document.getElementById('rt-processed').textContent=d.processed_count;
document.getElementById('rt-total').textContent=d.total_targets||0;
if(d.latest_screenshot){document.getElementById('latestScreenshot').src='/static/screenshots/'+d.latest_screenshot+'?t='+Date.now();document.getElementById('screenshotLabel').textContent=d.latest_screenshot;}
if(d.logs&&d.logs.length>logs.length){logs=d.logs;const c=document.getElementById('logContainer');const t=logs.slice(-50);c.innerHTML=t.map(l=>{let cls='oc-log-line';if(l.includes('submitted'))cls+=' oc-log-ok';else if(l.includes('Filled'))cls+=' oc-log-warn';else if(l.includes('Detected'))cls+=' oc-log-info';else if(l.includes('Processing'))cls+=' oc-log-ok';else if(l.includes('ERROR'))cls+=' oc-log-error';return `<div class="${cls}">${esc(l)}</div>`;}).join('');c.scrollTop=c.scrollHeight;}
if(d.submitted&&d.submitted.length>submitted.length){submitted=d.submitted;document.getElementById('submittedList').innerHTML=submitted.slice(-20).reverse().map((s,i)=>`<div>✅ <strong>${i+1}.</strong> <code>${esc(s.domain)}</code> — ${s.fields_filled}/${s.fields_total} fields</div>`).join('');}
}catch(e){console.error(e);}}
up();setInterval(up,1000);
</script></body></html>
"""


@router.get("/dashboard/", response_class=HTMLResponse)
async def dashboard_rt(request: Request):
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)

    def _query():
        with flask_app.app_context():
            targets = Target.query.order_by(Target.id.desc()).limit(50).all()
            submissions = Submission.query.order_by(Submission.id.desc()).limit(50).all()
            leads = Lead.query.order_by(Lead.id.desc()).limit(100).all()
            stats = PipelineStat.query.order_by(PipelineStat.id.desc()).first()
            return targets, submissions, leads, stats

    targets, submissions, leads, stats = await run_in_threadpool(_query)
    pipe = get_pipeline_status()

    # Screenshot gallery
    ss_dir = "static/screenshots"
    screenshots = []
    if os.path.exists(ss_dir):
        files = sorted(
            [f for f in os.listdir(ss_dir) if f.endswith(".png") and f != "placeholder.png"],
            key=lambda x: os.path.getmtime(os.path.join(ss_dir, x)),
            reverse=True,
        )
        for fname in files[:20]:
            fpath = os.path.join(ss_dir, fname)
            screenshots.append({"name": fname, "url": f"/static/screenshots/{fname}", "mtime": os.path.getmtime(fpath)})

    # Logs
    logs = []
    log_path = os.path.join("logs", "orbital.log")
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                logs = [line.strip() for line in f.readlines()[-50:] if line.strip()]
        except Exception:
            pass

    # Latest screenshot for live view
    latest_ss = None
    if screenshots:
        latest_ss = screenshots[0]

    # DocSend pending count
    doc_pending = sum(1 for l in leads if not getattr(l, "doc_send_sent_at", None))

    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "targets": targets,
        "submissions": submissions,
        "leads": leads,
        "stats": stats,
        "pipeline": pipe,
        "pipeline_json": json.dumps(pipe, indent=2, default=str),
        "screenshots": screenshots,
        "latest_screenshot": latest_ss,
        "logs": logs,
        "doc_pending": doc_pending,
        "smtp_host": Config.SMTP_HOST or "",
        "smtp_port": Config.SMTP_PORT,
        "smtp_user": Config.SMTP_USER or "",
        "smtp_enabled": bool(Config.SMTP_HOST and Config.SMTP_USER),
        "doc_send_link": Config.DOC_SEND_LINK or "",
        "sender_name": f"{Config.COMPANY_DATA.get('first_name', '')} {Config.COMPANY_DATA.get('last_name', '')}".strip(),
        "sender_title": Config.COMPANY_DATA.get("job_title", "Head of Partnerships"),
        "company_url": Config.COMPANY_DATA.get("company_url", ""),
        "company_phone": Config.COMPANY_DATA.get("phone", ""),
    })

@router.get("/dashboard/api/status")
async def dashboard_api_status(request: Request):
    if not _admin_logged_in(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    latest_screenshot = None
    if os.path.exists("static/screenshots"):
        files = sorted(
            [f for f in os.listdir("static/screenshots") if f.endswith(".png") and f != "placeholder.png"],
            key=lambda x: os.path.getmtime(os.path.join("static/screenshots", x)),
            reverse=True
        )
        if files:
            latest_screenshot = files[0]

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

    # Real counters from DB (not stale PipelineStat from legacy worker)
    with flask_app.app_context():
        submitted_count = Target.query.filter_by(status="submitted").count()
        processed_count = Target.query.filter(Target.status.in_(["submitted", "no_form", "error", "analyzed", "submit_not_found", "timeout", "no_fields"])).count()
        total_targets = Target.query.count()
        total_submissions = Submission.query.count()

    return {
        "status": "Running" if processed_count > 0 else "Idle",
        "latest_screenshot": latest_screenshot,
        "logs": logs,
        "submissions": submissions_list,
        "submitted_count": submitted_count,
        "processed_count": processed_count,
        "total_targets": total_targets,
        "total_submissions": total_submissions,
        "leads": leads_list,
        "targets": targets_list,
    }


@router.get("/api/screenshots/latest")
async def api_screenshots_latest(request: Request):
    _require_admin(request)
    ss_dir = "static/screenshots"
    
    # PRIORITY 1: Live desktop screenshot from worker Xvfb (scrot)
    # Only use desktop.png if it was updated within the last 2 minutes
    import time
    desktop_path = "static/desktop.png"
    now = time.time()
    if os.path.exists(desktop_path):
        desktop_mtime = os.path.getmtime(desktop_path)
        if now - desktop_mtime < 120:
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
            key=lambda x: os.path.getmtime(os.path.join(ss_dir, x)),
            reverse=True
        )
    
    if result_files:
        latest_result = result_files[-1]
        rtime = os.path.getmtime(os.path.join(ss_dir, latest_result))
        if rtime > best_time:
            return {"filename": latest_result, "url": f"/static/screenshots/{latest_result}", "stage": "Done"}
    
    if best_live:
        return {"filename": best_live, "url": f"/static/screenshots/{best_live}", "stage": best_stage}
    
    return {"filename": None, "url": None, "stage": None}


@router.get("/api/proofs")
async def api_list_proofs(request: Request, limit: int = 50, status: str = None):
    _require_admin(request)
    return list_proofs(limit=limit, status=status)


@router.get("/api/proofs/{proof_id}")
async def api_proof_detail(request: Request, proof_id: int):
    _require_admin(request)
    detail = get_proof_detail(proof_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Proof not found")
    return detail


@router.get("/api/proofs/{proof_id}/screenshots")
async def api_proof_screenshots(request: Request, proof_id: int):
    _require_admin(request)
    detail = get_proof_detail(proof_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Proof not found")
    return {
        "proof_id": proof_id,
        "target_url": detail["target_url"],
        "status": detail["status"],
        "screenshots": {
            "pre": detail["pre_screenshot"],
            "filling": detail["filling_screenshot"],
            "post": detail["post_screenshot"],
            "confirmation": detail["confirmation_screenshot"],
        },
        "submitted_message": detail["submitted_message"],
        "actual_values": detail["actual_values"],
        "ai_mapping": detail["ai_mapping"],
        "final_url": detail["final_url"],
    }





# ── DocSend Email Routes ────────────────────────────────────────────────────

from app.email_sender import get_email_sender
from datetime import datetime, timezone


@router.post("/api/leads/{lead_id}/send-doc-send")
async def api_send_doc_send(request: Request, lead_id: int, background_tasks: BackgroundTasks = None):
    """Send the pre-interaction DocSend framework email to a lead.

    This must be sent BEFORE any form submission, call, or data exchange.
    """
    _require_admin(request)

    def _send():
        with flask_app.app_context():
            lead = db.session.get(Lead, lead_id)
            if not lead:
                return {"ok": False, "error": "Lead not found"}
            if not lead.email:
                return {"ok": False, "error": "Lead has no email"}

            sender = get_email_sender()
            result = sender.send_doc_send_email(
                to_email=lead.email,
                to_name=lead.name or "",
            )

            if result["sent"]:
                lead.doc_send_sent_at = datetime.now(timezone.utc)
                lead.onboarding_status = "doc_send_sent"
                db.session.commit()

            return {"ok": result["sent"], "lead_id": lead_id, "email": lead.email, "error": result.get("error")}

    return await run_in_threadpool(_send)


@router.post("/api/leads/{lead_id}/doc-send-opened")
async def api_doc_send_opened(request: Request, lead_id: int):
    """Webhook / manual update: DocSend document was opened by recipient."""
    _require_admin(request)

    def _update():
        with flask_app.app_context():
            lead = db.session.get(Lead, lead_id)
            if not lead:
                return {"ok": False, "error": "Lead not found"}
            lead.doc_send_opened_at = datetime.now(timezone.utc)
            if lead.onboarding_status in ("doc_send_pending", "doc_send_sent"):
                lead.onboarding_status = "doc_send_opened"
            db.session.commit()
            return {"ok": True, "lead_id": lead_id, "status": lead.onboarding_status}

    return await run_in_threadpool(_update)


@router.post("/api/leads/{lead_id}/doc-send-downloaded")
async def api_doc_send_downloaded(request: Request, lead_id: int):
    """Webhook / manual update: DocSend document was downloaded by recipient."""
    _require_admin(request)

    def _update():
        with flask_app.app_context():
            lead = db.session.get(Lead, lead_id)
            if not lead:
                return {"ok": False, "error": "Lead not found"}
            lead.doc_send_downloaded_at = datetime.now(timezone.utc)
            lead.onboarding_status = "doc_send_downloaded"
            db.session.commit()
            return {"ok": True, "lead_id": lead_id, "status": lead.onboarding_status}

    return await run_in_threadpool(_update)


@router.get("/api/leads/onboarding-status")
async def api_leads_onboarding_status(request: Request, status: str = None):
    """List leads filtered by onboarding status (doc_send_pending, doc_send_sent, doc_send_opened, doc_send_downloaded)."""
    _require_admin(request)

    def _query():
        with flask_app.app_context():
            q = Lead.query
            if status:
                q = q.filter_by(onboarding_status=status)
            leads = q.order_by(Lead.id.desc()).limit(200).all()
            return [
                {
                    "id": l.id,
                    "email": l.email,
                    "name": l.name,
                    "company": l.company,
                    "source_url": l.source_url,
                    "status": l.status,
                    "onboarding_status": l.onboarding_status,
                    "doc_send_sent_at": l.doc_send_sent_at.isoformat() if l.doc_send_sent_at else None,
                    "doc_send_opened_at": l.doc_send_opened_at.isoformat() if l.doc_send_opened_at else None,
                    "doc_send_downloaded_at": l.doc_send_downloaded_at.isoformat() if l.doc_send_downloaded_at else None,
                    "created_at": l.created_at.isoformat() if l.created_at else None,
                }
                for l in leads
            ]

    return await run_in_threadpool(_query)


# ── Admin Leads Dashboard (DocSend tracking) ────────────────────────────────

@router.get("/leads-dashboard", response_class=HTMLResponse)
async def leads_dashboard(request: Request):
    """Admin dashboard showing leads with DocSend onboarding tracking."""
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)

    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")

    def _query():
        with flask_app.app_context():
            targets = Target.query.order_by(Target.id.desc()).limit(25).all()
            submissions = Submission.query.order_by(Submission.id.desc()).limit(25).all()
            leads = Lead.query.order_by(Lead.id.desc()).limit(200).all()
            return targets, submissions, leads

    targets, submissions, leads = await run_in_threadpool(_query)
    pipe = get_pipeline_status()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "targets": targets,
        "submissions": submissions,
        "leads": leads,
        "pipeline": pipe,
        "pipeline_json": json.dumps(pipe, indent=2, default=str),
    })


# ── Simple Admin Dashboard (no Jinja2 dependency issues) ─────────────────────

# ── Grok-Style Admin Dashboard ─────────────────────────────────────────────

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbital — Command Center</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0a0a;--surface:#111111;--surface2:#171717;--border:#1f1f1f;--text:#e5e5e5;--text2:#a3a3a3;--accent:#00ff88;--accent2:#00cc6a;--danger:#ff4444;--warning:#ffaa00;--info:#00ccff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;line-height:1.5}
.container{max-width:1400px;margin:0 auto;padding:24px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;flex-wrap:wrap;gap:16px}
.brand{display:flex;align-items:center;gap:12px}
.brand h1{font-size:22px;font-weight:700;letter-spacing:-0.5px;color:var(--text)}
.brand .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 12px var(--accent);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.status-pill{font-size:12px;font-weight:500;padding:4px 12px;border-radius:20px;background:var(--surface2);border:1px solid var(--border);color:var(--text2)}
.status-pill.live{color:var(--accent);border-color:var(--accent)33;background:var(--accent)10}
.nav{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.nav a{font-size:13px;font-weight:500;color:var(--text2);text-decoration:none;padding:8px 14px;border-radius:8px;background:var(--surface);border:1px solid var(--border);transition:all .15s}
.nav a:hover{color:var(--accent);border-color:var(--accent)40}
.nav a.active{color:var(--accent);background:var(--accent)10;border-color:var(--accent)40}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:32px}
@media(max-width:900px){.stats-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:500px){.stats-grid{grid-template-columns:1fr}}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;position:relative;overflow:hidden}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),transparent);opacity:.3}
.stat-label{font-size:12px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.stat-value{font-size:32px;font-weight:700;color:var(--text);letter-spacing:-1px}
.stat-value.accent{color:var(--accent)}
.stat-value.danger{color:var(--danger)}
.stat-value.warning{color:var(--warning)}
.stat-delta{font-size:12px;color:var(--text2);margin-top:4px}
.main-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:1000px){.main-grid{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.card-header{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border)}
.card-title{font-size:14px;font-weight:600;color:var(--text);letter-spacing:.2px}
.card-count{font-size:12px;font-weight:500;color:var(--text2);background:var(--surface2);padding:2px 10px;border-radius:12px}
.card-body{padding:0;max-height:420px;overflow-y:auto}
.card-body::-webkit-scrollbar{width:6px}
.card-body::-webkit-scrollbar-track{background:transparent}
.card-body::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.fullwidth{grid-column:1 / -1}
table{width:100%;border-collapse:collapse;font-size:13px}
th{padding:10px 16px;text-align:left;font-weight:500;color:var(--text2);font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--surface);z-index:1}
td{padding:12px 16px;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle}
tr:hover td{background:var(--surface2)}
td a{color:var(--accent);text-decoration:none;font-weight:500}
td a:hover{text-decoration:underline}
.url-cell{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--text2)}
.url-cell a{color:var(--text2)}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.3px}
.badge-dot{width:6px;height:6px;border-radius:50%}
.badge-pending{background:var(--warning)15;color:var(--warning);border:1px solid var(--warning)30}
.badge-pending .badge-dot{background:var(--warning)}
.badge-submitted{background:var(--accent)15;color:var(--accent);border:1px solid var(--accent)30}
.badge-submitted .badge-dot{background:var(--accent)}
.badge-error{background:var(--danger)15;color:var(--danger);border:1px solid var(--danger)30}
.badge-error .badge-dot{background:var(--danger)}
.badge-noform{background:var(--surface2);color:var(--text2);border:1px solid var(--border)}
.badge-noform .badge-dot{background:var(--text2)}
.badge-analyzed{background:var(--info)15;color:var(--info);border:1px solid var(--info)30}
.badge-analyzed .badge-dot{background:var(--info)}
.actions{display:flex;gap:6px;flex-wrap:nowrap}
.btn{font-size:11px;font-weight:600;padding:5px 12px;border-radius:6px;border:none;cursor:pointer;transition:all .12s;white-space:nowrap}
.btn:hover{transform:translateY(-1px)}
.btn-primary{background:var(--accent);color:var(--bg)}
.btn-primary:hover{background:var(--accent2)}
.btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--border)}
.btn-ghost:hover{color:var(--accent);border-color:var(--accent)40}
.empty{padding:40px;text-align:center;color:var(--text2);font-size:13px}
.empty-icon{font-size:32px;margin-bottom:8px;opacity:.5}
.scroll-hint{font-size:11px;color:var(--text2);padding:8px 16px;border-top:1px solid var(--border);text-align:center;opacity:.6}
</style>
</head>
<body>
<div class="container">
<div class="header">
<div class="brand">
<div class="dot"></div>
<h1>Orbital Command</h1>
</div>
<div class="status-pill live">● Pipeline Running</div>
</div>

<div class="nav">
<a href="/dashboard/">Dashboard</a>
<a href="/live">Live Browser</a>
<a href="/history">History</a>
<a href="/admin/test-email">Email Test</a>
<a href="/logout">Logout</a>
</div>

<div class="stats-grid">
<div class="stat-card"><div class="stat-label">Total Targets</div><div class="stat-value accent">{{TOTAL_TARGETS}}</div><div class="stat-delta">Boutique hedge funds, family offices, allocators</div></div>
<div class="stat-card"><div class="stat-label">Forms Submitted</div><div class="stat-value">{{TOTAL_SUBMISSIONS}}</div><div class="stat-delta">Successfully filled &amp; sent</div></div>
<div class="stat-card"><div class="stat-label">Leads Captured</div><div class="stat-value accent">{{TOTAL_LEADS}}</div><div class="stat-delta">Emails extracted from targets</div></div>
<div class="stat-card"><div class="stat-label">DocSend Pending</div><div class="stat-value warning">{{DOC_SEND_PENDING}}</div><div class="stat-delta">Ready to send framework doc</div></div>
</div>

<div class="main-grid">
<div class="card">
<div class="card-header"><span class="card-title">Targets &amp; Form Status</span><span class="card-count">{{TARGET_COUNT}} total</span></div>
<div class="card-body">
<table><thead><tr><th>Domain</th><th>Status</th><th>Fields</th><th>Screenshot</th></tr></thead><tbody>{{TARGET_ROWS}}</tbody></table>
{{TARGET_EMPTY}}
</div>
</div>

<div class="card">
<div class="card-header"><span class="card-title">Leads &amp; Source Domains</span><span class="card-count">{{LEAD_COUNT}} captured</span></div>
<div class="card-body">
<table><thead><tr><th>Email</th><th>Source Domain</th><th>Onboarding</th><th>Actions</th></tr></thead><tbody>{{LEAD_ROWS}}</tbody></table>
{{LEAD_EMPTY}}
</div>
</div>
</div>

<div class="card fullwidth">
<div class="card-header"><span class="card-title">Form Submissions Detail</span><span class="card-count">{{SUBMISSION_COUNT}} submissions</span></div>
<div class="card-body">
<table><thead><tr><th>ID</th><th>Target Domain</th><th>Status</th><th>Fields Filled</th><th>Screenshot</th><th>Time</th></tr></thead><tbody>{{SUBMISSION_ROWS}}</tbody></table>
{{SUBMISSION_EMPTY}}
</div>
</div>

</div>

<script>
async function sendDocSend(leadId){
  if(!confirm('Send DocSend framework email to lead #'+leadId+'?')) return;
  const r=await fetch('/api/leads/'+leadId+'/send-doc-send',{method:'POST'});
  const d=await r.json();
  alert(d.ok ? 'DocSend email sent.' : 'Error: '+(d.error||'Unknown'));
  if(d.ok) location.reload();
}
async function markOpened(leadId){
  const r=await fetch('/api/leads/'+leadId+'/doc-send-opened',{method:'POST'});
  const d=await r.json();
  if(d.ok) location.reload();
}
async function markDownloaded(leadId){
  if(!confirm('Mark lead #'+leadId+' as DocSend DOWNLOADED?')) return;
  const r=await fetch('/api/leads/'+leadId+'/doc-send-downloaded',{method:'POST'});
  const d=await r.json();
  if(d.ok) location.reload();
}
setInterval(()=>location.reload(),10000);
</script>
</body></html>
"""


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Redirect to unified dashboard."""
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard/", status_code=302)

# Legacy admin data builder preserved for reference
async def _admin_dashboard_old(request: Request):
    """Grok-style admin dashboard — targets, leads, submissions, DocSend."""
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)

    def _query():
        with flask_app.app_context():
            targets = Target.query.order_by(Target.id.desc()).limit(100).all()
            submissions = Submission.query.order_by(Submission.id.desc()).limit(50).all()
            leads = Lead.query.order_by(Lead.id.desc()).limit(200).all()
            return targets, submissions, leads

    targets, submissions, leads = await run_in_threadpool(_query)

    # Build target rows
    target_rows = []
    for t in targets:
        # Find matching submission
        sub = next((s for s in submissions if s.target_id == t.id), None)
        fields = f"{sub.fields_filled}/{sub.fields_total}" if sub else "—"
        ss = f'<a href="/{sub.screenshot_path}" target="_blank">View</a>' if sub and sub.screenshot_path else "—"
        badge = _status_badge(t.status)
        target_rows.append(
            f'<tr><td class="url-cell"><a href="{t.url}" target="_blank">{t.url[:55]}{"..." if len(t.url)>55 else ""}</a></td>'
            f'<td>{badge}</td>'
            f'<td>{fields}</td>'
            f'<td>{ss}</td></tr>'
        )

    # Build lead rows
    lead_rows = []
    for l in leads:
        status = l.onboarding_status or "doc_send_pending"
        badge = _onboarding_badge(status)
        actions = ""
        if not l.doc_send_sent_at:
            actions += f'<button class="btn btn-primary" onclick="sendDocSend({l.id})">Send DocSend</button>'
        if status == "doc_send_sent":
            actions += f'<button class="btn btn-ghost" onclick="markOpened({l.id})">Opened</button>'
        if status in ("doc_send_sent", "doc_send_opened"):
            actions += f'<button class="btn btn-primary" style="margin-left:4px" onclick="markDownloaded({l.id})">Downloaded</button>'
        lead_rows.append(
            f'<tr><td><a href="mailto:{l.email}">{l.email or "—"}</a></td>'
            f'<td class="url-cell"><a href="{l.source_url or "#"}" target="_blank">{l.source_url[:40] if l.source_url else "—"}{"..." if l.source_url and len(l.source_url)>40 else ""}</a></td>'
            f'<td>{badge}</td>'
            f'<td><div class="actions">{actions}</div></td></tr>'
        )

    # Build submission rows
    submission_rows = []
    for s in submissions:
        t = next((x for x in targets if x.id == s.target_id), None)
        domain = t.url[:50] + "..." if t and len(t.url) > 50 else (t.url if t else "—")
        badge = _status_badge(s.status)
        ss = f'<a href="/{s.screenshot_path}" target="_blank">View</a>' if s.screenshot_path else "—"
        time_str = s.created_at.strftime("%H:%M %d/%m") if s.created_at else "—"
        submission_rows.append(
            f'<tr><td>#{s.id}</td>'
            f'<td class="url-cell">{domain}</td>'
            f'<td>{badge}</td>'
            f'<td>{s.fields_filled}/{s.fields_total}</td>'
            f'<td>{ss}</td>'
            f'<td>{time_str}</td></tr>'
        )

    doc_pending = sum(1 for l in leads if not l.doc_send_sent_at)

    html = ADMIN_DASHBOARD_HTML
    html = html.replace("{{TOTAL_TARGETS}}", str(len(targets)))
    html = html.replace("{{TOTAL_SUBMISSIONS}}", str(len(submissions)))
    html = html.replace("{{TOTAL_LEADS}}", str(len(leads)))
    html = html.replace("{{DOC_SEND_PENDING}}", str(doc_pending))
    html = html.replace("{{TARGET_COUNT}}", str(len(targets)))
    html = html.replace("{{TARGET_ROWS}}", "".join(target_rows) if target_rows else '<tr><td colspan="4" class="empty"><div class="empty-icon">🎯</div>No targets yet — pipeline is discovering</td></tr>')
    html = html.replace("{{LEAD_COUNT}}", str(len(leads)))
    html = html.replace("{{LEAD_ROWS}}", "".join(lead_rows) if lead_rows else '<tr><td colspan="4" class="empty"><div class="empty-icon">📧</div>No leads captured yet</td></tr>')
    html = html.replace("{{SUBMISSION_COUNT}}", str(len(submissions)))
    html = html.replace("{{SUBMISSION_ROWS}}", "".join(submission_rows) if submission_rows else '<tr><td colspan="6" class="empty"><div class="empty-icon">📝</div>No submissions yet</td></tr>')
    return HTMLResponse(html)


def _status_badge(status: str) -> str:
    """Render a status badge HTML."""
    s = (status or "pending").lower()
    if s == "submitted":
        return '<span class="badge badge-submitted"><span class="badge-dot"></span>Submitted</span>'
    if s == "error":
        return '<span class="badge badge-error"><span class="badge-dot"></span>Error</span>'
    if s == "no_form":
        return '<span class="badge badge-noform"><span class="badge-dot"></span>No Form</span>'
    if s == "analyzed":
        return '<span class="badge badge-analyzed"><span class="badge-dot"></span>Analyzed</span>'
    if s in ("timeout", "no_fields"):
        return '<span class="badge badge-error"><span class="badge-dot"></span>' + s.replace("_", " ").title() + '</span>'
    return '<span class="badge badge-pending"><span class="badge-dot"></span>Pending</span>'


def _onboarding_badge(status: str) -> str:
    s = (status or "doc_send_pending").lower()
    if s == "doc_send_downloaded":
        return '<span class="badge badge-submitted"><span class="badge-dot"></span>Downloaded</span>'
    if s == "doc_send_opened":
        return '<span class="badge badge-analyzed"><span class="badge-dot"></span>Opened</span>'
    if s == "doc_send_sent":
        return '<span class="badge badge-analyzed"><span class="badge-dot"></span>Sent</span>'
    return '<span class="badge badge-pending"><span class="badge-dot"></span>Pending</span>'



# ── Email Test Dashboard ─────────────────────────────────────────────────────

EMAIL_TEST_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orbital — Email Test</title><link rel="stylesheet" href="/static/orbital.css">
</head><body><div class="oc-container">
<div class="oc-header"><div class="oc-brand"><div class="oc-dot"></div><h1>Orbital Command</h1></div>
<div class="oc-status live">● Pipeline Running</div></div>
<div class="oc-nav">
<a href="/dashboard/">Dashboard</a>
<a href="/live">Live</a>
<a href="/history">History</a>
<a href="/admin">Command</a>
<a href="/admin/test-email" class="active">Email</a>
<a href="/logout">Logout</a>
</div>
<div style="margin-bottom:24px"><h2 style="font-size:20px;font-weight:700">📧 Email Test Lab</h2><p style="color:var(--text-secondary);font-size:13px">Preview and test the DocSend framework email before sending to leads.</p></div>
<div class="oc-card" style="margin-bottom:16px"><div class="oc-card-bd" style="padding:22px">
<p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px"><strong style="color:var(--text)">SMTP Host:</strong> {{SMTP_HOST}} | <strong style="color:var(--text)">Port:</strong> {{SMTP_PORT}} | <strong style="color:var(--text)">User:</strong> {{SMTP_USER}} | <strong style="color:var(--text)">Enabled:</strong> {{SMTP_ENABLED}}</p>
<p style="font-size:13px;color:var(--text-secondary)"><strong style="color:var(--text)">DocSend Link:</strong> <a href="{{DOC_SEND_LINK}}" target="_blank">{{DOC_SEND_LINK}}</a></p>
</div></div>
<div class="oc-card" style="margin-bottom:16px"><div class="oc-card-bd" style="padding:22px">
<label class="oc-label">Recipient Email</label><input class="oc-input" type="email" id="testEmail" placeholder="test@example.com">
<label class="oc-label">Recipient Name (optional)</label><input class="oc-input" type="text" id="testName" placeholder="Ashley Whittaker">
<div style="margin-top:16px;display:flex;gap:8px">
<button class="oc-btn oc-btn--primary" onclick="previewEmail()">👁 Preview</button>
<button class="oc-btn oc-btn--ghost" onclick="sendTest()">🚀 Send Test</button>
</div>
<div id="result" style="margin-top:14px;font-size:13px;font-weight:600"></div>
</div></div>
<div class="oc-card" id="previewCard" style="display:none"><div class="oc-card-bd" style="padding:22px">
<h3 style="font-size:14px;font-weight:600;margin-bottom:12px;color:var(--text-secondary)">Email Preview</h3>
<div id="previewBody" style="background:var(--surface-raised);border:1px solid var(--border);border-radius:var(--radius-sm);padding:18px;font-family:Georgia,serif;font-size:14px;line-height:1.7;color:var(--text);white-space:pre-wrap"></div>
</div></div>
</div>
<script>
const DOC_SEND_LINK="{{DOC_SEND_LINK}}";const SENDER_NAME="{{SENDER_NAME}}";const SENDER_TITLE="{{SENDER_TITLE}}";const COMPANY_URL="{{COMPANY_URL}}";const COMPANY_PHONE="{{COMPANY_PHONE}}";
function buildEmail(name){return `Dear ${name||"there"},\n\nThank you for your message and for the overview shared.\n\nTo move forward efficiently, our standard onboarding process requires a reviewed framework document before any technical integration, data exchange, or joint roadmap discussion. This ensures both parties are aligned on confidentiality, liability, and operational boundaries from day one.\n\nI have prepared the enclosed secure document for your review via DocSend:\n\n${DOC_SEND_LINK}\n\nThe document covers:\n  • Mutual NDA and data handling terms\n  • Collaboration scope and exclusion clauses\n  • Single point of contact and escalation paths\n\nOnce reviewed and downloaded, we will immediately share access to our technical sandbox and schedule the kick-off call with our engineering and compliance leads.\n\nPlease let us know if your legal team requires any redlines — we typically turn around revisions within 24 hours.\n\nBest regards,\n\n${SENDER_NAME}\n${SENDER_TITLE}\nOrbitalTech\n${COMPANY_URL} | ${COMPANY_PHONE}`;}
function previewEmail(){const name=document.getElementById('testName').value;document.getElementById('previewCard').style.display='block';document.getElementById('previewBody').textContent=buildEmail(name);window.scrollTo(0,document.body.scrollHeight);}
async function sendTest(){const email=document.getElementById('testEmail').value;const name=document.getElementById('testName').value;if(!email){alert('Please enter a test email');return;}if(!confirm(`Send DocSend test email to: ${email}?`))return;const r=await fetch('/api/test/send-doc-send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,name})});const d=await r.json();const res=document.getElementById('result');res.style.color=d.ok?'var(--accent)':'var(--danger)';res.textContent=d.ok?`✓ Test email sent to ${email}`:`✕ Error: ${d.error||'Unknown'}`;}
</script></body></html>
"""



@router.get("/admin/test-email", response_class=HTMLResponse)
async def email_test_page(request: Request):
    """Redirect to unified email tab."""
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard/?tab=email", status_code=302)

async def _email_test_page_old(request: Request):
    """Email test lab — preview and send test DocSend emails."""
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)

    company = Config.COMPANY_DATA
    sender_name = f"{company.get('first_name', '')} {company.get('last_name', '')}".strip()
    sender_title = company.get("job_title", "Head of Partnerships")

    html = EMAIL_TEST_HTML
    html = html.replace("{{SMTP_HOST}}", Config.SMTP_HOST or "(not set)")
    html = html.replace("{{SMTP_PORT}}", str(Config.SMTP_PORT))
    html = html.replace("{{SMTP_USER}}", Config.SMTP_USER or "(not set)")
    html = html.replace("{{SMTP_ENABLED}}", "Yes" if (Config.SMTP_HOST and Config.SMTP_USER) else "No")
    html = html.replace("{{DOC_SEND_LINK}}", Config.DOC_SEND_LINK or "(not set)")
    html = html.replace("{{SENDER_NAME}}", sender_name)
    html = html.replace("{{SENDER_TITLE}}", sender_title)
    html = html.replace("{{COMPANY_URL}}", company.get("company_url", ""))
    html = html.replace("{{COMPANY_PHONE}}", company.get("phone", ""))
    return HTMLResponse(html)


@router.post("/api/test/send-doc-send")
async def api_test_send_doc_send(request: Request):
    """Send a test DocSend email to a specified address."""
    _require_admin(request)
    payload = await request.json()
    to_email = payload.get("email", "").strip()
    to_name = payload.get("name", "").strip()

    if not to_email:
        raise HTTPException(status_code=400, detail="email required")

    sender = get_email_sender()
    result = sender.send_doc_send_email(to_email=to_email, to_name=to_name)

    # Log test to stdout / logs regardless of SMTP state
    logger.info("TEST DocSend email to=%s sent=%s error=%s", to_email, result["sent"], result.get("error"))

    return {"ok": result["sent"], "email": to_email, "error": result.get("error")}
