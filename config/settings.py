"""
config/settings.py
------------------
Centralised configuration loaded from environment variables.

Bright Data strategy:
  - MCP server  : https://mcp.brightdata.com/mcp?token=...  (Claude Code tools)
  - REST API    : POST https://api.brightdata.com/request   (Python code)
  - SERP zone   : handles both Google SERP and general content fetching
  - Web Unlocker: not available — SERP zone used as the single BD gateway
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Bright Data — REST API + MCP (primary, bearer token auth) ────────────
    # Get API token: https://brightdata.com → Account Settings → API token
    BRIGHT_DATA_API_KEY: str = os.getenv("BRIGHT_DATA_API_KEY", "")
    # Active zones — SERP zone is the primary gateway (web_unlocker not available)
    BRIGHT_DATA_WEB_UNLOCKER_ZONE: str = os.getenv("BRIGHT_DATA_WEB_UNLOCKER_ZONE", "")
    BRIGHT_DATA_SERP_ZONE: str = os.getenv("BRIGHT_DATA_SERP_ZONE", "serp_api")
    BRIGHT_DATA_SCRAPING_BROWSER_ZONE: str = os.getenv("BRIGHT_DATA_SCRAPING_BROWSER_ZONE", "")
    # Hosted MCP server URL — used by Claude Code agent for direct tool access
    BRIGHT_DATA_MCP_URL: str = os.getenv("BRIGHT_DATA_MCP_URL", "")

    # ── Bright Data — legacy proxy credentials (fallback if REST API fails) ───
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

    # ── OpenAI (Stage 5 AI Enrichment — gpt-4o-mini) ─────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_MAX_TOKENS: int = int(os.getenv("OPENAI_MAX_TOKENS", "600"))

    # ── Google Gemini (MCP agent) ─────────────────────────────────────────────
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
    def bright_data_active_zone(self) -> str:
        """Return the best available BD zone — SERP first, then Web Unlocker."""
        return self.BRIGHT_DATA_SERP_ZONE or self.BRIGHT_DATA_WEB_UNLOCKER_ZONE or ""

    @property
    def bright_data_mcp_url(self) -> str:
        """Full BD hosted MCP server URL with auth token embedded."""
        if self.BRIGHT_DATA_MCP_URL:
            return self.BRIGHT_DATA_MCP_URL
        if self.BRIGHT_DATA_API_KEY:
            return f"https://mcp.brightdata.com/mcp?token={self.BRIGHT_DATA_API_KEY}"
        return ""

    @property
    def has_bright_data(self) -> bool:
        return bool(self.BRIGHT_DATA_API_KEY or (self.BRIGHT_DATA_USERNAME and self.BRIGHT_DATA_PASSWORD))

    @property
    def has_bright_data_mcp(self) -> bool:
        return bool(self.BRIGHT_DATA_API_KEY)

    @property
    def has_openai(self) -> bool:
        return bool(self.OPENAI_API_KEY)

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
