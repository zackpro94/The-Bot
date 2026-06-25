FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app

# Copy requirements and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
RUN playwright install chromium

# Copy project files
COPY . .

# Expose port (Render overrides this but good practice)
EXPOSE 10000

# Run using the PORT env var assigned by Render (defaults to 10000 if not set)
CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:${PORT:-10000} web_app:app
