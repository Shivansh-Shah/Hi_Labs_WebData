FROM python:3.11-slim

# System libs needed by psycopg2, playwright, and spacy
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install full pipeline deps on Python 3.11 (all wheels available)
COPY requirements.txt requirements-backend.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefer-binary -r requirements.txt

# Download spacy English model
RUN python -m spacy download en_core_web_sm || true

# Install playwright browsers (needed by Stage 2 scraper)
RUN playwright install chromium --with-deps || true

# Copy the full repo (pipeline.* needs all stage files)
COPY . .

# Render injects $PORT; default 10000 for local docker run
EXPOSE 10000
CMD ["sh", "-c", "uvicorn stage6.backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
