"""
stage1_signal_collection/github_collector.py
---------------------------------------------
GitHub org activity collector — FREE with personal access token.

GitHub REST API v3 — free tier: 5,000 requests/hour with token.
No Bright Data needed. Direct HTTPS to api.github.com.

Monitors public repositories of known vendor/competitor orgs for:
  • Commit velocity spikes (commits/day > threshold)
  • New repositories going public in the last N days
  • Customer company names appearing in commit messages
  • Customer mentions in README files
  • New contributors joining an org (possible customer onboarding)

Rate-limit aware: tracks X-RateLimit-Remaining header and backs off
automatically when approaching the limit.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

import httpx

from models.signal import Signal, SignalSource, SignalType
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_GITHUB_API = "https://api.github.com"
_RATE_LIMIT_PAUSE_THRESHOLD = 50  # pause if fewer than this many requests remain


# ── Async GitHub REST client (direct, free) ───────────────────────────────────


class GitHubAPIClient:
    """
    Lightweight async GitHub REST v3 client.
    Uses httpx directly — no PyGithub so we can stay async and control rate-limit.
    """

    def __init__(self) -> None:
        self._token = settings.GITHUB_TOKEN
        self._remaining = 5000
        self._reset_at: datetime = datetime.now(timezone.utc)

    def _build_headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GTM-Intel/1.0",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def get(self, path: str, params: dict | None = None) -> dict | list | None:
        """Make a rate-limit-aware GET request to GitHub API."""
        await self._check_rate_limit()

        url = f"{_GITHUB_API}{path}"
        try:
            async with httpx.AsyncClient(headers=self._build_headers(), timeout=20) as client:
                resp = await client.get(url, params=params or {})

                # Update rate-limit tracking from response headers
                self._remaining = int(resp.headers.get("X-RateLimit-Remaining", self._remaining))
                reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
                if reset_ts:
                    self._reset_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc)

                if resp.status_code == 404:
                    logger.debug("[GH-API] 404 for %s", url)
                    return None
                if resp.status_code == 403:
                    logger.warning("[GH-API] 403 (rate limit?) for %s", url)
                    await asyncio.sleep(60)
                    return None

                resp.raise_for_status()
                return resp.json()

        except Exception as exc:
            logger.debug("[GH-API] Request failed for %s: %s", url, exc)
            return None

    async def get_paginated(
        self, path: str, params: dict | None = None, max_pages: int = 5
    ) -> list:
        """Paginate through GitHub API results."""
        all_items: list = []
        params = params or {}
        params.setdefault("per_page", 100)

        for page in range(1, max_pages + 1):
            params["page"] = page
            data = await self.get(path, params)
            if not data or not isinstance(data, list):
                break
            all_items.extend(data)
            if len(data) < params["per_page"]:
                break  # Last page

        return all_items

    async def _check_rate_limit(self) -> None:
        """Pause if approaching GitHub's rate limit."""
        if self._remaining < _RATE_LIMIT_PAUSE_THRESHOLD:
            now = datetime.now(timezone.utc)
            wait = max((self._reset_at - now).total_seconds(), 0) + 5
            logger.warning(
                "[GH-API] Rate limit low (%d remaining) — pausing %.0fs",
                self._remaining, wait,
            )
            await asyncio.sleep(wait)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _commits_per_day(commits: list[dict]) -> float:
    if len(commits) < 2:
        return float(len(commits))
    try:
        dates = [
            datetime.fromisoformat(
                c.get("commit", {}).get("committer", {}).get("date", "")
                .replace("Z", "+00:00")
            )
            for c in commits
            if c.get("commit", {}).get("committer", {}).get("date")
        ]
        if len(dates) < 2:
            return float(len(commits))
        delta_days = max((max(dates) - min(dates)).days, 1)
        return len(commits) / delta_days
    except Exception:
        return float(len(commits))


def _find_customer_mentions(text: str, customer_names: list[str]) -> list[str]:
    text_lower = text.lower()
    return [n for n in customer_names if n.lower() in text_lower]


# ── Org-level signals ─────────────────────────────────────────────────────────


