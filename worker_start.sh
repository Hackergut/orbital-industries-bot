#!/bin/bash
set -e

# Selenium Docker worker with virtual display for video recording
export PYTHONUNBUFFERED=1

# Clean stale Xvfb locks
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Start virtual display for the browser
Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1

# Ensure cleanup on exit
cleanup() {
    kill $XVFB_PID 2>/dev/null || true
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
}
trap cleanup EXIT

export DISPLAY=:99

# Run persistent pipeline
exec python /app/pipeline_runner_persistent.py
