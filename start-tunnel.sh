#!/bin/bash
# Orbital Industries — Cloudflare Tunnel (via screen)
SESSION="cloudflared-orbital"
CONFIG="$HOME/.cloudflared/config.yml"
TUNNEL_ID="9eb35455-e593-4586-9a67-de2002043e78"

# Kill existing session
screen -S $SESSION -X quit 2>/dev/null
sleep 1

# Start new detached screen session
screen -dmS $SESSION /opt/homebrew/bin/cloudflared tunnel --config "$CONFIG" run "$TUNNEL_ID"

echo "[$(date)] Tunnel started in screen session: $SESSION"
echo "To view logs: screen -r $SESSION"
echo "To detach: Ctrl+A then D"
