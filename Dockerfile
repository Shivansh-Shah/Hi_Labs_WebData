FROM python:3.11-slim

# System libs needed by psycopg2, spaCy, and pandas wheel builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (separate layer so rebuilds are fast on code changes) ──────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── spaCy language model (optional — falls back to regex if absent) ────────────
RUN python -m spacy download en_core_web_sm || true

# ── Copy full repo (Launch Sniper subprocess needs idea 2/ tree) ───────────────
COPY . .

# Render injects $PORT at runtime; fall back to 10000 for local docker run
EXPOSE 10000
CMD ["sh", "-c", "uvicorn stage6.backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
