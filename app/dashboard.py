"""
Real-time processing dashboard
"""
import os
import json
from flask import Blueprint, render_template_string, jsonify
from pathlib import Path

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

SCREENSHOTS_DIR = "static/screenshots"
LOGS_DIR = "logs"


@dashboard_bp.route('/')
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Orbital Real-Time Processing</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Monaco', 'Menlo', monospace; background: #0a0e27; color: #e0e0e0; overflow-x: hidden; }
            .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
            .header { text-align: center; margin-bottom: 40px; }
            h1 { font-size: 2em; color: #00ff88; text-shadow: 0 0 10px #00ff88; margin-bottom: 10px; }
            .status { display: flex; justify-content: center; gap: 30px; margin-top: 20px; font-size: 14px; }
            .stat { display: flex; align-items: center; gap: 10px; }
            .stat-value { font-weight: bold; color: #00ff88; font-size: 1.3em; }
            
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }
            @media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }
            
            .card { background: #1a1e3f; border: 1px solid #00ff88; border-radius: 8px; padding: 20px; }
            .card h2 { color: #00ff88; margin-bottom: 15px; font-size: 1.2em; }
            
            .screenshot-container { position: relative; }
            .screenshot-container img { width: 100%; border-radius: 4px; border: 1px solid #00ff88; max-height: 400px; object-fit: contain; }
            .screenshot-label { font-size: 12px; color: #888; margin-top: 8px; text-align: center; }
            
            .log-container { max-height: 500px; overflow-y: auto; background: #0a0e27; border: 1px solid #00ff88; border-radius: 4px; padding: 12px; font-size: 12px; line-height: 1.6; }
            .log-line { margin: 4px 0; }
            .log-processing { color: #00ff88; }
            .log-detected { color: #00ccff; }
            .log-filled { color: #ffaa00; }
            .log-submitted { color: #00ff00; }
            .log-error { color: #ff3333; }
            .log-warning { color: #ffff00; }
            
            .progress-bar { width: 100%; height: 8px; background: #333; border-radius: 4px; overflow: hidden; margin: 10px 0; }
            .progress-fill { height: 100%; background: linear-gradient(90deg, #00ff88, #00ccff); animation: pulse 1s infinite; }
            @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
            
            .timestamp { color: #666; font-size: 11px; }
            
            .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #00ff88; border-top: 2px solid transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
            @keyframes spin { to { transform: rotate(360deg); } }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚀 Orbital Real-Time Processing</h1>
                <div class="status">
                    <div class="stat">
                        <div class="spinner"></div>
                        <span>Status: <span id="status">Monitoring...</span></span>
                    </div>
                    <div class="stat">Forms Submitted: <span class="stat-value" id="submitted">0</span></div>
                    <div class="stat">Domains Processed: <span class="stat-value" id="processed">0</span></div>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="progress"></div>
                </div>
            </div>
            
            <div class="grid">
                <div class="card">
                    <h2>📸 Latest Screenshot</h2>
                    <div class="screenshot-container">
                        <img id="latestScreenshot" src="/static/screenshots/placeholder.png" alt="Latest" onerror="this.src='/static/placeholder.png'">
                        <div class="screenshot-label" id="screenshotLabel">Waiting for first submission...</div>
                    </div>
                </div>
                
                <div class="card">
                    <h2>📝 Live Logs</h2>
                    <div class="log-container" id="logContainer"></div>
                </div>
            </div>
            
            <div class="card">
                <h2>📊 Submitted Forms</h2>
                <div id="submittedList" style="font-size: 13px; line-height: 2; max-height: 300px; overflow-y: auto;"></div>
            </div>
        </div>
        
        <script>
            let logs = [];
            let submitted = [];
            
            async function updateDashboard() {
                try {
                    const response = await fetch('/dashboard/api/status');
                    const data = await response.json();
                    
                    document.getElementById('status').textContent = data.status;
                    document.getElementById('submitted').textContent = data.submitted_count;
                    document.getElementById('processed').textContent = data.processed_count;
                    
                    // Update latest screenshot
                    if (data.latest_screenshot) {
                        const img = document.getElementById('latestScreenshot');
                        img.src = '/static/screenshots/' + data.latest_screenshot + '?t=' + Date.now();
                        document.getElementById('screenshotLabel').textContent = 'Last: ' + data.latest_screenshot;
                    }
                    
                    // Update logs
                    if (data.logs && data.logs.length > logs.length) {
                        logs = data.logs;
                        updateLogs();
                    }
                    
                    // Update submitted
                    if (data.submitted && data.submitted.length > submitted.length) {
                        submitted = data.submitted;
                        updateSubmitted();
                    }
                } catch (e) {
                    console.error('Dashboard update failed:', e);
                }
            }
            
            function updateLogs() {
                const container = document.getElementById('logContainer');
                const tail = logs.slice(-50);
                
                container.innerHTML = tail.map(line => {
                    let css = 'log-line';
                    if (line.includes('submitted')) css += ' log-submitted';
                    else if (line.includes('Filled')) css += ' log-filled';
                    else if (line.includes('Detected')) css += ' log-detected';
                    else if (line.includes('Processing')) css += ' log-processing';
                    else if (line.includes('ERROR')) css += ' log-error';
                    else if (line.includes('WARNING')) css += ' log-warning';
                    
                    return `<div class="${css}">${escapeHtml(line)}</div>`;
                }).join('');
                
                container.scrollTop = container.scrollHeight;
            }
            
            function updateSubmitted() {
                const list = document.getElementById('submittedList');
                list.innerHTML = submitted.slice(-20).reverse().map((s, i) => {
                    return `<div>✅ <strong>${i+1}.</strong> <code>${escapeHtml(s.domain)}</code> - ${s.fields_filled}/${s.fields_total} fields</div>`;
                }).join('');
            }
            
            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }
            
            // Update every 1 second
            updateDashboard();
            setInterval(updateDashboard, 3000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@dashboard_bp.route('/api/status')
def api_status():
    """API endpoint for real-time status"""
    
    # Get latest screenshot
    latest_screenshot = None
    if os.path.exists(SCREENSHOTS_DIR):
        files = sorted([f for f in os.listdir(SCREENSHOTS_DIR) if f.endswith('.png')])
        if files:
            latest_screenshot = files[-1]
    
    # Get logs
    logs = []
    if os.path.exists(LOGS_DIR):
        log_file = os.path.join(LOGS_DIR, 'orbital.log')
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    logs = [line.strip() for line in lines[-100:] if line.strip()]
            except Exception:
                pass
    
    # Parse submitted forms from logs
    submitted = []
    for line in logs:
        if 'submitted' in line.lower() and 'fields_filled' in line:
            try:
                # Extract domain and fields from log line
                if 'https://' in line:
                    domain = line.split('https://')[1].split('/')[0] if 'https://' in line else 'unknown'
                    submitted.append({
                        'domain': domain,
                        'fields_filled': 0,
                        'fields_total': 0
                    })
            except Exception:
                pass
    
    return jsonify({
        'status': 'Processing...',
        'latest_screenshot': latest_screenshot,
        'logs': logs,
        'submitted': submitted[-20:],
        'submitted_count': len(submitted),
        'processed_count': len(set([s['domain'] for s in submitted]))
    })
