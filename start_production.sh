#!/bin/bash
# Orbital Industries — Production Launch Script
# Runs web server + persistent pipeline runner

set -e
CWD="/Volumes/HRD2T/ORBITAL TECH 2"
cd "$CWD"
source venv/bin/activate

mkdir -p logs

# ── Web Server ───────────────────────────────────────────────
if ! lsof -i :5000 > /dev/null 2>&1; then
    echo "[LAUNCH] Starting web server on port 5000..."
    nohup python run.py > logs/web_server.log 2>&1 &
    sleep 2
    echo "[LAUNCH] Web server started"
else
    echo "[LAUNCH] Web server already running on port 5000"
fi

# ── Persistent Pipeline Runner ──────────────────────────────
if ! pgrep -f "pipeline_runner_persistent.py" > /dev/null 2>&1; then
    echo "[LAUNCH] Starting persistent pipeline runner..."
    nohup python pipeline_runner_persistent.py > logs/pipeline.log 2>&1 &
    sleep 2
    echo "[LAUNCH] Pipeline runner started"
else
    echo "[LAUNCH] Pipeline runner already running"
fi

echo "[LAUNCH] Production services active:"
echo "  - Dashboard: http://localhost:5000/live"
echo "  - API:       http://localhost:5000/api/"
echo "  - Logs:      logs/web_server.log  logs/pipeline.log"