async def _scan_new_repos(
    gh: GitHubAPIClient,
    org: str,
    since: datetime,
) -> list[Signal]:
    """Detect repos created or made public after `since`."""
    repos = await gh.get_paginated(f"/orgs/{org}/repos", {"type": "public", "sort": "created"})
    signals: list[Signal] = []

    for repo in repos or []:
        created_str = repo.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            continue

        if created_at >= since:
            signal = Signal(
                source=SignalSource.GITHUB,
                target_domain=org,
                signal_type=SignalType.NEW_REPO,
                timestamp=created_at,
                raw_data={
                    "repo": repo.get("full_name"),
                    "description": repo.get("description") or "",
                    "language": repo.get("language"),
                    "topics": repo.get("topics", []),
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "url": repo.get("html_url"),
                    "pushed_at": repo.get("pushed_at"),
                },
                confidence=0.65,
                metadata={"org": org},
            )
            signals.append(signal)
            logger.info("[GH] New public repo: %s", repo.get("full_name"))

    return signals


async def _scan_commit_velocity(
    gh: GitHubAPIClient,
    org: str,
    repo_full_name: str,
    since: datetime,
    spike_threshold: int,
    customer_names: list[str],
) -> list[Signal]:
    """Scan a repo for commit spikes and customer name mentions."""
    repo_path = repo_full_name.replace("https://github.com/", "")
    if repo_path.startswith("/"):
        repo_path = repo_path[1:]

    # Get commits since `since`
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = await gh.get_paginated(
        f"/repos/{repo_path}/commits",
        {"since": since_iso},
        max_pages=3,
    )
    signals: list[Signal] = []
    if not commits:
        return signals

    velocity = _commits_per_day(commits)

    if velocity >= spike_threshold:
        signal = Signal(
            source=SignalSource.GITHUB,
            target_domain=org,
            signal_type=SignalType.COMMIT_SPIKE,
            timestamp=datetime.now(timezone.utc),
            raw_data={
                "repo": repo_path,
                "commits_in_window": len(commits),
                "commits_per_day": round(velocity, 2),
                "spike_threshold": spike_threshold,
                "sample_commit": commits[0].get("sha", "")[:8] if commits else "",
            },
            confidence=min(0.40 + velocity / (spike_threshold * 5), 0.90),
            metadata={"org": org, "spike_threshold": spike_threshold},
        )
        signals.append(signal)
        logger.info("[GH] Commit spike in %s: %.1f/day", repo_path, velocity)

    # Scan commit messages for customer names
    if customer_names:
        for commit in commits[:30]:  # cap at 30 to save API quota
            message = commit.get("commit", {}).get("message", "")
            found = _find_customer_mentions(message, customer_names)
            if found:
                ts_str = commit.get("commit", {}).get("committer", {}).get("date", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    ts = datetime.now(timezone.utc)

                signal = Signal(
                    source=SignalSource.GITHUB,
                    target_domain=org,
                    signal_type=SignalType.CUSTOMER_NAME_IN_COMMIT,
                    timestamp=ts,
                    raw_data={
                        "repo": repo_path,
                        "commit_sha": commit.get("sha", "")[:12],
                        "commit_message": message[:500],
                        "matched_customers": found,
                        "commit_url": commit.get("html_url", ""),
                    },
                    confidence=0.78,
                    metadata={"customers_found": found},
                )
                signals.append(signal)
                logger.info("[GH] Customer mention in %s: %s", repo_path, found)

    return signals


async def _scan_readme(
    gh: GitHubAPIClient,
    org: str,
    repo_full_name: str,
    customer_names: list[str],
) -> list[Signal]:
    """Check README for customer name mentions."""
    if not customer_names:
        return []

    repo_path = repo_full_name if "/" in repo_full_name else f"{org}/{repo_full_name}"
    readme = await gh.get(f"/repos/{repo_path}/readme")
    if not readme or not isinstance(readme, dict):
        return []

    import base64
    try:
        content_b64 = readme.get("content", "")
        content = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
    except Exception:
        return []

    found = _find_customer_mentions(content, customer_names)
    if not found:
        return []

    return [Signal(
        source=SignalSource.GITHUB,
        target_domain=org,
        signal_type=SignalType.CUSTOMER_NAME_IN_COMMIT,
        timestamp=datetime.now(timezone.utc),
        raw_data={
            "repo": repo_path,
            "file": "README",
            "matched_customers": found,
            "readme_snippet": content[:400],
            "readme_url": readme.get("html_url", ""),
        },
        confidence=0.82,
        metadata={"source": "readme", "customers_found": found},
    )]


# ── Main collector ────────────────────────────────────────────────────────────


class GitHubOrgCollector:
    """
    Monitor GitHub organisations for intelligence signals.

    Uses free GitHub REST API (5000 req/hr with token).
    Fully async, rate-limit aware, no Bright Data needed.

    Parameters
    ----------
    org_names       : GitHub org handles (e.g. ["salesforce", "hubspot"])
    customer_names  : company names to search for in commits/READMEs
    lookback_days   : how far back to look (default: 30)
    spike_threshold : daily commit rate triggering COMMIT_SPIKE (default: 10)
    max_repos       : max repos to scan per org (default: 20, sort by recently pushed)

    Usage
    -----
        collector = GitHubOrgCollector(
            org_names=["salesforce"],
            customer_names=["Acme Corp", "Globex"],
        )
        signals = await collector.collect_all()
    """

    def __init__(
        self,
        org_names: Iterable[str],
        customer_names: list[str] | None = None,
        lookback_days: int = 30,
        spike_threshold: int | None = None,
        max_repos: int = 20,
    ) -> None:
        self.org_names = list(org_names)
        self.customer_names = customer_names or []
        self.lookback_days = lookback_days
        self.spike_threshold = spike_threshold or settings.GITHUB_SPIKE_THRESHOLD
        self.max_repos = max_repos
        self._gh = GitHubAPIClient()

    async def _scan_org(self, org: str) -> list[Signal]:
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        signals: list[Signal] = []

        # ── 1. New repos
        new_repo_signals = await _scan_new_repos(self._gh, org, since)
        signals.extend(new_repo_signals)

        # ── 2. Get recently-active repos to scan
        repos = await self._gh.get_paginated(
            f"/orgs/{org}/repos",
            {"type": "public", "sort": "pushed", "direction": "desc"},
            max_pages=1,  # One page of 100 = most recently active
        ) or []
        repos = repos[:self.max_repos]

        logger.info("[GH] Scanning %d repos in org %s", len(repos), org)

        # ── 3. Scan commit velocity + customer mentions concurrently
        semaphore = asyncio.Semaphore(5)  # Max 5 parallel repo scans

        async def scan_repo(repo: dict) -> list[Signal]:
            async with semaphore:
                full_name = repo.get("full_name", "")
                if not full_name:
                    return []
                sigs = await _scan_commit_velocity(
                    self._gh, org, full_name, since, self.spike_threshold, self.customer_names
                )
                readme_sigs = await _scan_readme(self._gh, org, full_name, self.customer_names)
                return sigs + readme_sigs

        results = await asyncio.gather(*[scan_repo(r) for r in repos], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                signals.extend(r)
            elif isinstance(r, Exception):
                logger.debug("[GH] Repo scan error: %s", r)

        logger.info("[GH] Org %s → %d signals", org, len(signals))
        return signals

    async def collect_all(self) -> list[Signal]:
        results = await asyncio.gather(
            *[self._scan_org(org) for org in self.org_names],
            return_exceptions=True,
        )
        out: list[Signal] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
            else:
                logger.error("[GH] Org error: %s", r)
        logger.info("[GH] Total: %d GitHub signals", len(out))
        return out


# ── Polling monitor (long-running) ────────────────────────────────────────────


class GitHubPollingMonitor:
    """
    Periodically runs GitHubOrgCollector and fires on_signal callback.
    Designed for long-running pipeline (stream mode).
    """

    def __init__(
        self,
        org_names: Iterable[str],
        customer_names: list[str],
        on_signal: Callable[[Signal], None],
        poll_interval_seconds: int = 3600,
    ) -> None:
        self.org_names = list(org_names)
        self.customer_names = customer_names
        self.on_signal = on_signal
        self.poll_interval = poll_interval_seconds
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info("[GH-MONITOR] Polling %s every %ds", self.org_names, self.poll_interval)
        while self._running:
            try:
                collector = GitHubOrgCollector(
                    org_names=self.org_names,
                    customer_names=self.customer_names,
                )
                for s in await collector.collect_all():
                    self.on_signal(s)
            except Exception as exc:
                logger.error("[GH-MONITOR] Poll error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False
