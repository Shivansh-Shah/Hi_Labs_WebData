# WHOIS collector — detects newly registered competitor-adjacent domains using python-whois.
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import whois

from models.signal import Signal, SignalSource, SignalType
from stage2_brightdata.mcp_client import BrightDataMCPClient, BudgetExceededError

logger = logging.getLogger(__name__)

# Pull domain names out of any URL string
_DOMAIN_RE = re.compile(r'https?://(?:www\.)?([a-zA-Z0-9][a-zA-Z0-9\-]{0,61}[a-zA-Z0-9]\.[a-zA-Z]{2,})')

# Substrings that indicate a privacy proxy registrant — not a real org name
_PRIVACY_MARKERS = (
    "whoisproxy",
    "whoisguard",
    "privacy",
    "redacted",
    "proxy",
    "protected",
    "contact privacy",
    "domains by proxy",
    "perfect privacy",
    "private registration",
    "data protected",
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_privacy_org(org: str) -> bool:
    """Return True if the org name belongs to a WHOIS privacy proxy service."""
    lower = org.lower()
    return any(marker in lower for marker in _PRIVACY_MARKERS)


def _extract_org(w: Any) -> str | None:
    """
    Return the registrant organisation name from a python-whois result.
    Returns None for empty values AND for known privacy proxy org names —
    both cases should be treated as 'no usable org found'.
    """
    for field in ("org", "registrant_org", "registrant", "registrant_organization"):
        val = getattr(w, field, None)
        if not val:
            continue
        if isinstance(val, list):
            val = val[0]
        val = str(val).strip()
        if not val:
            continue
        # Reject privacy proxies at extraction time so callers never see them
        if _is_privacy_org(val):
            return None
        return val
    return None


def _extract_domains_from_results(
    results: list[dict[str, str]], original_domain: str
) -> list[str]:
    """
    Pull unique domain names from MCP search result links/titles/descriptions.
    Excludes the original competitor domain and its subdomains.
    """
    found: set[str] = set()
    for r in results:
        for text in (r.get("link", ""), r.get("title", ""), r.get("description", "")):
            for m in _DOMAIN_RE.finditer(text):
                d = m.group(1).lower()
                if d and original_domain not in d and d != original_domain:
                    found.add(d)
    return list(found)


def _serialise_whois(w: Any) -> dict[str, Any]:
    """Convert a python-whois result to a JSON-safe dict."""
    raw: dict[str, Any] = {}
    src = w.__dict__ if hasattr(w, "__dict__") else {}
    for k, v in src.items():
        if k.startswith("_"):
            continue
        if isinstance(v, datetime):
            raw[k] = v.isoformat()
        elif isinstance(v, list):
            raw[k] = [
                x.isoformat() if isinstance(x, datetime) else x for x in v
            ]
        else:
            raw[k] = v
    return raw


# ── Collector ─────────────────────────────────────────────────────────────────


class WhoisCollector:
    """
    Detects newly registered domains tied to the same registrant organisation
    as a tracked competitor domain.

    Steps per domain
    ----------------
    1. python-whois lookup to retrieve the registrant org name.
    2. BrightData MCP search: "new domain registered <org> 2026"
    3. Extract domain names from result links.
    4. Emit one Signal per new domain found.

    Budget
    ------
    One MCP search() call per competitor domain.
    Stops immediately if BudgetExceededError is raised.
    """

    def __init__(self, mcp_client: BrightDataMCPClient) -> None:
        self._mcp = mcp_client

    async def collect(self, competitor_domains: list[str]) -> list[Signal]:
        signals: list[Signal] = []
        for domain in competitor_domains:
            try:
                found = await self._check_domain(domain)
                signals.extend(found)
            except BudgetExceededError:
                print(f"[WHOIS] Budget exceeded — stopping collection after {domain}")
                break
            except Exception as exc:
                logger.error("[WHOIS] Unexpected error for %s: %s", domain, exc)
        return signals

    async def _check_domain(self, domain: str) -> list[Signal]:
        print(f"[WHOIS] Checking {domain}...")
        signals: list[Signal] = []

        # Step 1 — WHOIS lookup (blocking I/O → run in thread)
        try:
            w = await asyncio.to_thread(whois.whois, domain)
        except Exception as exc:
            logger.warning("[WHOIS] Lookup failed for %s: %s", domain, exc)
            print(f"[WHOIS] Lookup failed for {domain}: {exc}")
            return signals

        # Check raw org value first so we can log the right reason for skipping
        raw_org: str | None = None
        for field in ("org", "registrant_org", "registrant", "registrant_organization"):
            val = getattr(w, field, None)
            if val:
                raw_org = (val[0] if isinstance(val, list) else str(val)).strip()
                break

        org = _extract_org(w)
        if not org:
            if raw_org and _is_privacy_org(raw_org):
                logger.info("[WHOIS] %s — registrant is privacy-protected, skipping", domain)
                print(f"[WHOIS] {domain} — registrant is privacy-protected, skipping")
            else:
                print(f"[WHOIS] No registrant org found for {domain} — skipping")
            return signals

        print(f"[WHOIS] {domain} → registrant org: {org!r}")

        # Step 2 — MCP search for new domains by same org in last 30 days
        query = f'new domain registered "{org}" 2026'
        results = await self._mcp.search(query, num_results=10)

        # Step 3 — Parse domain names out of search results
        new_domains = _extract_domains_from_results(results, domain)

        if not new_domains:
            print(f"[WHOIS] No new domains found for org {org!r}")
            return signals

        print(f"[WHOIS] {len(new_domains)} new domain(s) for org {org!r}: {new_domains}")

        raw_whois = _serialise_whois(w)

        # Step 4 — Emit one signal per new domain
        for new_domain in new_domains:
            signals.append(Signal(
                source=SignalSource.WHOIS,
                signal_type=SignalType.NEW_DOMAIN_REGISTRATION,
                target_domain=domain,
                timestamp=datetime.now(timezone.utc),
                raw_data={**raw_whois, "new_domain_found": new_domain, "org": org},
                confidence=0.7,
                vendor_hint=new_domain,
            ))

        return signals
