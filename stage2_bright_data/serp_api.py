"""
stage2_bright_data/serp_api.py
--------------------------------
SERP intelligence layer.

Primary strategy:
  1. Bright Data SERP (primary)  — Google SERP via REST API, budget-gated
  2. DuckDuckGo HTML scraping    — free fallback if BD unavailable/fails

Routes Google searches through Bright Data's Web Unlocker or SERP zone
(configured via BRIGHT_DATA_SERP_ZONE or BRIGHT_DATA_WEB_UNLOCKER_ZONE).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from config.settings import get_settings
from stage2_bright_data.budget_guard import BudgetGuard, BDRequestType

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class OrganicResult:
    position: int
    title: str
    url: str
    domain: str
    snippet: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SERPResponse:
    query: str
    total_results: int
    organic: list[OrganicResult]
    source: str = "unknown"

    @property
    def urls(self) -> list[str]:
        return [r.url for r in self.organic]

    @property
    def domains(self) -> set[str]:
        return {r.domain for r in self.organic}


def _extract_domain(url: str) -> str:
    m = re.match(r"https?://([^/?\s]+)", url)
    return m.group(1).lower() if m else ""


# ── Source 1: DuckDuckGo (FREE, no key) ──────────────────────────────────────


class DuckDuckGoSearchClient:
    """
    Free SERP via DuckDuckGo HTML search.
    No API key, no rate limit (be polite with delays).
    Parses the DDG HTML result page to extract organic URLs.
    """

    _DDG_URL = "https://html.duckduckgo.com/html/"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def search(self, query: str, max_results: int = 30) -> SERPResponse:
        """Search DuckDuckGo and return parsed organic results."""
        try:
            async with httpx.AsyncClient(
                headers=self._HEADERS,
                timeout=20,
                follow_redirects=True,
            ) as client:
                resp = await client.post(
                    self._DDG_URL,
                    data={"q": query, "b": "", "kl": "us-en"},
                )
                if resp.status_code != 200:
                    return SERPResponse(query=query, total_results=0, organic=[])

                return self._parse_html(query, resp.text, max_results)

        except Exception as exc:
            logger.debug("[DDG] Search failed for '%s': %s", query, exc)
            return SERPResponse(query=query, total_results=0, organic=[])

    @staticmethod
    def _parse_html(query: str, html: str, max_results: int) -> SERPResponse:
        """
        Extract result links from DDG HTML response.
        DDG uses <a class="result__url"> or <a class="result__a"> for results.
        """
        # Extract all URLs from result links
        results: list[OrganicResult] = []

        # DDG result URL pattern
        url_pattern = re.compile(
            r'class="result__a"[^>]*href="(https?://[^"]+)"',
            re.I,
        )
        title_url_pattern = re.compile(
            r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
            re.I,
        )
        snippet_pattern = re.compile(
            r'class="result__snippet"[^>]*>([^<]+(?:<[^>]+>[^<]*</[^>]+>)*[^<]*)</[^>]+>',
            re.I,
        )

        # Simpler extraction: find all href in result blocks
        block_pattern = re.compile(
            r'<div class="result[^"]*".*?</div>\s*</div>',
            re.S | re.I,
        )

        # Fallback: just extract all external URLs from the page
        all_urls = re.findall(
            r'href="(https?://(?!duckduckgo\.com)[^"]+)"',
            html,
        )

        seen: set[str] = set()
        for i, url in enumerate(all_urls[:max_results * 2]):
            if url in seen:
                continue
            # Filter out DDG utility links
            if any(skip in url for skip in ["duckduckgo.com", "duck.com"]):
                continue
            seen.add(url)
            results.append(
                OrganicResult(
                    position=len(results) + 1,
                    title="",
                    url=url,
                    domain=_extract_domain(url),
                )
            )
            if len(results) >= max_results:
                break

        return SERPResponse(
            query=query,
            total_results=len(results),
            organic=results,
            source="duckduckgo",
        )


# ── Source 2: Bright Data SERP (primary, REST API) ────────────────────────────

_BD_REST_API = "https://api.brightdata.com/request"


class BrightDataSERPClient:
    """
    Bright Data SERP via REST API — Google results with anti-bot bypass.
    Uses bearer token authentication (BRIGHT_DATA_API_KEY).

    Routes Google searches through the configured SERP zone (or Web Unlocker
    zone as fallback) and requests structured JSON results via brd_json=1.
    """

    _GOOGLE_SEARCH_URL = "https://www.google.com/search"

    async def search(
        self, query: str, num_results: int = 30, country: str = "us"
    ) -> SERPResponse:
        guard = BudgetGuard.get_instance()
        if not guard.can_spend(BDRequestType.SERP):
            logger.warning("[BD-SERP] Budget gate blocked search for '%s'", query)
            return SERPResponse(query=query, total_results=0, organic=[], source="blocked_budget")

        if not settings.BRIGHT_DATA_API_KEY:
            logger.warning("[BD-SERP] No BRIGHT_DATA_API_KEY configured")
            return SERPResponse(query=query, total_results=0, organic=[], source="no_key")

        guard.record_spend(BDRequestType.SERP)

        # Build Google URL with structured JSON output flag
        encoded_query = query.replace(" ", "+")
        google_url = (
            f"{self._GOOGLE_SEARCH_URL}"
            f"?q={encoded_query}&num={num_results}&gl={country}&brd_json=1"
        )

        # Use SERP zone (only active zone — web_unlocker not available)
        zones_to_try = [z for z in [settings.BRIGHT_DATA_SERP_ZONE] if z]

        for zone in zones_to_try:
            result = await self._fetch_serp(query, google_url, zone, num_results)
            if result is not None:
                return result

        logger.error("[BD-SERP] All zones failed for '%s'", query)
        return SERPResponse(query=query, total_results=0, organic=[], source="error")

    async def _fetch_serp(
        self, query: str, google_url: str, zone: str, num_results: int
    ) -> SERPResponse | None:
        """Attempt a SERP fetch through a specific Bright Data zone."""
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                resp = await client.post(
                    _BD_REST_API,
                    headers={
                        "Authorization": f"Bearer {settings.BRIGHT_DATA_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"zone": zone, "url": google_url, "format": "raw"},
                )

                if resp.status_code == 404:
                    logger.warning(
                        "[BD-SERP] Zone '%s' not found — "
                        "check BRIGHT_DATA_SERP_ZONE in .env", zone
                    )
                    return None
                if resp.status_code == 401:
                    logger.error("[BD-SERP] 401 Unauthorized — check BRIGHT_DATA_API_KEY")
                    return None

                resp.raise_for_status()

                # Parse structured JSON (brd_json=1) or fall back to regex
                try:
                    data = resp.json()
                    organic_raw = data.get("organic", [])
                except Exception:
                    organic_raw = []
                    for url in re.findall(r'href="(https?://[^"&]+)"', resp.text):
                        if "google.com" not in url:
                            organic_raw.append({"url": url, "title": "", "description": ""})

                results = [
                    OrganicResult(
                        position=i + 1,
                        title=r.get("title", ""),
                        url=r.get("url", r.get("link", "")),
                        domain=_extract_domain(r.get("url", r.get("link", ""))),
                        snippet=r.get("description", r.get("snippet", ""))[:300],
                        raw=r,
                    )
                    for i, r in enumerate(organic_raw)
                    if r.get("url") or r.get("link")
                ]

                logger.info(
                    "[BD-SERP] '%s' → %d results via zone=%s ($%.4f)",
                    query, len(results), zone, settings.BRIGHT_DATA_COST_SERP,
                )
                return SERPResponse(
                    query=query,
                    total_results=len(results),
                    organic=results,
                    source=f"bright_data_{zone}",
                )

        except httpx.TimeoutException:
            logger.warning("[BD-SERP] Timeout for '%s' via zone=%s", query, zone)
            return None
        except Exception as exc:
            logger.error("[BD-SERP] Failed for '%s' via zone=%s: %s", query, zone, exc)
            return None


# ── Unified SERP client (BD primary, DDG fallback) ────────────────────────────


class SERPClient:
    """
    Unified SERP client — Bright Data is the primary source.
    DuckDuckGo is used as fallback when BD is unavailable or returns no results.

    Usage
    -----
        client = SERPClient()
        resp = await client.search("site:*.acme.com integrations")
    """

    def __init__(self) -> None:
        self._bd = BrightDataSERPClient()
        self._ddg = DuckDuckGoSearchClient()

    async def search(self, query: str, max_results: int = 30) -> SERPResponse:
        # Primary: Bright Data
        if settings.BRIGHT_DATA_API_KEY:
            resp = await self._bd.search(query, num_results=max_results)
            if resp.organic:
                return resp
            logger.info("[SERP] BD returned 0 results for '%s' — trying DuckDuckGo", query)

        # Fallback: DuckDuckGo (free)
        return await self._ddg.search(query, max_results=max_results)

    async def search_subdomains(self, apex_domain: str) -> list[str]:
        """
        Run site:*.{domain} query and extract unique subdomains.
        Returns list of FQDNs.
        """
        query = f"site:*.{apex_domain}"
        resp = await self.search(query, max_results=50)

        pattern = re.compile(rf'([\w\-]+\.{re.escape(apex_domain)})', re.I)
        subdomains: set[str] = set()
        for result in resp.organic:
            for text in (result.url, result.title, result.snippet):
                for m in pattern.finditer(text):
                    fqdn = m.group(1).lower()
                    if fqdn != apex_domain:
                        subdomains.add(fqdn)
        return list(subdomains)

    async def find_integration_pages(self, apex_domain: str) -> list[OrganicResult]:
        """Find indexed /integrations/ or /partners/ pages."""
        queries = [
            f"site:{apex_domain}/integrations",
            f"site:{apex_domain}/partners",
        ]
        results: list[OrganicResult] = []
        seen: set[str] = set()
        for q in queries:
            resp = await self.search(q, max_results=20)
            for r in resp.organic:
                if r.url not in seen:
                    seen.add(r.url)
                    results.append(r)
            await asyncio.sleep(1)  # Polite delay
        return results


# ── Domain intelligence ────────────────────────────────────────────────────────


class DomainSERPIntelligence:
    """Convenience wrapper for domain-specific SERP intelligence tasks."""

    def __init__(self) -> None:
        self._client = SERPClient()

    async def find_subdomains(self, domain: str) -> list[str]:
        return await self._client.search_subdomains(domain)

    async def find_integration_pages(self, domain: str) -> list[OrganicResult]:
        return await self._client.find_integration_pages(domain)

    async def batch_subdomain_discovery(
        self, domains: Iterable[str]
    ) -> dict[str, list[str]]:
        domains_list = list(domains)
        results = await asyncio.gather(
            *[self.find_subdomains(d) for d in domains_list],
            return_exceptions=True,
        )
        return {
            d: r if isinstance(r, list) else []
            for d, r in zip(domains_list, results)
        }
