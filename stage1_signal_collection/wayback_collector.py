"""
stage1_signal_collection/wayback_collector.py
----------------------------------------------
Wayback Machine CDX API collector — 100% FREE.

The Wayback CDX API is a public service by the Internet Archive.
No API key, no cost, no rate limits (be polite with delays).

Strategy:
  1. Query CDX for all snapshots of interesting paths in a 30-day rolling window
  2. Compare against a local state file to find NET-NEW URLs
  3. Optionally fetch and diff page content for changed pages
  4. Direct requests only — Wayback doesn't block bots (it IS a bot archive)

Interesting paths monitored:
  /integrations*, /docs*, /status*, /pricing*, /partners*, /marketplace*,
  /changelog*, /api*, /ecosystem*, /customers*, /case-study*
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import httpx

from models.signal import Signal, SignalSource, SignalType
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_CDX_URL = "https://web.archive.org/cdx/search/cdx"
_WAYBACK_BASE = "https://web.archive.org/web"

# ── Path pattern → signal type mapping ───────────────────────────────────────

_PATH_RULES: list[tuple[re.Pattern, SignalType]] = [
    (re.compile(r"/integrat",     re.I), SignalType.NEW_INTEGRATION_PAGE),
    (re.compile(r"/partners?",    re.I), SignalType.NEW_INTEGRATION_PAGE),
    (re.compile(r"/marketplace",  re.I), SignalType.NEW_INTEGRATION_PAGE),
    (re.compile(r"/ecosystem",    re.I), SignalType.NEW_INTEGRATION_PAGE),
    (re.compile(r"/docs?/",       re.I), SignalType.NEW_DOCS_PAGE),
    (re.compile(r"/api/",         re.I), SignalType.NEW_DOCS_PAGE),
    (re.compile(r"/status",       re.I), SignalType.NEW_STATUS_PAGE),
    (re.compile(r"/changelog",    re.I), SignalType.PAGE_CONTENT_CHANGE),
    (re.compile(r"/pricing",      re.I), SignalType.PAGE_CONTENT_CHANGE),
    (re.compile(r"/customers?",   re.I), SignalType.PAGE_CONTENT_CHANGE),
    (re.compile(r"/case-stud",    re.I), SignalType.PAGE_CONTENT_CHANGE),
    (re.compile(r"/launch",       re.I), SignalType.NEW_INTEGRATION_PAGE),
    (re.compile(r"/beta",         re.I), SignalType.NEW_INTEGRATION_PAGE),
]


def _classify_url(url: str) -> SignalType | None:
    for pattern, stype in _PATH_RULES:
        if pattern.search(url):
            return stype
    return None


def _url_fingerprint(url: str) -> str:
    return hashlib.sha1(url.lower().encode()).hexdigest()[:12]


def _parse_cdx_timestamp(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


# ── CDX state persistence ─────────────────────────────────────────────────────


class WaybackStateStore:
    """
    JSON file-based state store for seen URL fingerprints.
    In production: replace with Redis HSET / PostgreSQL.
    """

    def __init__(self, state_dir: str = ".wayback_state") -> None:
        self._dir = Path(state_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, domain: str) -> Path:
        return self._dir / f"{domain.replace('.', '_')}.json"

    def load(self, domain: str) -> dict[str, str]:
        """Returns {url_fp: content_digest}."""
        p = self._path(domain)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        return {}

    def save(self, domain: str, state: dict[str, str]) -> None:
        self._path(domain).write_text(json.dumps(state, indent=2))


# ── CDX API client (free) ─────────────────────────────────────────────────────


class WaybackCDXClient:
    """
    Async client for the Wayback Machine CDX Search API.
    Completely free — direct HTTP, no proxy needed.
    """

    _HEADERS = {
        "User-Agent": "GTM-Intel/1.0 (https://github.com/your-org; research)",
        "Accept": "application/json",
    }

    async def fetch_snapshots(
        self,
        domain: str,
        from_dt: datetime,
        to_dt: datetime,
        limit: int = 500,
    ) -> list[dict]:
        """
        Fetch CDX records for *.{domain} within the date range.
        Returns list of dicts with keys: url, timestamp, status, mime, digest.
        """
        params = {
            "url": f"*.{domain}/*",
            "output": "json",
            "from": from_dt.strftime("%Y%m%d"),
            "to": to_dt.strftime("%Y%m%d"),
            "fl": "original,timestamp,statuscode,mimetype,digest",
            "filter": ["statuscode:200", "mimetype:text/html"],
            "collapse": "urlkey",
            "limit": limit,
        }

        try:
            async with httpx.AsyncClient(headers=self._HEADERS, timeout=40) as client:
                resp = await client.get(_CDX_URL, params=params)
                resp.raise_for_status()
                rows = resp.json()

            if not rows or not isinstance(rows[0], list):
                return []

            # First row is header
            header, data = rows[0], rows[1:]
            keys = [h.lower() for h in header]

            result = []
            for row in data:
                if len(row) != len(keys):
                    continue
                entry = dict(zip(keys, row))
                result.append({
                    "url": entry.get("original", ""),
                    "timestamp": entry.get("timestamp", ""),
                    "status": entry.get("statuscode", "200"),
                    "mime": entry.get("mimetype", "text/html"),
                    "digest": entry.get("digest", ""),
                })
            return result

        except Exception as exc:
            logger.error("[WAYBACK-CDX] Request failed for %s: %s", domain, exc)
            return []

    async def fetch_page_content(self, snapshot_url: str, timestamp: str) -> str | None:
        """Retrieve archived HTML from Wayback for content diffing."""
        url = f"{_WAYBACK_BASE}/{timestamp}/{snapshot_url}"
        try:
            async with httpx.AsyncClient(
                headers=self._HEADERS, timeout=20, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.text
        except Exception as exc:
            logger.debug("[WAYBACK-CDX] Content fetch failed: %s", exc)
        return None


# ── Main collector ────────────────────────────────────────────────────────────


class WaybackCollector:
    """
    Wayback Machine CDX-based collector.

    Detects:
      • Brand-new pages appearing under watched paths (high signal)
      • Pages with changed content digests (medium signal)

    Diff window: last 30 days (configurable).
    State is persisted to disk between runs to track what's new.

    100% free — Wayback CDX API is public and open.

    Usage
    -----
        collector = WaybackCollector(["acme.com", "globex.io"])
        signals = await collector.collect_all()
    """

    def __init__(
        self,
        target_domains: Iterable[str],
        diff_window_days: int | None = None,
        diff_content: bool = True,
        max_snapshots: int | None = None,
        state_store: WaybackStateStore | None = None,
    ) -> None:
        self.target_domains = list(target_domains)
        self.diff_window_days = diff_window_days or settings.WAYBACK_DIFF_WINDOW_DAYS
        self.diff_content = diff_content
        self.max_snapshots = max_snapshots or settings.WAYBACK_MAX_SNAPSHOTS
        self._cdx = WaybackCDXClient()
        self._state = state_store or WaybackStateStore()

    async def collect_domain(self, domain: str) -> list[Signal]:
        signals: list[Signal] = []
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=self.diff_window_days)

        seen = self._state.load(domain)
        new_state = dict(seen)

        snapshots = await self._cdx.fetch_snapshots(
            domain, from_dt, now, limit=self.max_snapshots
        )
        logger.info(
            "[WAYBACK] %s → %d CDX rows in %d-day window",
            domain, len(snapshots), self.diff_window_days,
        )

        # Filter to interesting paths only
        interesting = [s for s in snapshots if _classify_url(s["url"])]
        logger.info("[WAYBACK] %s → %d interesting path snapshots", domain, len(interesting))

        changed_pages: list[dict] = []

        for snap in interesting:
            url = snap["url"]
            ts_str = snap["timestamp"]
            digest = snap["digest"]
            fp = _url_fingerprint(url)
            stype = _classify_url(url)

            if fp not in seen:
                # Brand-new URL — high-value signal
                signal = Signal(
                    source=SignalSource.WAYBACK,
                    target_domain=domain,
                    signal_type=stype,
                    timestamp=_parse_cdx_timestamp(ts_str),
                    raw_data={
                        "url": url,
                        "wayback_timestamp": ts_str,
                        "wayback_url": f"{_WAYBACK_BASE}/{ts_str}/{url}",
                        "mime": snap["mime"],
                        "digest": digest,
                    },
                    confidence=0.75,
                    metadata={"is_new_url": True, "path_type": stype.value},
                )
                signals.append(signal)
                new_state[fp] = digest
                logger.info("[WAYBACK] NEW URL: %s", url)

            elif self.diff_content and seen.get(fp) != digest:
                # Content changed — queue for text diff
                changed_pages.append(snap)
                new_state[fp] = digest

        # Fetch content diffs concurrently (cap at 5)
        if changed_pages:
            semaphore = asyncio.Semaphore(5)
            async def _diff_one(snap: dict) -> Signal | None:
                async with semaphore:
                    content = await self._cdx.fetch_page_content(
                        snap["url"], snap["timestamp"]
                    )
                    if content:
                        return Signal(
                            source=SignalSource.WAYBACK,
                            target_domain=domain,
                            signal_type=SignalType.PAGE_CONTENT_CHANGE,
                            timestamp=_parse_cdx_timestamp(snap["timestamp"]),
                            raw_data={
                                "url": snap["url"],
                                "wayback_timestamp": snap["timestamp"],
                                "content_length": len(content),
                                "content_snippet": content[:800],
                                "new_digest": snap["digest"],
                            },
                            confidence=0.60,
                            metadata={"is_content_change": True},
                        )
                    return None

            diff_results = await asyncio.gather(
                *[_diff_one(s) for s in changed_pages[:10]],  # cap at 10 content fetches
                return_exceptions=True,
            )
            for r in diff_results:
                if isinstance(r, Signal):
                    signals.append(r)

        self._state.save(domain, new_state)
        logger.info("[WAYBACK] %s → %d signals emitted", domain, len(signals))
        return signals

    async def collect_all(self) -> list[Signal]:
        results = await asyncio.gather(
            *[self.collect_domain(d) for d in self.target_domains],
            return_exceptions=True,
        )
        out: list[Signal] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
            else:
                logger.error("[WAYBACK] Error: %s", r)
        return out
