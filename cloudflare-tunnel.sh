#!/bin/bash
# ── Orbital Industries — Cloudflare Tunnel Deploy ─────────────
# FREE HTTPS + custom domain in 2 minutes. No VPS needed.
# Requirements: Docker running, Cloudflare account (free)
set -e

DOMAIN="${1:-}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Orbital Industries — Cloudflare Tunnel Deploy          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [ -z "$DOMAIN" ]; then
    echo "Usage:   ./cloudflare-tunnel.sh <your-domain.com>"
    echo "Example: ./cloudflare-tunnel.sh orbital.mycompany.com"
    echo ""
    echo "Your domain must be on Cloudflare DNS (free plan works)."
    echo ""
    echo "Steps before running:"
    echo "  1. Add your domain to Cloudflare (cloudflare.com)"
    echo "  2. Point domain NS to Cloudflare"
    echo "  3. Run this script"
    exit 1
fi

# ── 1. Install cloudflared if missing ─────────────────────────
if ! command -v cloudflared >/dev/null 2>&1; then
    echo "[→] Installing cloudflared..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install cloudflared
    else
        curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
        sudo dpkg -i /tmp/cloudflared.deb
        rm /tmp/cloudflared.deb
    fi
fi

# ── 2. Authenticate ───────────────────────────────────────────
echo "[→] Opening Cloudflare auth..."
cloudflared tunnel login

echo ""
echo "✅ Auth complete. Creating tunnel for: $DOMAIN"

# ── 3. Create tunnel ──────────────────────────────────────────
TUNNEL_NAME="orbital-$(hostname | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-' | head -c20)"
TUNNEL_ID=$(cloudflared tunnel create "$TUNNEL_NAME" 2>&1 | grep -oP 'Created tunnel \K[a-z0-9\-]+' || true)

if [ -z "$TUNNEL_ID" ]; then
    echo "Tunnel may already exist. Listing tunnels:"
    cloudflared tunnel list
    echo ""
    echo "Run manually:"
    echo "  cloudflared tunnel route dns <tunnel-id> $DOMAIN"
    echo "  cloudflared tunnel run <tunnel-id>"
    exit 1
fi

echo "Tunnel ID: $TUNNEL_ID"

# ── 4. Route DNS ──────────────────────────────────────────────
cloudflared tunnel route dns "$TUNNEL_ID" "$DOMAIN"

# ── 5. Write config ───────────────────────────────────────────
CONFIG_DIR="$HOME/.cloudflared"
mkdir -p "$CONFIG_DIR"

cat > "$CONFIG_DIR/${TUNNEL_ID}.json" << CFGEOF
{
    "tunnel": "$TUNNEL_ID",
    "credentials-file": "$CONFIG_DIR/${TUNNEL_ID}.json",
    "ingress": [
        {
            "hostname": "$DOMAIN",
            "service": "http://localhost:80",
            "originRequest": {
                "noTLSVerify": true,
                "connectTimeout": "30s",
                "tlsTimeout": "30s"
            }
        },
        { "service": "http_status:404" }
    ],
    "protocol": "http2"
}
CFGEOF

# ── 6. Start instructions ─────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    ✅ TUNNEL READY                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Domain:    https://$DOMAIN"
echo "Tunnel:    $TUNNEL_NAME ($TUNNEL_ID)"
echo ""
echo "1. Start nginx + app stack:"
echo "   docker compose -f docker-compose.prod.yml up -d"
echo ""
echo "2. Start tunnel (foreground):"
echo "   cloudflared tunnel run $TUNNEL_ID"
echo ""
echo "3. Or install as system service:"
echo "   sudo cloudflared service install $TUNNEL_ID"
echo "   sudo systemctl start cloudflared"
echo ""
echo "Dashboard will be live at: https://$DOMAIN/dashboard/"
echo ""
