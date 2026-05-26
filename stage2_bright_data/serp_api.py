"""
stage2_bright_data/serp_api.py
--------------------------------
SERP intelligence layer.

Free-first strategy:
  1. DuckDuckGo HTML scraping   — completely free, no key, good results
  2. SerpDog (free tier)         — 100 free searches/month with API key (optional)
  3. Bright Data SERP (paid)     — last resort, budget-gated

For subdomain discovery the SERP approach complements DNS enumeration
by finding subdomains that have web presence (indexed by Google/DDG).
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


# ── Source 2: Bright Data SERP (paid, last resort) ────────────────────────────


class BrightDataSERPClient:
    """
    Bright Data SERP zone — Google results with anti-bot bypass.
    ONLY used when DuckDuckGo results are insufficient.
    Always checks budget before firing.
    """

    _GOOGLE_URL = "https://www.google.com/search"

    async def search(
        self, query: str, num_results: int = 30, country: str = "us"
    ) -> SERPResponse:
        guard = BudgetGuard.get_instance()
        if not guard.can_spend(BDRequestType.SERP):
            logger.warning("[BD-SERP] Budget gate blocked search for '%s'", query)
            return SERPResponse(query=query, total_results=0, organic=[], source="blocked_budget")

        guard.record_spend(BDRequestType.SERP)
        proxy = settings.bright_data_serp_proxy_url

        try:
            async with httpx.AsyncClient(
                proxies={"http://": proxy, "https://": proxy},
                timeout=35,
                verify=False,
            ) as client:
                resp = await client.get(
                    self._GOOGLE_URL,
                    params={"q": query, "num": num_results, "brd_json": "1", "gl": country},
                )
                resp.raise_for_status()

                try:
                    data = resp.json()
                    organic_raw = data.get("organic", [])
                except Exception:
                    # Fallback: regex extract URLs from HTML
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
                    "[BD-SERP] '%s' → %d results (PAID $%.4f)",
                    query,
                    len(results),
                    settings.BRIGHT_DATA_COST_SERP,
                )
                return SERPResponse(
                    query=query,
                    total_results=len(results),
                    organic=results,
                    source="bright_data_google",
                )

        except Exception as exc:
            logger.error("[BD-SERP] Failed for '%s': %s", query, exc)
            return SERPResponse(query=query, total_results=0, organic=[], source="error")


# ── Unified SERP client (free-first) ──────────────────────────────────────────


class SERPClient:
    """
    Unified SERP client — tries DuckDuckGo first, falls back to Bright Data
    only when DDG yields fewer than `min_results` results.

    Usage
    -----
        client = SERPClient()
        resp = await client.search("site:*.acme.com integrations")
    """

    def __init__(self, min_results_threshold: int = 3) -> None:
        self._ddg = DuckDuckGoSearchClient()
        self._bd = BrightDataSERPClient()
        self._min_threshold = min_results_threshold

    async def search(self, query: str, max_results: int = 30) -> SERPResponse:
        # Try DDG first (free)
        resp = await self._ddg.search(query, max_results=max_results)
        if len(resp.organic) >= self._min_results_threshold:
            return resp

        logger.info(
            "[SERP] DDG returned %d results for '%s' — trying Bright Data",
            len(resp.organic),
            query,
        )
        bd_resp = await self._bd.search(query, num_results=max_results)
        if bd_resp.organic:
            return bd_resp
        return resp  # Return DDG results even if sparse

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
