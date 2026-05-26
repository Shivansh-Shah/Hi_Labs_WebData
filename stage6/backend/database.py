"""
stage6/backend/database.py
---------------------------
SQLAlchemy engine + session factory.

Primary: Supabase PostgreSQL (SSL required).
Fallback: SQLite for local dev when no PostgreSQL is reachable.

The engine is built with URL.create() so passwords with special characters
(@ [ ] etc.) never need manual URL-encoding in the .env file — just set:

    DATABASE_URL=postgresql://postgres:Chiyo%40Chiko@db.xxx.supabase.co:5432/postgres

Run from project root:
    uvicorn stage6.backend.main:app --reload
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

_RAW_URL = os.getenv(
    "DATABASE_URL",
    # port 6543 = Supabase pgBouncer pooler (port 5432 direct is IPv6-only on some networks)
    "postgresql://postgres:Chiyo%40Chiko@db.ewxkfqovkrfobajjgzcm.supabase.co:6543/postgres",
)


def _build_engine(raw_url: str):
    """
    Parse DATABASE_URL and build a SQLAlchemy engine.

    Uses URL.create() to safely handle passwords with special characters
    (@ [ ] # etc.) without requiring double-encoding in .env.
    """
    parsed = urlparse(raw_url)

    is_postgres = parsed.scheme.startswith("postgresql") or parsed.scheme.startswith("postgres")

    if not is_postgres:
        # SQLite or other — pass through directly
        return create_engine(raw_url, connect_args={"check_same_thread": False}, echo=False)

    # Decode percent-encoding so psycopg2 receives the raw password
    password = unquote(parsed.password or "")
    username = unquote(parsed.username or "")
    database = parsed.path.lstrip("/")

    url = URL.create(
        drivername="postgresql+psycopg2",
        username=username,
        password=password,          # raw — SQLAlchemy handles escaping internally
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=database,
    )

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=300,           # recycle every 5 min (Supabase idle limit)
        connect_args={"sslmode": "require"},   # pgBouncer: no extra options
        echo=False,
    )


# ── Engine ────────────────────────────────────────────────────────────────────

try:
    engine = _build_engine(_RAW_URL)
    with engine.connect() as _conn:
        _conn.execute(text("SELECT 1"))
    _host = urlparse(_RAW_URL).hostname or _RAW_URL
    logger.info("[DB] ✓ Connected to %s", _host)

except Exception as _pg_err:
    _sqlite_url = "sqlite:///./stage6/gtm_intel.db"
    logger.warning(
        "[DB] PostgreSQL unavailable (%s) — falling back to SQLite at %s",
        str(_pg_err).split("\n")[0], _sqlite_url,
    )
    _RAW_URL = _sqlite_url
    engine = create_engine(
        _sqlite_url,
        connect_args={"check_same_thread": False},
        echo=False,
    )

DATABASE_URL = _RAW_URL

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ────────────────────────────────────────────────────────


def get_db():
    """Yield a SQLAlchemy session, close it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
