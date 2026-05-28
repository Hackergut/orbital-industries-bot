"""History view — screenshots gallery, full submissions, full logs, CSV export."""
import csv
import io
import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app import create_app
from app.models import Submission, Target

router = APIRouter()

def _admin_logged_in(request) -> bool:
    if request.session.get("admin") is True:
        return True
    host = request.headers.get("host", "")
    if host.startswith("127.") or host.startswith("localhost") or host.startswith("192.168."):
        return True
    return False
flask_app = create_app()

HISTORY_HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orbital — History</title><link rel="stylesheet" href="/static/orbital.css">
<style>.oc-gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;padding:18px}
.oc-gallery-item{background:var(--surface-raised);border:1px solid var(--border);border-radius:var(--radius-md);overflow:hidden;cursor:pointer;transition:var(--transition)}
.oc-gallery-item:hover{border-color:var(--accent);transform:translateY(-2px)}
.oc-gallery-item img{width:100%;height:170px;object-fit:cover;display:block}
.oc-gallery-meta{padding:10px 12px;font-size:11px;color:var(--text-muted)}
</style></head><body><div class="oc-container">
<div class="oc-header"><div class="oc-brand"><div class="oc-dot"></div><h1>Orbital Command</h1></div>
<div class="oc-status live">● Pipeline Running</div></div>
<div class="oc-nav">
<a href="/dashboard/">Dashboard</a>
<a href="/live">Live</a>
<a href="/history" class="active">History</a>
<a href="/admin">Command</a>
<a href="/admin/test-email">Email</a>
<a href="/logout">Logout</a>
</div>
<div class="oc-card" style="margin-bottom:16px"><div class="oc-card-hd"><h2>📸 Screenshots Gallery</h2></div><div class="oc-gallery" id="gallery">Loading...</div></div>
<div class="oc-card" style="margin-bottom:16px"><div class="oc-card-hd"><h2>📋 Full Submissions</h2><button class="oc-btn oc-btn--primary" onclick="downloadCsv()">⬇ Download CSV</button></div><div class="oc-card-bd">
<table class="oc-table"><thead><tr><th>ID</th><th>Target</th><th>Status</th><th>Fields</th><th>Form Data</th><th>Time</th></tr></thead><tbody id="submissionsBody">Loading...</tbody></table></div></div>
<div class="oc-card"><div class="oc-card-hd"><h2>📝 Full Logs</h2></div><div class="oc-card-bd"><div class="oc-log-panel" id="logContainer">Loading...</div></div></div>
</body></html>
"""


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard/?tab=history", status_code=302)


@router.get("/api/screenshots")
async def api_screenshots(request: Request, limit: int = 40):
    if not _admin_logged_in(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    ss_dir = "static/screenshots"
    result = []
    if os.path.exists(ss_dir):
        files = sorted(
            [f for f in os.listdir(ss_dir) if f.endswith(".png") and f != "placeholder.png"],
            key=lambda x: os.path.getmtime(os.path.join(ss_dir, x)),
            reverse=True,
        )
        def _map():
            with flask_app.app_context():
                subs = Submission.query.filter(Submission.screenshot_path != None).order_by(Submission.id.desc()).limit(200).all()
                mapping = {}
                for s in subs:
                    if s.screenshot_path:
                        fname = os.path.basename(s.screenshot_path)
                        mapping[fname] = s.target.url if s.target else None
                return mapping
        url_map = await run_in_threadpool(_map)
        for fname in files[:limit]:
            fpath = os.path.join(ss_dir, fname)
            result.append({
                "filename": fname,
                "url": f"/static/screenshots/{fname}",
                "mtime": os.path.getmtime(fpath),
                "target_url": url_map.get(fname),
            })
    return {"screenshots": result}


@router.get("/api/logs/full")
async def api_logs_full(request: Request, lines: int = 200):
    if not _admin_logged_in(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    log_path = os.path.join("logs", "orbital.log")
    if not os.path.exists(log_path):
        return {"lines": []}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read().splitlines()
        return {"lines": data[-lines:]}
    except Exception:
        return {"lines": []}


@router.get("/api/submissions/export")
async def api_submissions_export(request: Request):
    if not _admin_logged_in(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    def _build_csv():
        with flask_app.app_context():
            subs = Submission.query.order_by(Submission.id.desc()).all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "id", "target_url", "target_id", "status",
                "fields_filled", "fields_total", "final_url",
                "field_mapping_json", "screenshot_path", "created_at"
            ])
            for s in subs:
                writer.writerow([
                    s.id,
                    s.target.url if s.target else "",
                    s.target_id or "",
                    s.status,
                    s.fields_filled or 0,
                    s.fields_total or 0,
                    s.final_url or "",
                    s.field_mapping or "",
                    s.screenshot_path or "",
                    s.created_at.isoformat() if s.created_at else "",
                ])
            return output.getvalue()

    csv_data = await run_in_threadpool(_build_csv)
    filename = f"orbital_submissions_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
