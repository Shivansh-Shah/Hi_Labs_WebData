# Robots.txt collector — fetches robots.txt via Bright Data MCP and diffs against cached version.
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from models.signal import Signal, SignalSource, SignalType
from stage2_brightdata.mcp_client import BrightDataMCPClient, BudgetExceededError

logger = logging.getLogger(__name__)

_STATE_DIR = Path(__file__).parent.parent / "state"
_CACHE_FILE = _STATE_DIR / "robots_cache.json"

# Newly-added Disallow paths matching these prefixes indicate unreleased products
_SUSPICIOUS_RE = re.compile(
    r"/(new-|coming-|beta-|launch-|preview-|product-|announce-)",
    re.IGNORECASE,
)


# ── Cache helpers ─────────────────────────────────────────────────────────────


def _load_cache() -> dict[str, str]:
    """Return the robots_cache dict, or an empty dict if the file doesn't exist."""
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[ROBOTS] Could not read cache: %s", exc)
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    """Persist the updated cache to disk, creating state/ if needed."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _CACHE_FILE.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("[ROBOTS] Cache saved (%d entries)", len(cache))
    except Exception as exc:
        logger.warning("[ROBOTS] Could not write cache: %s", exc)


# ── robots.txt parser ─────────────────────────────────────────────────────────


def _parse_disallow_paths(robots_txt: str) -> set[str]:
    """
    Extract all Disallow: values from a robots.txt string.
    Handles both plain text and markdown code-block wrapping from scrape_as_markdown.
    """
    # Strip markdown fences if Bright Data wrapped it (```...```)
    cleaned = re.sub(r"```[^\n]*\n(.*?)```", r"\1", robots_txt, flags=re.DOTALL)
    paths: set[str] = set()
    for line in cleaned.splitlines():
        line = line.strip()
        if line.lower().startswith("disallow:"):
            path = line[9:].split("#")[0].strip()
            if path:
                paths.add(path)
    return paths


# ── Collector ─────────────────────────────────────────────────────────────────


class RobotsTxtCollector:
    """
    Detects newly-added suspicious Disallow paths in competitor robots.txt files.

    Per domain
    ----------
    1. Fetch robots.txt via BrightData MCP scrape_as_markdown (bypasses bot blocks).
    2. Diff against the previous version stored in state/robots_cache.json.
    3. Flag any NEW Disallow paths matching /new-, /coming-, /beta-, /launch-,
       /preview-, /product-, /announce-.
    4. Emit a Signal if suspicious new paths are found.
    5. Always save the latest robots.txt to cache regardless.

    Budget
    ------
    One MCP scrape_url() call per competitor domain.
    Stops immediately and saves cache if BudgetExceededError is raised.
    """

    def __init__(self, mcp_client: BrightDataMCPClient) -> None:
        self._mcp = mcp_client

    async def collect(self, competitor_domains: list[str]) -> list[Signal]:
        cache = _load_cache()
        signals: list[Signal] = []

        for domain in competitor_domains:
            try:
                found, cache = await self._check_domain(domain, cache)
                signals.extend(found)
            except BudgetExceededError:
                print(f"[ROBOTS] Budget exceeded — stopping collection at {domain}")
                break
            except Exception as exc:
                logger.error("[ROBOTS] Unexpected error for %s: %s", domain, exc)
                print(f"[ROBOTS] Error checking {domain}: {exc}")

        _save_cache(cache)
        return signals

    async def _check_domain(
        self, domain: str, cache: dict[str, str]
    ) -> tuple[list[Signal], dict[str, str]]:
        print(f"[ROBOTS] Fetching https://{domain}/robots.txt via Bright Data MCP...")
        signals: list[Signal] = []

        current_robots = await self._mcp.scrape_url(f"https://{domain}/robots.txt")

        if not current_robots:
            print(f"[ROBOTS] Empty response for {domain} — skipping diff")
            # Keep old cache entry; don't overwrite with empty
            return signals, cache

        previous_robots = cache.get(domain, "")
        previous_paths = _parse_disallow_paths(previous_robots)
        current_paths = _parse_disallow_paths(current_robots)

        new_paths = current_paths - previous_paths
        suspicious = sorted(p for p in new_paths if _SUSPICIOUS_RE.search(p))

        print(
            f"[ROBOTS] {domain} — {len(current_paths)} total disallow paths, "
            f"{len(new_paths)} new since last check, {len(suspicious)} suspicious"
        )

        if suspicious:
            print(f"[ROBOTS] Suspicious new paths on {domain}: {suspicious}")
            signals.append(Signal(
                source=SignalSource.ROBOTS_TXT,
                signal_type=SignalType.NEW_BLOCKED_PATH,
                target_domain=domain,
                timestamp=datetime.now(timezone.utc),
                raw_data={
                    "new_paths": suspicious,
                    "previous_robots": previous_robots,
                    "current_robots": current_robots,
                },
                confidence=0.8,
                vendor_hint=suspicious[0],
            ))

        # Always update cache with the latest version
        cache[domain] = current_robots
        return signals, cache
