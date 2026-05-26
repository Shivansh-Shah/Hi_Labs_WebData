"""
stage6/backend/main.py
------------------------
FastAPI application entry point.

Start the server from the project root:
    uvicorn stage6.backend.main:app --reload --port 8000

Swagger UI: http://localhost:8000/docs
ReDoc:       http://localhost:8000/redoc
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# Ensure the project root is importable (pipeline.*, models.*, etc.)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .database import Base, engine
from .routes.deals import router as deals_router
from .routes.digest import router as digest_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create all tables on startup (no-op if they already exist)."""
    logger.info("[STARTUP] Creating tables if needed…")
    Base.metadata.create_all(bind=engine)
    logger.info("[STARTUP] Ready — http://localhost:8000/docs")
    yield
    logger.info("[SHUTDOWN] GTM Intelligence Platform stopped")


# ── App ───────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="GTM Intelligence Platform",
    description=(
        "B2B sales intelligence from public web signals.\n\n"
        "**Stages**: CT logs → DNS → Wayback → GitHub → Bright Data → "
        "Parsing → Correlation → AI Enrichment → Delivery"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow Vite dev server (5173) and any localhost port
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deals_router,  prefix="/api")
app.include_router(digest_router, prefix="/api")


# ── Health & root ─────────────────────────────────────────────────────────────


@app.get("/health", tags=["meta"])
def health_check():
    return {"status": "ok", "service": "GTM Intelligence Platform", "version": "1.0.0"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    return """
    <html><body style="font-family:monospace;padding:32px;background:#0f0f0f;color:#e5e5e5">
      <h2>GTM Intelligence Platform API</h2>
      <p>
        <a href="/docs"  style="color:#60a5fa">/docs</a> — Swagger UI<br>
        <a href="/redoc" style="color:#60a5fa">/redoc</a> — ReDoc<br>
        <a href="/api/stats" style="color:#60a5fa">/api/stats</a> — live stats
      </p>
      <p style="color:#6b7280;font-size:13px">
        Frontend: <code>cd stage6/frontend && npm i && npm run dev</code>
      </p>
    </body></html>
    """
