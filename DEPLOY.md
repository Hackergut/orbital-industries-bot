# 🚀 Orbital Industries — Deploy Guide

## 4 modi per rendere la piattaforma accessibile da terzi

---

## 🟢 Opzione A: Cloudflare Tunnel (Consigliato — Gratuito, HTTPS)

**Perfetto per:** accesso immediato, dominio personalizzato, nessun VPS.

### Prerequisiti
- Dominio aggiunto a Cloudflare DNS (piano gratuito va bene)
- Docker Compose in esecuzione

### Comandi

```bash
# 1. Assicurati che lo stack locale sia up
docker compose -f docker-compose.prod.yml up -d

# 2. Avvia il tunnel (sostituisci con il tuo dominio)
./cloudflare-tunnel.sh orbital.miodominio.com
```

Lo script ti guida passo-passo: installa `cloudflared`, autentica, crea il tunnel e instrada il DNS. Alla fine avrai:

```
https://orbital.miodominio.com/dashboard/
```

Con **HTTPS automatico**, **rate limiting**, e **nessuna porta aperta** sul tuo PC.

### Per renderlo permanente (Linux/macOS)

```bash
# Dopo aver creato il tunnel, installa come servizio
sudo cloudflared service install <TUNNEL-ID>
sudo systemctl start cloudflared   # Linux
sudo launchctl load ...            # macOS (segui output dello script)
```

---

## 🟡 Opzione B: VPS (Hetzner / DigitalOcean / AWS / Linode)

**Perfetto per:** produzione stabile, controllo totale, IP dedicato.

### 1. Compra un VPS (consigliato: 2vCPU/4GB RAM minimo)

### 2. SSH nel server e lancia lo script

```bash
cd /opt
git clone <repo-url> orbital  # o upload via SCP

cd orbital
./deploy-vps.sh tuo-dominio.com
```

Lo script:
- Installa Docker, Docker Compose, nginx, certbot, fail2ban
- Genera una `SECRET_KEY` casuale
- Avvia lo stack prod
- Rilascia certificato SSL Let's Encrypt

### 3. Post-deploy

```bash
# Logs
cd /opt/orbital && docker compose -f docker-compose.prod.yml logs -f

# Restart
cd /opt/orbital && docker compose -f docker-compose.prod.yml restart

# Aggiorna password admin
nano .env   # modifica ADMIN_PASSWORD, poi restart
```

---

## 🟠 Opzione C: ngrok (Temporaneo — 1 minuto)

**Perfetto per:** test rapidi, demo al volo, condivisione con un cliente.

```bash
# 1. Avvia lo stack
docker compose -f docker-compose.prod.yml up -d

# 2. Tunnel pubblico
./ngrok-quick.sh
```

Otterrai un URL tipo:
```
https://a1b2c3d4.ngrok-free.app/dashboard/
```

**Nota:** l'URL cambia ad ogni riavvio (a meno che non compri ngrok Pro).

---

## 🔵 Opzione D: Railway / Render (Platform-as-a-Service)

**Perfetto per:** zero manutenzione server, deploy da Git.

Già presente `railway.toml`. Vai su [railway.app](https://railway.app) o [render.com](https://render.com), collega il repo e deploya.

**Attenzione:** per il browser headless + Selenium + Crawl4AI, un PaaS potrebbe avere limitazioni. VPS o Cloudflare Tunnel sono più affidabili per questo stack.

---

## 🔐 Sicurezza in produzione — CHECKLIST

Prima di dare accesso a terzi, verifica:

| Check | Come fare |
|-------|-----------|
| **Cambia password admin** | Modifica `ADMIN_PASSWORD` in `.env` |
| **Cambia SECRET_KEY** | `openssl rand -hex 32` → `.env` |
| **Disabilita bypass locale** | `ADMIN_BYPASS_LOCAL=false` |
| **Blocco IP su fail2ban** | Già attivo con `deploy-vps.sh` |
| **Rate limiting nginx** | Già in `nginx.conf` (10 req/s) |
| **Nessun file sensibile esposto** | `nginx.conf` blocca `.env`, `.git`, etc. |
| **HTTPS forzato** | Cloudflare Tunnel e VPS (certbot) lo fanno automaticamente |

---

## 📁 File di deploy creati

| File | Scopo |
|------|-------|
| `docker-compose.prod.yml` | Stack produzione (web + redis + crawl4ai + nginx) |
| `nginx.conf` | Reverse proxy + sicurezza + rate limit |
| `.env.production` | Template env con valori sicuri di default |
| `cloudflare-tunnel.sh` | Setup automatico Cloudflare Tunnel |
| `ngrok-quick.sh` | Tunnel temporaneo ngrok |
| `deploy-vps.sh` | Deploy completo su VPS Ubuntu |
| `DEPLOY.md` | Questa guida |

---

## 🆘 Troubleshooting

### "tcp connect error" (temporal-worker)

Il worker Temporal è **disabilitato** in produzione perché non c'è un server Temporal. Questo errore è normale se il worker vecchio è ancora in cache. Ignoralo o rimuovi il vecchio container:

```bash
docker compose down
docker compose -f docker-compose.prod.yml up -d
```

### Dashboard non carica da remoto

Verifica:
```bash
curl http://localhost:5000/api/pipeline/status   # locale
curl http://TUO-IP/api/pipeline/status              # remoto (se VPS)
```

Se il locale funziona ma il remoto no → problema firewall/nginx/tunnel.

### Certbot / SSL fallisce

Assicurati che il dominio punti al VPS (DNS A record). Poi:
```bash
sudo certbot --nginx -d tuo-dominio.com
```

---

**Hai scelto un'opzione? Dimmi quale e ti guido passo-passo.**
