FROM python:3.11-slim

# System libs needed by psycopg2 and httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install only backend-specific deps (no pandas/spacy/networkx to compile)
COPY requirements-backend.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-backend.txt

# Copy the full repo (Launch Sniper subprocess needs the idea 2/ tree)
COPY . .

# Render injects $PORT; default 10000 for local docker run
EXPOSE 10000
CMD ["sh", "-c", "uvicorn stage6.backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
