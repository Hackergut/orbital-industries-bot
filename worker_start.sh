#!/bin/bash
set -e

# Clean up any stale Xvfb lock files
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Start virtual display for the browser
Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1

# Start a minimal window manager
fluxbox -display :99 &
FLUX_PID=$!

# Capture desktop screenshots in a loop for the live view
(
    while true; do
        DISPLAY=:99 scrot -o /app/static/desktop.png 2>/dev/null || true
        sleep 0.5
    done
) &
SCROT_PID=$!

# Ensure cleanup on exit
cleanup() {
    kill $SCROT_PID $FLUX_PID $XVFB_PID 2>/dev/null || true
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
}
trap cleanup EXIT

# Run persistent pipeline — browser pool stays open across batches
export DISPLAY=:99

# Launch the persistent runner (no more killing chromium between batches)
exec python /app/pipeline_runner_persistent.py
