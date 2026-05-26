"""
config/settings.py
------------------
Centralised configuration loaded from environment variables.

Budget strategy:
  - Bright Data: $250 credit — FALLBACK ONLY after free APIs fail
  - Gemini API:  Free tier (1500 req/day, 1M TPM) — PRIMARY AI layer
  - All other collectors: Free APIs first, paid as last resort
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Bright Data (PAID — use sparingly, ~$250 budget) ─────────────────────
    BRIGHT_DATA_USERNAME: str = os.getenv("BRIGHT_DATA_USERNAME", "")
    BRIGHT_DATA_PASSWORD: str = os.getenv("BRIGHT_DATA_PASSWORD", "")
    BRIGHT_DATA_HOST: str = os.getenv("BRIGHT_DATA_HOST", "brd.superproxy.io")
    BRIGHT_DATA_CUSTOMER_ID: str = os.getenv("BRIGHT_DATA_CUSTOMER_ID", "")
    WEB_UNLOCKER_PORT: int = int(os.getenv("WEB_UNLOCKER_PORT", "22225"))
    SERP_API_PORT: int = int(os.getenv("SERP_API_PORT", "22226"))
    SCRAPING_BROWSER_CDP_URL: str = os.getenv(
        "SCRAPING_BROWSER_CDP_URL", "wss://brd.superproxy.io:9222"
    )

    # Budget limit (USD) — hard-stop bright data when exceeded
    BRIGHT_DATA_BUDGET_USD: float = float(os.getenv("BRIGHT_DATA_BUDGET_USD", "250.0"))
    # Cost estimates per request type (USD)
    BRIGHT_DATA_COST_WEB_UNLOCKER: float = 0.004   # ~$4/1000 req
    BRIGHT_DATA_COST_SERP: float = 0.005            # ~$5/1000 req
    BRIGHT_DATA_COST_BROWSER_SESSION: float = 0.10  # ~$0.10/session

    # ── Google Gemini (FREE — primary AI layer) ───────────────────────────────
    # Get free key at: https://aistudio.google.com/apikey
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # ── GitHub (FREE with token — 5000 req/hr) ───────────────────────────────
    # Get free token at: https://github.com/settings/tokens
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

    # ── SecurityTrails (PAID — optional, skip if no key) ─────────────────────
    SECURITY_TRAILS_API_KEY: str = os.getenv("SECURITY_TRAILS_API_KEY", "")

    # ── Output / Delivery ─────────────────────────────────────────────────────
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
    HUBSPOT_API_KEY: str = os.getenv("HUBSPOT_API_KEY", "")

    # ── Storage ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://postgres:password@localhost:5432/gtm_intel"
    )

    # ── Async workers ─────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv(
        "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
    )

    # ── Collector tuning ──────────────────────────────────────────────────────
    CT_POLL_INTERVAL: int = int(os.getenv("CT_POLL_INTERVAL", "300"))
    WAYBACK_DIFF_WINDOW_DAYS: int = int(os.getenv("WAYBACK_DIFF_WINDOW_DAYS", "30"))
    GITHUB_SPIKE_THRESHOLD: int = int(os.getenv("GITHUB_SPIKE_THRESHOLD", "10"))
    WAYBACK_MAX_SNAPSHOTS: int = int(os.getenv("WAYBACK_MAX_SNAPSHOTS", "50"))
    MAX_CONCURRENT_COLLECTORS: int = int(os.getenv("MAX_CONCURRENT_COLLECTORS", "10"))

    # ── Misc ──────────────────────────────────────────────────────────────────
    VENDOR_FINGERPRINT_TABLE: str = os.getenv(
        "VENDOR_FINGERPRINT_TABLE", "config/vendor_fingerprints.json"
    )

    @property
    def bright_data_proxy_url(self) -> str:
        return (
            f"http://{self.BRIGHT_DATA_USERNAME}:{self.BRIGHT_DATA_PASSWORD}"
            f"@{self.BRIGHT_DATA_HOST}:{self.WEB_UNLOCKER_PORT}"
        )

    @property
    def bright_data_serp_proxy_url(self) -> str:
        return (
            f"http://{self.BRIGHT_DATA_USERNAME}:{self.BRIGHT_DATA_PASSWORD}"
            f"@{self.BRIGHT_DATA_HOST}:{self.SERP_API_PORT}"
        )

    @property
    def has_bright_data(self) -> bool:
        return bool(self.BRIGHT_DATA_USERNAME and self.BRIGHT_DATA_PASSWORD)

    @property
    def has_gemini(self) -> bool:
        return bool(self.GEMINI_API_KEY)

    @property
    def has_github(self) -> bool:
        return bool(self.GITHUB_TOKEN)

    @property
    def has_security_trails(self) -> bool:
        return bool(self.SECURITY_TRAILS_API_KEY)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
