"""Ultra-simple live view — Grok/XAI dark minimal."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import os, time

router = APIRouter()


def _admin_logged_in(request) -> bool:
    if request.session.get("admin") is True:
        return True
    host = request.headers.get("host", "")
    if host.startswith("127.") or host.startswith("localhost") or host.startswith("192.168."):
        return True
    return False


@router.get("/live-simple", response_class=HTMLResponse)
async def live_simple(request: Request):
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard/?tab=live", status_code=302)

async def _live_simple_old(request: Request):
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)

    ss_dir = "static/screenshots"
    latest = None
    stage = "No screenshot yet"
    if os.path.exists(ss_dir):
        files = sorted(
            [f for f in os.listdir(ss_dir) if f.endswith(".png") and f != "placeholder.png"],
            key=lambda x: os.path.getmtime(os.path.join(ss_dir, x)),
            reverse=True,
        )
        if files:
            latest = files[0]
            stage = latest.replace("live_", "").replace(".png", "").replace("_", " ").title()

    if not latest:
        latest = "placeholder.png"

    ts = int(time.time())
    img_url = f"/static/screenshots/{latest}?t={ts}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Orbital — Live</title>
<link rel="stylesheet" href="/static/orbital.css">
<style>
.oc-live-img{{width:100%;display:block;border-radius:var(--radius-lg);border:1px solid var(--border)}}
.oc-live-meta{{text-align:center;color:var(--text-muted);font-size:12px;margin-top:14px}}
</style>
</head>
<body>
<div class="oc-container">
  <div class="oc-header">
    <div class="oc-brand"><div class="oc-dot"></div><h1>Live Browser</h1></div>
    <div class="oc-status live">LIVE</div>
  </div>
  <nav class="oc-nav">
    <a href="/dashboard/">Dashboard</a>
    <a href="/history">History</a>
    <a href="/temporal/ui">Temporal</a>
    <a href="/logout">Logout</a>
  </nav>
  <div class="oc-card">
    <div class="oc-card-header">
      <div class="oc-card-title">Current Screenshot</div>
      <div class="oc-card-badge">{stage}</div>
    </div>
    <div class="oc-card-body" style="padding:0">
      <div class="oc-live-view">
        <img class="oc-live-img" src="{img_url}" alt="Live screenshot" onerror="this.style.display='none';document.getElementById('fallback').style.display='block';">
        <div id="fallback" class="oc-empty" style="display:none">
          <div class="oc-empty-icon">&#128247;</div>
          Screenshot not available yet.<br>Pipeline is running — check back in a few seconds.
        </div>
        <div class="oc-live-label">{stage}</div>
      </div>
    </div>
  </div>
  <div class="oc-live-meta">Auto-refresh every 5s &bull; Time: {ts}</div>
</div>
</body>
</html>"""
    return HTMLResponse(html)
