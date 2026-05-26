"""
stage2_bright_data/scraping_browser.py
----------------------------------------
Bright Data Scraping Browser — BUDGET-GATED.

Cost: ~$0.10/session. Reserve for truly JS-heavy pages that can't be
fetched with direct httpx or simpler tools.

When NOT to use Scraping Browser (use smart_fetch instead):
  - Static pages (robots.txt, JSON APIs, most crt.sh responses)
  - Any page that returns good content with a real User-Agent

When to USE Scraping Browser:
  - GitHub org pages (JS-rendered activity feeds)
  - Statuspage.io instances with JS components
  - SPA-based integration directories with infinite scroll

Free fallback: httpx with real User-Agent header covers 80%+ of use cases.
"""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx

from config.settings import get_settings
from stage2_bright_data.budget_guard import BudgetGuard, BDRequestType
from stage2_bright_data.web_unlocker import smart_fetch

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Free httpx-based JS-light scraper ────────────────────────────────────────


async def fetch_page_free(
    url: str,
    timeout: float = 20.0,
    extra_headers: dict[str, str] | None = None,
) -> str | None:
    """
    Fetch page HTML using httpx with a realistic browser User-Agent.
    Works for most non-SPA pages. Free.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        **(extra_headers or {}),
    }
    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            logger.debug("[SCRAPER-FREE] %s returned %d", url, resp.status_code)
            return None
    except Exception as exc:
        logger.debug("[SCRAPER-FREE] Error for %s: %s", url, exc)
        return None


# ── Robots.txt fetcher (free, no JS needed) ───────────────────────────────────


async def fetch_robots_txt(domain: str) -> dict[str, Any]:
    """
    Fetch and parse robots.txt for a domain.
    Completely free — robots.txt is static, no JS needed.
    Returns dict with disallowed paths, user-agent rules, and sitemaps.
    """
    url = f"https://{domain}/robots.txt"
    result = {
        "domain": domain,
        "url": url,
        "disallowed": [],
        "allowed": [],
        "sitemaps": [],
        "interesting_paths": [],
        "raw": "",
    }

    # Try HTTPS first, fallback to HTTP
    for scheme in ("https", "http"):
        content = await fetch_page_free(f"{scheme}://{domain}/robots.txt", timeout=10.0)
        if content:
            result["raw"] = content
            break
    else:
        return result

    # Parse robots.txt
    for line in result["raw"].splitlines():
        line = line.strip()
        if line.lower().startswith("disallow:"):
            path = line[9:].strip().split("#")[0].strip()
            if path:
                result["disallowed"].append(path)
        elif line.lower().startswith("allow:"):
            path = line[6:].strip().split("#")[0].strip()
            if path:
                result["allowed"].append(path)
        elif line.lower().startswith("sitemap:"):
            result["sitemaps"].append(line[8:].strip())

    # Flag interesting disallowed paths (potential staged content)
    interesting_keywords = [
        "launch", "beta", "staging", "preview", "internal", "admin",
        "new-product", "product-", "upcoming", "unreleased", "secret",
        "experiment", "test-", "-v2", "/v2/", "feature-",
    ]
    for path in result["disallowed"]:
        if any(kw in path.lower() for kw in interesting_keywords):
            result["interesting_paths"].append(path)

    logger.info(
        "[ROBOTS] %s — %d disallowed, %d interesting",
        domain,
        len(result["disallowed"]),
        len(result["interesting_paths"]),
    )
    return result


async def batch_fetch_robots(domains: list[str]) -> list[dict[str, Any]]:
    """Fetch robots.txt for multiple domains concurrently (free)."""
    results = await asyncio.gather(
        *[fetch_robots_txt(d) for d in domains],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, dict)]


# ── Scraping Browser (Playwright CDP) — budget-gated ─────────────────────────


class ScrapingBrowserClient:
    """
    Bright Data Scraping Browser via Playwright CDP.
    ONLY used when free alternatives fail for JS-heavy pages.

    Cost: ~$0.10/session.
    Each session is gated by BudgetGuard.

    Usage
    -----
        async with ScrapingBrowserClient.if_affordable() as browser:
            if browser:
                html = await browser.fetch_page_html("https://...")
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    @classmethod
    @asynccontextmanager
    async def if_affordable(cls) -> AsyncGenerator["ScrapingBrowserClient | None", None]:
        """
        Context manager that yields a browser client only if budget allows,
        otherwise yields None (caller handles gracefully).
        """
        guard = BudgetGuard.get_instance()
        if not guard.can_spend(BDRequestType.SCRAPING_BROWSER):
            logger.warning("[SCRAPING-BROWSER] Budget gate blocked — yielding None")
            yield None
            return

        guard.record_spend(BDRequestType.SCRAPING_BROWSER)
        client = cls()
        try:
            await client._connect()
            yield client
        except Exception as exc:
            logger.error("[SCRAPING-BROWSER] Failed to connect: %s", exc)
            yield None
        finally:
            await client._disconnect()

    async def _connect(self) -> None:
        try:
            from playwright.async_api import async_playwright  # type: ignore
            self._playwright = await async_playwright().start()
            cdp_url = settings.SCRAPING_BROWSER_CDP_URL
            logger.info("[SCRAPING-BROWSER] Connecting to %s", cdp_url)
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            logger.info(
                "[SCRAPING-BROWSER] Connected. Version: %s", self._browser.version
            )
        except ImportError:
            raise RuntimeError("playwright not installed — run: pip install playwright && playwright install chromium")

    async def _disconnect(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch_page_html(self, url: str, wait_selector: str | None = None) -> str | None:
        """Fetch fully rendered HTML from a JS-heavy page."""
        if not self._browser:
            return None
        try:
            context = await self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = await context.new_page()
            # Block media to speed up rendering
            await page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf}",
                lambda r: r.abort(),
            )
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=8_000)
                except Exception:
                    pass
            html = await page.content()
            await context.close()
            logger.info("[SCRAPING-BROWSER] Fetched %s (%d chars)", url, len(html))
            return html
        except Exception as exc:
            logger.error("[SCRAPING-BROWSER] Fetch failed for %s: %s", url, exc)
            return None

    async def extract_links(self, url: str, filter_pattern: str | None = None) -> list[str]:
        """Extract all links from a rendered page."""
        html = await self.fetch_page_html(url)
        if not html:
            return []
        pattern = re.compile(r'href="(https?://[^"]+)"', re.I)
        links = pattern.findall(html)
        if filter_pattern:
            fp = re.compile(filter_pattern, re.I)
            links = [l for l in links if fp.search(l)]
        return list(set(links))


# ── Smart page fetcher (free-first, Playwright fallback) ──────────────────────


async def smart_page_fetch(
    url: str,
    require_js: bool = False,
    wait_selector: str | None = None,
) -> str | None:
    """
    Intelligently fetch a page:
      - If require_js=False: try free httpx first, Scraping Browser only if blocked
      - If require_js=True:  skip directly to Scraping Browser (budget-gated)

    This is the main entrypoint for content fetching in Stage 2.
    """
    if not require_js:
        html = await fetch_page_free(url)
        if html:
            return html
        # Check if it looks like a real block or just a 404
        logger.debug("[SMART-PAGE] Free fetch failed for %s — trying BD", url)

    async with ScrapingBrowserClient.if_affordable() as browser:
        if browser:
            return await browser.fetch_page_html(url, wait_selector=wait_selector)

    # Last resort: Web Unlocker
    result = await smart_fetch(url)
    if result and hasattr(result, "text"):
        return result.text
    return None
