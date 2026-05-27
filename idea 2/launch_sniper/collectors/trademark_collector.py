# Trademark collector — queries USPTO PAIR API for recent filings by tracked competitor names.
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from models.signal import Signal, SignalSource, SignalType

logger = logging.getLogger(__name__)

_USPTO_URL = "https://developer.uspto.gov/ibd-api/v1/application/searchTrademarks"
_TIMEOUT = 20.0
_RETRY_STATUSES = {429, 500, 502, 503, 504}   # transient errors worth retrying
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0                         # seconds; doubles each attempt


# ── Helpers ───────────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Convert a competitor name to a URL-safe slug for use as target_domain."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _parse_filings(data: dict | list) -> list[dict]:
    """
    Extract a flat list of filing records from the USPTO API response.
    Handles several common response shapes:
      - Plain list
      - {"trademarks": [...]} / {"results": [...]} / {"applications": [...]}
      - Elasticsearch-style {"hits": {"hits": [{"_source": {...}}]}}
    """
    if isinstance(data, list):
        return data

    for key in ("trademarks", "results", "applications", "hits", "items", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return val

    # Elasticsearch nested hits
    hits = data.get("hits", {})
    if isinstance(hits, dict):
        inner = hits.get("hits", [])
        if isinstance(inner, list):
            return [h.get("_source", h) for h in inner]

    return []


def _parse_timestamp(filing: dict) -> datetime:
    """Best-effort extraction of a filing date from a USPTO record."""
    for key in ("filingDate", "applicationDate", "dateOfApplication", "date"):
        raw = filing.get(key, "")
        if raw:
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
    return datetime.now(timezone.utc)


# ── Collector ─────────────────────────────────────────────────────────────────


class TrademarkCollector:
    """
    Detects recent trademark filings by competitor organisations.

    Queries the free USPTO IBD API — no API key required.
    Looks back 60 days from today for filings whose applicant name
    matches a tracked competitor name.

    One httpx request per competitor name; all requests share a
    single AsyncClient for connection reuse.
    """

    async def collect(self, competitor_names: list[str]) -> list[Signal]:
        today = datetime.now(timezone.utc)
        date_start = (today - timedelta(days=60)).strftime("%Y-%m-%d")
        date_end = today.strftime("%Y-%m-%d")

        signals: list[Signal] = []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for name in competitor_names:
                try:
                    found = await self._check_name(client, name, date_start, date_end)
                    signals.extend(found)
                except Exception as exc:
                    logger.error("[TRADEMARK] Error for %r: %s", name, exc)
                    print(f"[TRADEMARK] Error checking {name!r}: {exc}")

        return signals

    async def _check_name(
        self,
        client: httpx.AsyncClient,
        name: str,
        date_start: str,
        date_end: str,
    ) -> list[Signal]:
        print(f"[TRADEMARK] Searching USPTO for {name!r} ({date_start} → {date_end})...")
        signals: list[Signal] = []
        data: dict | list | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.get(
                    _USPTO_URL,
                    params={
                        "query": name,
                        "dateRangeStart": date_start,
                        "dateRangeEnd": date_end,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                break  # success — exit retry loop
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "[TRADEMARK] USPTO HTTP %d for %r — retry %d/%d in %.0fs",
                        status, name, attempt + 1, _MAX_RETRIES, delay,
                    )
                    print(
                        f"[TRADEMARK] USPTO HTTP {status} for {name!r} — "
                        f"retry {attempt + 1}/{_MAX_RETRIES} in {delay:.0f}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning("[TRADEMARK] USPTO HTTP %d for %r — giving up", status, name)
                    print(f"[TRADEMARK] USPTO HTTP {status} for {name!r} — giving up")
                    return signals
            except Exception as exc:
                logger.warning("[TRADEMARK] Request failed for %r: %s", name, exc)
                print(f"[TRADEMARK] Request failed for {name!r}: {exc}")
                return signals

        if data is None:
            return signals

        filings = _parse_filings(data)
        print(f"[TRADEMARK] {name!r} → {len(filings)} filing(s) found")

        slug = _slugify(name)
        for filing in filings:
            mark_name = (
                filing.get("markName")
                or filing.get("mark")
                or filing.get("trademarkName")
                or name
            )
            signals.append(Signal(
                source=SignalSource.TRADEMARK,
                signal_type=SignalType.TRADEMARK_FILING,
                target_domain=slug,
                timestamp=_parse_timestamp(filing),
                raw_data=filing,
                confidence=0.75,
                vendor_hint=str(mark_name),
            ))

        return signals
