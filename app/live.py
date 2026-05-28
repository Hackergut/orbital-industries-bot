"""Live browser view module."""
import os
import json
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import HTMLResponse

router = APIRouter()

def _admin_logged_in(request: Request) -> bool:
    if request.session.get("admin") is True:
        return True
    # Localhost bypass for Docker/local dev
    host = request.headers.get("host", "")
    if host.startswith("127.") or host.startswith("localhost") or host.startswith("192.168."):
        return True
    return False

LIVE_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Orbital Live Browser</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0a0e27;color:#e0e0e0;overflow-x:hidden}
.container{max-width:1800px;margin:0 auto;padding:20px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px}
h1{color:#00ff88;font-size:1.8em;display:flex;align-items:center;gap:10px}
.live-dot{width:12px;height:12px;background:#00ff88;border-radius:50%;animation:pulse 1.5s infinite}
@keyframes pulse{0%{opacity:1}50%{opacity:0.3}100%{opacity:1}}
.controls{display:flex;gap:12px}
.btn{padding:10px 20px;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:14px}
.btn-start{background:#00ff88;color:#0a0e27}
.btn-stop{background:#ff3333;color:#fff}
.btn-refresh{background:#00ccff;color:#0a0e27}
.grid{display:grid;grid-template-columns:1.5fr 1fr;gap:20px;margin-bottom:20px}
@media(max-width:1200px){.grid{grid-template-columns:1fr}}
.panel{background:#1a1e3f;border:1px solid #00ff88;border-radius:12px;padding:16px}
.panel h2{color:#00ff88;margin-bottom:12px;font-size:1.1em}
.screenshot-container{position:relative;width:100%;background:#0a0e27;border-radius:8px;overflow:hidden;min-height:350px;display:flex;align-items:center;justify-content:center}
.screenshot-container img{width:100%;max-height:600px;object-fit:contain;border-radius:8px;display:block}
.screenshot-placeholder{color:#666;font-size:16px;text-align:center;padding:40px}
.screenshot-label{position:absolute;top:8px;right:12px;background:rgba(0,0,0,0.7);color:#00ff88;padding:4px 10px;border-radius:4px;font-size:12px;font-family:monospace}
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:20px}
.stat-card{background:#1a1e3f;border:1px solid #00ff88;border-radius:8px;padding:14px;text-align:center}
.stat-value{font-size:1.8em;font-weight:bold;color:#00ff88}
.stat-label{font-size:11px;color:#888;margin-top:4px}
.data-panel{background:#1a1e3f;border:1px solid #00ccff;border-radius:12px;padding:16px;margin-bottom:20px;max-height:350px;overflow-y:auto}
.data-panel h2{color:#00ccff;margin-bottom:12px;font-size:1.1em}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 8px;text-align:left;border-bottom:1px solid #333}
th{color:#00ccff;font-weight:bold}
.status-badge{padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold}
.status-submitted{background:#004d00;color:#00ff88}
.status-error{background:#4d0000;color:#ff3333}
.status-pending{background:#4d4d00;color:#ffff00}
.status-new{background:#004d4d;color:#00ffff}
.log-panel{background:#1a1e3f;border:1px solid #00ff88;border-radius:12px;padding:16px;max-height:300px;display:flex;flex-direction:column}
.log-panel h2{color:#00ff88;margin-bottom:12px;font-size:1.1em}
.log-container{flex:1;overflow-y:auto;background:#0a0e27;border-radius:8px;padding:12px;font-family:monospace;font-size:11px;line-height:1.7}
.log-line{margin:1px 0;padding:1px 0}
.log-ok{color:#00ff88}
.log-warn{color:#ffff00}
.log-error{color:#ff3333}
.log-info{color:#00ccff}
.status-bar{background:#1a1e3f;border:1px solid #00ff88;border-radius:8px;padding:12px;margin-bottom:20px;display:flex;gap:25px;align-items:center;flex-wrap:wrap}
.status-item{display:flex;align-items:center;gap:6px;font-size:14px}
.status-value{font-weight:bold;color:#00ff88}
#runningIndicator{display:none}
#runningIndicator.active{display:inline-flex;align-items:center;gap:6px}
.spinner{width:14px;height:14px;border:2px solid #00ff88;border-top:2px solid transparent;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.tabs{display:flex;gap:8px;margin-bottom:12px;border-bottom:1px solid #333;padding-bottom:8px}
.tab-btn{padding:6px 14px;border:none;border-radius:4px;background:#1a1e3f;color:#888;cursor:pointer;font-size:13px}
.tab-btn.active{background:#00ff88;color:#0a0e27;font-weight:bold}
.tab-content{display:none}
.tab-content.active{display:block}
</style>
<script>window.__INITIAL_DATA__ = null;</script>
</head>
<body>
<div class="container">
<div class="header">
<h1><span class="live-dot"></span> Orbital Live Browser</h1>
<div class="controls">
<button class="btn btn-start" onclick="startPipeline()">Start Pipeline</button>
<button class="btn btn-stop" onclick="stopPipeline()">Stop</button>
<button class="btn btn-refresh" onclick="forceRefresh()">Refresh</button>
<a href="/dashboard/" style="color:#00ff88;text-decoration:none;font-size:14px;margin-right:12px">🏠 Dashboard</a>
<a href="/live" style="color:#00ff88;text-decoration:none;font-size:14px;margin-right:12px">📡 Live</a>
<a href="/history" style="color:#00ff88;text-decoration:none;font-size:14px;margin-right:12px">📜 History</a>
<a href="/temporal/ui" style="color:#00ff88;text-decoration:none;font-size:14px;margin-right:12px">⏳ Temporal</a>
<a href="/logout" style="color:#ff3333;text-decoration:none;font-size:14px">🔒 Logout</a>
</div>
</div>

<div class="status-bar">
<div class="status-item">Status: <span id="statusText" class="status-value">Idle</span>
<span id="runningIndicator"><span class="spinner"></span> Running</span></div>
<div class="status-item">Processed: <span id="processedCount" class="status-value">0</span></div>
<div class="status-item">Submitted: <span id="submittedCount" class="status-value">0</span></div>
<div class="status-item">Failed: <span id="failedCount" class="status-value">0</span></div>
<div class="status-item">Rate: <span id="rateValue" class="status-value">0</span>/h</div>
<div class="status-item">Targets: <span id="totalTargets" class="status-value">0</span></div>
<div class="status-item">Leads: <span id="totalLeads" class="status-value">0</span></div>
</div>

<div class="grid">
<div>
<div class="panel">
<h2>🖥️ Live Screenshot</h2>
<div class="screenshot-container" id="screenshotContainer">
<div class="screenshot-placeholder">Waiting for first submission...</div>
</div>
<div class="screenshot-label" id="screenshotLabel">No screenshot yet</div>
</div>

<div class="log-panel" style="margin-top:20px">
<h2>📝 Live Logs</h2>
<div class="log-container" id="logContainer"><div class="log-line log-info">Ready...</div></div>
</div>
</div>

<div>
<div class="stats-grid">
<div class="stat-card"><div class="stat-value" id="statPending">0</div><div class="stat-label">Pending</div></div>
<div class="stat-card"><div class="stat-value" id="statAnalyzed">0</div><div class="stat-label">Analyzed</div></div>
<div class="stat-card"><div class="stat-value" id="statSubmitted">0</div><div class="stat-label">Submitted</div></div>
<div class="stat-card"><div class="stat-value" id="statFailed">0</div><div class="stat-label">Failed</div></div>
<div class="stat-card"><div class="stat-value" id="statSkipped">0</div><div class="stat-label">Skipped</div></div>
<div class="stat-card"><div class="stat-value" id="statCaptcha">0</div><div class="stat-label">CAPTCHAs</div></div>
</div>

<div class="data-panel">
<div class="tabs">
<button class="tab-btn active" onclick="switchTab('submissions')">Submissions</button>
<button class="tab-btn" onclick="switchTab('leads')">Leads</button>
<button class="tab-btn" onclick="switchTab('targets')">Targets</button>
</div>
<div id="tab-submissions" class="tab-content active">
<table><thead><tr><th>ID</th><th>Domain</th><th>Status</th><th>Fields</th><th>Form</th></tr></thead><tbody id="submissionsBody"></tbody></table>
</div>
<div id="tab-leads" class="tab-content">
<table><thead><tr><th>Email</th><th>Source</th><th>Status</th><th>Form Sent</th></tr></thead><tbody id="leadsBody"></tbody></table>
</div>
<div id="tab-targets" class="tab-content">
<table><thead><tr><th>ID</th><th>URL</th><th>Status</th><th>Form</th></tr></thead><tbody id="targetsBody"></tbody></table>
</div>
<div id="tab-proofs" class="tab-content">
<table><thead><tr><th>ID</th><th>Domain</th><th>Status</th><th>Proof</th><th>Screenshots</th></tr></thead><tbody id="proofsBody"></tbody></table>
</div>
</div>
</div>
</div>

<script>
let latestScreenshot = '';
let lastLogCount = 0;

function escapeHtml(text){
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function classifyLog(line){
    const lower = line.toLowerCase();
    if(lower.includes('submitted') || lower.includes('success')) return 'log-ok';
    if(lower.includes('error') || lower.includes('failed')) return 'log-error';
    if(lower.includes('warning') || lower.includes('warn')) return 'log-warn';
    if(lower.includes('processing') || lower.includes('navigating')) return 'log-info';
    return '';
}

function statusClass(s){
    if(s==='submitted') return 'status-submitted';
    if(s==='error' || s==='timeout') return 'status-error';
    if(s==='pending') return 'status-pending';
    return '';
}

function switchTab(name){
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('tab-'+name).classList.add('active');
}

let evtSource = null;
let reconnectTimer = null;
let lastLogCount = 0;

function connectSSE(){
    if(evtSource) { evtSource.close(); }
    evtSource = new EventSource('/api/live/stream', {withCredentials: true});
    evtSource.onmessage = (e) => {
        try{
            const data = JSON.parse(e.data);
            if(data.error) { console.error('SSE error:', data.error); return; }
            renderLive(data);
        }catch(err){
            console.error('Parse error:', err);
        }
    };
    evtSource.onerror = () => {
        console.warn('SSE disconnected, retrying in 5s...');
        evtSource.close();
        if(!reconnectTimer){
            reconnectTimer = setTimeout(() => { reconnectTimer = null; // Use server-rendered initial data immediately
if (window.__INITIAL_DATA__) {
    renderLive(window.__INITIAL_DATA__);
}
connectSSE();
// Fallback polling every 3s if SSE fails or browser blocks it
setInterval(() => {
    if (!evtSource || evtSource.readyState === EventSource.CLOSED) {
        forceRefresh();
    }
}, 3000); }, 5000);
        }
    };
}

function renderLive(data){
    const status = data.status || {};

    // Pipeline stats
    document.getElementById('statusText').textContent = status.started_at ? 'Running' : 'Idle';
    document.getElementById('runningIndicator').className = status.started_at ? 'active' : '';
    document.getElementById('processedCount').textContent = status.processed || 0;
    document.getElementById('submittedCount').textContent = status.submitted || 0;
    document.getElementById('failedCount').textContent = status.failed || 0;
    document.getElementById('rateValue').textContent = (status.rate_per_hour || 0).toFixed(1);
    document.getElementById('totalTargets').textContent = status.total_targets || 0;
    document.getElementById('totalLeads').textContent = data.stats?.total_leads || 0;

    // Stats cards
    if(data.stats){
        document.getElementById('statPending').textContent = data.stats.pending || 0;
        document.getElementById('statSubmitted').textContent = data.stats.submitted || 0;
        document.getElementById('statFailed').textContent = data.stats.failed || 0;
        document.getElementById('statSkipped').textContent = data.stats.skipped || 0;
        document.getElementById('statCaptcha').textContent = (status.captchas_solved || 0) + '/' + ((status.captchas_solved||0)+(status.captchas_failed||0));
    }

    // Update screenshot
    const ssData = data.screenshot || {};
    if(ssData.filename){
        const isDesktop = ssData.filename === 'desktop.png';
        if(isDesktop || ssData.filename !== latestScreenshot){
            latestScreenshot = ssData.filename;
            const container = document.getElementById('screenshotContainer');
            let img = document.getElementById('liveImg');
            if(!img || !isDesktop){
                container.innerHTML = `<img src="${ssData.url}?t=${Date.now()}" alt="Live screenshot" id="liveImg" style="width:100%;max-height:600px;object-fit:contain;border-radius:8px;display:block">`;
            } else {
                img.src = ssData.url + '?t=' + Date.now();
            }
            document.getElementById('screenshotLabel').textContent = ssData.stage || ssData.filename;
        }
    }

    // Update proofs table
    if(data.proofs && data.proofs.length){
        const tbody = document.getElementById('proofsBody');
        tbody.innerHTML = data.proofs.slice(0, 20).map(p=>{
            const domain = (p.target_url||'').replace(/^https?:\/\//,'').split('/')[0];
            const ssLinks = [];
            if(p.pre_screenshot) ssLinks.push(`<a href="/${p.pre_screenshot}" target="_blank" style="color:#00ccff">Pre</a>`);
            if(p.post_screenshot) ssLinks.push(`<a href="/${p.post_screenshot}" target="_blank" style="color:#00ff88">Post</a>`);
            if(p.confirmation_screenshot) ssLinks.push(`<a href="/${p.confirmation_screenshot}" target="_blank" style="color:#ffcc00">Confirm</a>`);
            if(p.video_path) ssLinks.push(`<a href="/${p.video_path}" target="_blank" style="color:#ff3333">Video</a>`);
            return `<tr><td>${p.id}</td><td>${escapeHtml(domain)}</td><td><span class="status-badge ${statusClass(p.status)}">${p.status}</span></td><td><button class="btn" style="padding:4px 10px;font-size:11px" onclick="showProofDetail(${p.id})">View</button></td><td>${ssLinks.join(' | ')||'-'}</td></tr>`;
        }).join('');
    }

    // Update submissions table
    if(data.submissions && data.submissions.length){
        const tbody = document.getElementById('submissionsBody');
        tbody.innerHTML = data.submissions.slice(0, 15).map(s=>{
            let formBtn='';
            if(s.field_mapping){
                const b64=btoa(escapeHtml(s.field_mapping||'')).replace(/=/g,'');
                const filled=Object.values(JSON.parse(s.field_mapping||'{}')).filter(v=>typeof v==='object'&&v&&v.action!=='skip').length;
                formBtn=`<button style="padding:2px 6px;font-size:10px;border:none;border-radius:4px;background:#00ccff;color:#0a0e27;cursor:pointer" onclick="showFormModal('Submission #${s.id} — ${escapeHtml((s.target_url||'').substring(0,30))}', '${b64}', '')">Show ${filled} fields</button>`;
            }
            return `<tr><td>${s.id}</td><td><a href="${escapeHtml(s.target_url||'')}" target="_blank" style="color:#00ccff">${escapeHtml((s.target_url||'').substring(0,40))}</a></td>
            <td><span class="status-badge ${statusClass(s.status)}">${s.status}</span></td>
            <td>${s.fields_filled||0}/${s.fields_total||0}</td>
            <td>${formBtn||'-'}</td></tr>`;
        }).join('');
    }

    // Update leads table — clearer view with filled field count
    if(data.leads && data.leads.length){
        const tbody = document.getElementById('leadsBody');
        tbody.innerHTML = data.leads.slice(0, 15).map(l=>{
            let formBtn = '';
            if(l.submitted_form_data){
                const b64 = btoa(escapeHtml(l.submitted_form_data||'')).replace(/=/g,'');
                formBtn = ` <button style="padding:2px 6px;font-size:10px;border:none;border-radius:4px;background:#00ccff;color:#0a0e27;cursor:pointer" onclick="showFormModal('${escapeHtml(l.email||'')}', '${b64}', '${escapeHtml((l.submitted_message||'').substring(0,120))}')">View ${l.form_filled_count||0} fields</button>`;
            }
            return `<tr><td>${escapeHtml(l.email||'')}${formBtn}</td><td><a href="${escapeHtml(l.source_url||'')}" target="_blank" style="color:#00ccff">${escapeHtml((l.source_url||'').substring(0,35))}</a></td>
                <td><span class="status-badge status-new">${l.status}</span></td>
                <td>${l.submitted_form_data ? `<span style="color:#00ff88;font-size:11px">${l.form_filled_count||0} fields sent</span>` : '-'}</td></tr>`;
        }).join('');
    }

    // Update targets table
    if(data.targets && data.targets.length){
        const tbody = document.getElementById('targetsBody');
        tbody.innerHTML = data.targets.slice(0, 15).map(t=>`
            <tr><td>${t.id}</td><td><a href="${escapeHtml(t.url||'')}" target="_blank" style="color:#00ccff">${escapeHtml((t.url||'').substring(0,40))}</a></td>
            <td><span class="status-badge ${statusClass(t.status)}">${t.status}</span></td>
            <td>${t.has_form?'Yes':'No'}</td></tr>`).join('');
    }

    // Update logs
    if(data.logs && data.logs.length > lastLogCount){
        lastLogCount = data.logs.length;
        const container = document.getElementById('logContainer');
        const tail = data.logs.slice(-40);
        container.innerHTML = tail.map(line => {
            const cls = classifyLog(line);
            return `<div class="log-line ${cls}">${escapeHtml(line)}</div>`;
        }).join('');
        container.scrollTop = container.scrollHeight;
    }
}

async function startPipeline(){
    document.getElementById('statusText').textContent = 'Starting...';
    try{
        await fetch('/api/pipeline/start', {method:'POST', credentials: 'same-origin'});
    }catch(e){console.error(e);}
}
function stopPipeline(){
    document.getElementById('statusText').textContent = 'Stopped';
}
function forceRefresh(){
    fetch('/api/live/all', {credentials:'same-origin'})
        .then(r=>r.json()).then(d=>renderLive(d)).catch(e=>console.error(e));
}

// Use server-rendered initial data immediately
if (window.__INITIAL_DATA__) {
    renderLive(window.__INITIAL_DATA__);
}
connectSSE();
// Fallback polling every 3s if SSE fails or browser blocks it
setInterval(() => {
    if (!evtSource || evtSource.readyState === EventSource.CLOSED) {
        forceRefresh();
    }
}, 3000);
</script>
<!-- Form Data Modal -->
<div id="formModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:1000;align-items:center;justify-content:center">
<div style="background:#1a1e3f;border:2px solid #00ff88;border-radius:12px;padding:24px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;position:relative">
<button onclick="closeFormModal()" style="position:absolute;top:10px;right:14px;background:#ff3333;color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:12px">Close</button>
<h3 id="modalEmail" style="color:#00ff88;margin-bottom:12px;font-size:1.2em"></h3>
<div id="modalMessage" style="background:#0a0e27;border-radius:8px;padding:12px;margin-bottom:16px;font-size:13px;color:#00ccff;border:1px solid #00ccff"></div>
<pre id="modalForm" style="background:#0a0e27;border-radius:8px;padding:12px;font-family:monospace;font-size:11px;color:#e0e0e0;overflow-x:auto;white-space:pre-wrap;word-break:break-word;border:1px solid #333"></pre>
</div>
</div>
<script>
function showFormModal(email, formB64, message){
    document.getElementById("modalEmail").textContent = email || "Form Data";
    document.getElementById("modalMessage").textContent = message || "No message saved";
    try{
        const raw = atob(formB64.replace(/[^A-Za-z0-9+/]/g, '') + Array(5 - formB64.length % 4).fill('=').join(''));
        const formData = JSON.parse(raw);
        let rows = '';
        for(const [k,v] of Object.entries(formData)){
            if(typeof v === 'object' && v !== null){
                if(v.action === 'skip') continue;
                rows += `<tr><td style="padding:4px 8px;border-bottom:1px solid #333;color:#00ccff">${k}</td><td style="padding:4px 8px;border-bottom:1px solid #333">${escapeHtml(String(v.value||''))}</td><td style="padding:4px 8px;border-bottom:1px solid #333;color:#00ff88">${v.action}</td></tr>`;
            } else {
                rows += `<tr><td style="padding:4px 8px;border-bottom:1px solid #333;color:#00ccff">${k}</td><td style="padding:4px 8px;border-bottom:1px solid #333">${escapeHtml(String(v||''))}</td><td style="padding:4px 8px;border-bottom:1px solid #333;color:#00ff88">fill</td></tr>`;
            }
        }
        const table = `<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr><th style="text-align:left;color:#00ff88;padding:4px 8px">Field</th><th style="text-align:left;color:#00ff88;padding:4px 8px">Value</th><th style="text-align:left;color:#00ff88;padding:4px 8px">Action</th></tr></thead><tbody>${rows}</tbody></table>`;
        document.getElementById("modalForm").innerHTML = table;
    }catch(e){
        document.getElementById("modalForm").textContent = atob(formB64.replace(/[^A-Za-z0-9+/]/g, '') + Array(5 - formB64.length % 4).fill('=').join(''));
    }
    document.getElementById("formModal").style.display = "flex";
}
function closeFormModal(){
    document.getElementById("formModal").style.display = "none";
}
document.getElementById("formModal").addEventListener("click", function(e){
    if(e.target === this) closeFormModal();
});
</script>
</body>
</html>
"""

@router.get("/live", response_class=HTMLResponse)
async def live_page(request: Request):
    if not _admin_logged_in(request):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard/?tab=live", status_code=302)
