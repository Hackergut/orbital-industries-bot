# Orbital Industries — AI Form Submission Bot

Automated outreach bot that discovers hedge funds, family offices, crypto firms, and venture capital companies, then fills and submits their contact forms using AI.

## Features

- **Target Discovery**: Automatically finds contact forms via DuckDuckGo/Firecrawl
- **AI Form Mapping**: Uses Ollama (local LLM) to intelligently map form fields
- **Browser Automation**: Playwright with stealth + browser pool for parallel processing
- **CAPTCHA Bypass**: 2Captcha integration for reCAPTCHA v2/v3 and hCaptcha
- **Live Dashboard**: Real-time screenshot + log monitoring
- **Redis Cache**: Caches LLM responses to reduce latency
- **High Volume**: Designed for 1000–2000 submissions/day

## Quick Start

### 1. Install dependencies

```bash
cd "/Volumes/HRD2T/ORBITAL TECH 2"
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Start Ollama (local AI)

```bash
ollama run llama3.1:8b
```

### 3. Start the web dashboard

```bash
python run.py
```

Open `http://localhost:5000/` in your browser.

### 4. Start the pipeline (in a second terminal)

```bash
python pipeline_runner.py
```

This processes targets continuously. Keep both terminals open.

### 5. Login

- Username: `admin`
- Password: `orbital2024`

## Configuration

Edit `.env` or `app/config.py`:

```python
COMPANY_DATA = {
    "company": "Orbital Industries Limited",
    "company_url": "https://orbitaltech.pro",
    "email": "contact@orbitaltech.pro",
    # ... etc
}
```

Set your 2Captcha key:
```bash
export TWOCAPTCHA_API_KEY="your-key-here"
```

## Architecture

| Component | File | Purpose |
|---|---|---|
| Web API | `app/main.py` | FastAPI + Uvicorn server |
| Pipeline | `app/pipeline_async.py` | Core automation logic |
| Browser Pool | `app/browser_async.py` | Playwright async pool |
| AI Engine | `app/ai_engine.py` | Ollama + caching |
| Dashboard | `app/live.py` | Real-time monitoring |
| Runner | `pipeline_runner.py` | Standalone batch processor |

## Deployment

For production, run both services as systemd services or use Docker Compose:

```bash
docker-compose up --build
```

## License

Private — Orbital Industries Limited
