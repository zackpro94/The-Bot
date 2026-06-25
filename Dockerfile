FROM python:3.10-slim

# Install system dependencies needed for Playwright Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk-1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    librandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (specifically chromium)
RUN playwright install chromium

# Copy project files
COPY . .

# Expose port (Render overrides this but good practice)
EXPOSE 10000

# Run using the PORT env var assigned by Render (defaults to 10000 if not set)
CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:${PORT:-10000} web_app:app
