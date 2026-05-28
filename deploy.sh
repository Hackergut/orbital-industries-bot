#!/bin/bash
# ── Orbital Industries — Production Deploy ──────────────────
# Run on Hetzner VPS (Ubuntu 22.04+) as root
set -e

echo "=== Orbital Industries — Production Deploy ==="

# ── 1. System deps ─────────────────────────────────────────
apt-get update && apt-get install -y \
    curl git docker.io docker-compose-plugin fail2ban ufw \
    && rm -rf /var/lib/apt/lists/*

systemctl enable docker && systemctl start docker

# ── 2. Firewall ────────────────────────────────────────────
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp     # SSH
ufw allow 5001/tcp   # Orbital
ufw allow 443/tcp    # HTTPS (optional, for nginx)
ufw --force enable

# ── 3. Clone / Upload ─────────────────────────────────────
APP_DIR="/opt/orbital"
if [ ! -d "$APP_DIR" ]; then
    mkdir -p "$APP_DIR"
fi

# ── 4. Environment ─────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" << 'ENVEOF'
FIRECRAWL_API_KEY=fc-fb0a013bf5014a268bce13cafb3596d2
TWOCAPTCHA_API_KEY=3640aaca7f962e22486bd52fffd52921
OLLAMA_HOST=https://ollama.com
POSTGRES_PASSWORD=orbital
DATABASE_URL=postgresql://orbital:orbital@db:5432/orbital
AI_PROVIDER=ollama
AI_MODEL=deepseek-v3.1:671b-cloud
OLLAMA_API_KEY=02977ac46ce84f9c801d9837cd36195c.HDnYw5UA7K50P9lMhRmNK1rU
PIPELINE_MAX_CONCURRENT=1
PIPELINE_BATCH_SIZE=20
DISCOVER_INTERVAL_MINUTES=10
PROCESS_INTERVAL_MINUTES=1
DISABLE_LLM_FORMS=true
SECRET_KEY=orbital-industries-2026
BROWSER_HEADLESS=true
ENVEOF
    echo "Created .env"
fi

# ── 5. Build & Start ──────────────────────────────────────
cd "$APP_DIR"
docker compose down 2>/dev/null || true
docker compose build --no-cache
docker compose up -d

# ── 6. Wait for DB ────────────────────────────────────────
echo "Waiting for PostgreSQL..."
for i in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U orbital &>/dev/null; then
        echo "PostgreSQL ready"
        break
    fi
    sleep 2
done

# ── 7. Verify ─────────────────────────────────────────────
sleep 5
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5001/login)
if [ "$STATUS" = "200" ]; then
    echo "=== DEPLOY SUCCESS ==="
    echo "Dashboard: http://$(curl -s ifconfig.me):5001"
    echo "Login: admin / orbital2024"
else
    echo "=== WARNING: Status $STATUS ==="
    docker compose logs --tail=50 orbital
fi

echo ""
echo "Useful commands:"
echo "  docker compose logs -f orbital    # Watch logs"
echo "  docker compose restart orbital    # Restart app"
echo "  docker compose down              # Stop everything"
