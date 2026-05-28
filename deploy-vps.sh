#!/bin/bash
# ── Orbital Industries — VPS Production Deploy ────────────────
# Run on Ubuntu 22.04+ VPS as root. Sets up Docker, nginx, SSL.
set -e

APP_DIR="${APP_DIR:-/opt/orbital}"
DOMAIN="${1:-}"

echo "=== Orbital Industries — VPS Deploy ==="
if [ -z "$DOMAIN" ]; then
    echo "Usage: ./deploy-vps.sh <your-domain.com>"
    exit 1
fi

# ── 1. System ────────────────────────────────────────────────
apt-get update -qq
apt-get install -y -qq curl git docker.io docker-compose-plugin nginx certbot python3-certbot-nginx ufw fail2ban
systemctl enable docker && systemctl start docker

# ── 2. Firewall ──────────────────────────────────────────────
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ── 3. App dir ───────────────────────────────────────────────
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# ── 4. .env setup ────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.production .env
    # Generate random secret key
    SECRET=$(openssl rand -hex 32 2>/dev/null || cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c64)
    sed -i "s|SECRET_KEY=.*|SECRET_KEY=$SECRET|" .env
    echo "Generated SECRET_KEY"
fi

# ── 5. Docker Compose prod ───────────────────────────────────
docker compose -f docker-compose.prod.yml down 2>/dev/null || true
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --build

# ── 6. Nginx + SSL ───────────────────────────────────────────
cat > /etc/nginx/sites-available/orbital << NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/orbital /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# SSL
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" 2>/dev/null || true

# ── 7. Verify ────────────────────────────────────────────────
sleep 5
STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "https://$DOMAIN/api/pipeline/status" || echo "000")
if [ "$STATUS" = "200" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                  ✅ DEPLOY SUCCESS                            ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo "  Dashboard: https://$DOMAIN/dashboard/"
    echo "  Health:    https://$DOMAIN/api/pipeline/status"
    echo ""
    echo "  Login:     admin  (password from .env ADMIN_PASSWORD)"
    echo ""
    echo "  Commands:"
    echo "    cd $APP_DIR && docker compose -f docker-compose.prod.yml logs -f"
    echo "    cd $APP_DIR && docker compose -f docker-compose.prod.yml restart"
else
    echo "⚠️  Status check returned $STATUS. Checking logs..."
    docker compose -f docker-compose.prod.yml logs --tail=30 web
fi
