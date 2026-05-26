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
flask_app = create_app()

HISTORY_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orbital History — Screenshots & Submissions</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0a0e27;color:#e0e0e0;overflow-x:hidden}
.container{max-width:1800px;margin:0 auto;padding:20px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px}
h1{color:#00ff88;font-size:1.8em}
.nav a{color:#00ff88;text-decoration:none;margin-left:16px;font-size:14px}
.section{background:#1a1e3f;border:1px solid #00ff88;border-radius:12px;padding:16px;margin-bottom:20px}
.section h2{color:#00ff88;margin-bottom:12px;font-size:1.2em}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.gallery-item{background:#0a0e27;border:1px solid #333;border-radius:8px;overflow:hidden;cursor:pointer;transition:border .2s}
.gallery-item:hover{border-color:#00ff88}
.gallery-item img{width:100%;height:180px;object-fit:cover;display:block}
.gallery-item .meta{padding:8px;font-size:11px;color:#888}
.gallery-item .meta strong{color:#00ccff}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.9);z-index:1000;align-items:center;justify-content:center;flex-direction:column}
.modal img{max-width:90%;max-height:80vh;border:2px solid #00ff88;border-radius:8px}
.modal .close{position:absolute;top:20px;right:30px;color:#fff;font-size:30px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #333}
th{color:#00ff88}
.status-badge{padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold}
.status-submitted{background:#004d00;color:#00ff88}
.status-error{background:#4d0000;color:#ff3333}
.expand-btn{background:#00ccff;color:#0a0e27;border:none;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px}
.form-detail{display:none;background:#0a0e27;border:1px solid #333;border-radius:4px;padding:10px;margin-top:6px;font-family:monospace;font-size:11px;white-space:pre-wrap;word-break:break-word}
.log-container{max-height:500px;overflow-y:auto;background:#0a0e27;border-radius:8px;padding:12px;font-family:monospace;font-size:11px;line-height:1.7}
.log-line{margin:1px 0}
.log-ok{color:#00ff88}
.log-warn{color:#ffff00}
.log-error{color:#ff3333}
.log-info{color:#00ccff}
.csv-btn{background:#00ff88;color:#0a0e27;border:none;border-radius:4px;padding:8px 16px;cursor:pointer;font-weight:bold;font-size:13px}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>Orbital History</h1>
<div class="nav">
<a href="/live">← Live View</a>
<a href="/dashboard/">Dashboard →</a>
</div>
</div>

<div class="section">
<h2>📸 Screenshots Gallery</h2>
<div class="gallery" id="gallery">Loading...</div>
</div>

<div class="section">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
<h2>📋 Full Submissions</h2>
<button class="csv-btn" onclick="downloadCsv()">⬇ Download CSV</button>
</div>
<table>
<thead><tr><th>ID</th><th>Target</th><th>Status</th><th>Fields</th><th>Form Data</th><th>Time</th></tr></thead>
<tbody id="submissionsBody">Loading...</tbody>
</table>
</div>

<div class="section">
<h2>📝 Full Logs</h2>
<div class="log-container" id="logContainer">Loading...</div>
</div>
</div>

<div class="modal" id="imgModal" onclick="this.style.display='none'">
<span class="close">&times;</span>
<img id="modalImg" src="" alt="Screenshot">
</div>

<script>
function escapeHtml(text){
    const div=document.createElement('div');div.textContent=text;return div.innerHTML;
}
function statusClass(s){
    if(s==='submitted')return'status-submitted';
    if(s==='error'||s==='timeout')return'status-error';
    return'';
}
function openModal(src){
    document.getElementById('modalImg').src=src;
    document.getElementById('imgModal').style.display='flex';
}
function toggleForm(id){
    const el=document.getElementById('form-'+id);
    el.style.display=el.style.display==='block'?'none':'block';
}

async function downloadCsv(){
    try{
        const res=await fetch('/api/submissions/export',{credentials:'same-origin'});
        if(res.status===401){window.location.href='/login';return;}
        const blob=await res.blob();
        const url=window.URL.createObjectURL(blob);
        const a=document.createElement('a');
        a.href=url;
        a.download='orbital_submissions_'+new Date().toISOString().slice(0,10)+'.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    }catch(e){console.error(e);}
}

async function loadScreenshots(){
    const res=await fetch('/api/screenshots?limit=40',{credentials:'same-origin'});
    if(res.status===401){window.location.href='/login';return;}
    const data=await res.json();
    const container=document.getElementById('gallery');
    if(!data.screenshots||!data.screenshots.length){container.innerHTML='<div style="color:#666;padding:20px">No screenshots yet</div>';return;}
    container.innerHTML=data.screenshots.map(ss=>{
        const date=new Date(ss.mtime*1000).toLocaleString();
        return `<div class="gallery-item" onclick="openModal('${escapeHtml(ss.url)}')">
            <img src="${ss.url}" alt="${escapeHtml(ss.filename)}">
            <div class="meta"><strong>${escapeHtml(ss.target_url||ss.filename)}</strong><br>${date}</div>
        </div>`;
    }).join('');
}

async function loadSubmissions(){
    const res=await fetch('/api/submissions?limit=50',{credentials:'same-origin'});
    if(res.status===401)return;
    const data=await res.json();
    const tbody=document.getElementById('submissionsBody');
    if(!data.submissions||!data.submissions.length){tbody.innerHTML='<tr><td colspan="6" style="color:#666">No submissions</td></tr>';return;}
    tbody.innerHTML=data.submissions.map(s=>{
        let formBtn='';
        let formDetail='';
        if(s.field_mapping){
            formBtn=`<button class="expand-btn" onclick="toggleForm(${s.id})">Show Form</button>`;
            try{
                const parsed=JSON.parse(s.field_mapping);
                let rows='';
                for(const[k,v]of Object.entries(parsed)){
                    if(typeof v==='object'&&v!==null){
                        if(v.action==='skip')continue;
                        rows+=`<div style="color:#00ccff">${k}</div><div style="padding-left:12px">Value: ${escapeHtml(String(v.value||''))} <span style="color:#00ff88">[${v.action}]</span></div>`;
                    }else{
                        rows+=`<div style="color:#00ccff">${k}</div><div style="padding-left:12px">Value: ${escapeHtml(String(v||''))} <span style="color:#00ff88">[fill]</span></div>`;
                    }
                }
                formDetail=`<div class="form-detail" id="form-${s.id}">${rows||'No fillable fields mapped'}</div>`;
            }catch(e){
                formDetail=`<div class="form-detail" id="form-${s.id}">${escapeHtml(s.field_mapping)}</div>`;
            }
        }
        const targetUrl=escapeHtml(s.target_url||'');
        return `<tr>
            <td>${s.id}</td>
            <td><a href="${targetUrl}" target="_blank" style="color:#00ccff">${targetUrl.substring(0,45)}</a></td>
            <td><span class="status-badge ${statusClass(s.status)}">${s.status}</span></td>
            <td>${s.fields_filled||0}/${s.fields_total||0}</td>
            <td>${formBtn}${formDetail}</td>
            <td>${s.created_at?s.created_at.substring(0,19).replace('T',' '):''}</td>
        </tr>`;
    }).join('');
}

async function loadLogs(){
    const res=await fetch('/api/logs/full?lines=200',{credentials:'same-origin'});
    if(res.status===401)return;
    const data=await res.json();
    const container=document.getElementById('logContainer');
    if(!data.lines||!data.lines.length){container.innerHTML='<div style="color:#666">No logs</div>';return;}
    container.innerHTML=data.lines.map(line=>{
        const lower=line.toLowerCase();
        let cls='';
        if(lower.includes('submitted')||lower.includes('success'))cls='log-ok';
        else if(lower.includes('error')||lower.includes('failed'))cls='log-error';
        else if(lower.includes('warning')||lower.includes('warn'))cls='log-warn';
        else if(lower.includes('navigating')||lower.includes('processing'))cls='log-info';
        return `<div class="log-line ${cls}">${escapeHtml(line)}</div>`;
    }).join('');
    container.scrollTop=container.scrollHeight;
}

loadScreenshots();
loadSubmissions();
loadLogs();
</script>
</body>
</html>
"""


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(HISTORY_HTML)


@router.get("/api/screenshots")
async def api_screenshots(request: Request, limit: int = 40):
    if not request.session.get("admin"):
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
    if not request.session.get("admin"):
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
    if not request.session.get("admin"):
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
