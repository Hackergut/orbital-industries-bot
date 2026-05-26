FROM python:3.11-slim

WORKDIR /app

# Install system deps for Playwright + virtual display
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libxshmfence1 \
    libgtk-3-0 \
    xdg-utils \
    xvfb \
    fluxbox \
    scrot \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

# Copy app code
COPY . .

# Create directories
RUN mkdir -p static/screenshots logs browser_data instance

# Environment
ENV PYTHONUNBUFFERED=1
ENV BROWSER_HEADLESS=true
ENV BROWSER_POOL_SIZE=2
ENV PIPELINE_MAX_CONCURRENT=2

EXPOSE 5000

# Default: run web server
CMD ["python", "run.py"]
