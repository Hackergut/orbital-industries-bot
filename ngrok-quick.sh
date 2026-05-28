#!/bin/bash
# ── Orbital Industries — 30-second public URL via ngrok ───────
set -e

if ! command -v ngrok >/dev/null 2>&1; then
    echo "[→] Installing ngrok..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ngrok/ngrok/ngrok
    else
        curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
        echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list >/dev/null
        sudo apt update && sudo apt install ngrok
    fi
    echo ""
    echo "⚠️  You MUST run 'ngrok config add-authtoken <YOUR_TOKEN>' first"
    echo "   Get token free at: https://dashboard.ngrok.com/get-started/your-authtoken"
    exit 1
fi

echo "[→] Starting ngrok tunnel to http://localhost:5000"
echo "    Your app will be public at a random *.ngrok-free.app URL"
echo ""
echo "    Dashboard will appear below once connected..."
echo ""

# Ensure app is running locally
if ! curl -s http://localhost:5000/api/pipeline/status >/dev/null; then
    echo "⚠️  App not running on localhost:5000"
    echo "    Start it first:  docker compose -f docker-compose.prod.yml up -d"
    exit 1
fi

ngrok http http://localhost:5000
