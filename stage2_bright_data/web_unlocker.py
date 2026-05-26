"""
stage2_bright_data/web_unlocker.py
------------------------------------
Bright Data content fetch layer.

Strategy:
  1. Bright Data REST API (primary) — uses bearer token auth against active zone
     Zone priority: WEB_UNLOCKER_ZONE → SERP_ZONE (serp_api zone doubles as
     a general unlocker when no dedicated web_unlocker zone is configured)
  2. Direct HTTP (fallback)         — only if BD fails or key not configured

BD REST API endpoint:
  POST https://api.brightdata.com/request
  Authorization: Bearer {BRIGHT_DATA_API_KEY}
  Body: {"zone": "<active_zone>", "url": "target_url", "format": "raw"}

Cost: ~$0.004–0.005 per request. With $250 budget ≈ 50,000+ requests.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config.settings import get_settings
from stage2_bright_data.budget_guard import BudgetGuard, BDRequestType

logger = logging.getLogger(__name__)
settings = get_settings()

_BD_REST_API = "https://api.brightdata.com/request"

_DIRECT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# HTTP statuses that indicate a site is blocking us
_BLOCK_STATUSES = {403, 429, 503, 407, 401}


# ── Bright Data REST API fetch (primary) ─────────────────────────────────────


async def _bright_data_rest_fetch(
    url: str,
    method: str,
    timeout: float,
    json_response: bool,
    zone: str | None = None,
    **kwargs,
) -> httpx.Response | dict | str | None:
    """
    Fetch a URL through Bright Data REST API.
    Uses bearer token authentication — no proxy credentials needed.
    Zone selection: explicit zone arg → WEB_UNLOCKER_ZONE → SERP_ZONE (fallback).
    """
    target_zone = zone or settings.bright_data_active_zone
    if not target_zone:
        logger.warning("[BD-FETCH] No BD zone configured — cannot use BD REST API")
        return None
    payload = {"zone": target_zone, "url": url, "format": "raw"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _BD_REST_API,
                headers={
                    "Authorization": f"Bearer {settings.BRIGHT_DATA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if resp.status_code == 400:
                body = resp.text[:300]
                logger.error(
                    "[BD-WEB-UNLOCKER] 400 Bad Request for zone=%s url=%s — %s",
                    target_zone, url, body,
                )
                return None
            if resp.status_code == 401:
                logger.error("[BD-WEB-UNLOCKER] 401 Unauthorized — check BRIGHT_DATA_API_KEY")
                return None
            if resp.status_code == 404:
                logger.error(
                    "[BD-FETCH] 404 — zone '%s' not found. "
                    "Check BRIGHT_DATA_SERP_ZONE in your .env (currently: %s). "
                    "Manage zones at https://brightdata.com/cp/zones",
                    target_zone, settings.BRIGHT_DATA_SERP_ZONE,
                )
                return None

            resp.raise_for_status()
            logger.info("[BD-FETCH] %s → %d (zone=%s)", url, resp.status_code, target_zone)
            return _parse_response(resp, json_response)

    except httpx.TimeoutException:
        logger.warning("[BD-FETCH] Timeout for %s (zone=%s)", url, target_zone)
        return None
    except Exception as exc:
        logger.error("[BD-FETCH] REST API failed for %s: %s", url, exc)
        return None


# ── Direct HTTP fetch (fallback) ─────────────────────────────────────────────


async def _direct_fetch(
    url: str,
    method: str,
    timeout: float,
    json_response: bool,
    **kwargs,
) -> httpx.Response | dict | str | None:
    """Direct HTTPS request without any proxy — free fallback."""
    try:
        async with httpx.AsyncClient(
            headers=_DIRECT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            resp = await _do_request(client, method, url, **kwargs)
            if resp.status_code in _BLOCK_STATUSES:
                logger.debug("[DIRECT] %s returned %d", url, resp.status_code)
                return None
            resp.raise_for_status()
            logger.debug("[DIRECT] %s → %d (free)", url, resp.status_code)
            return _parse_response(resp, json_response)
    except httpx.TimeoutException:
        logger.debug("[DIRECT] Timeout for %s", url)
        return None
    except Exception as exc:
        logger.debug("[DIRECT] Error for %s: %s", url, exc)
        return None


# ── Legacy proxy fetch (if proxy credentials provided) ───────────────────────


async def _bright_data_proxy_fetch(
    url: str,
    method: str,
    timeout: float,
    json_response: bool,
    **kwargs,
) -> httpx.Response | dict | str | None:
    """Route request through Bright Data proxy (legacy username/password auth)."""
    proxy_url = settings.bright_data_proxy_url
    try:
        async with httpx.AsyncClient(
            proxies={"http://": proxy_url, "https://": proxy_url},
            headers=_DIRECT_HEADERS,
            timeout=timeout,
            verify=False,
            follow_redirects=True,
        ) as client:
            resp = await _do_request(client, method, url, **kwargs)
            resp.raise_for_status()
            logger.info("[BD-PROXY] %s → %d (zone proxy)", url, resp.status_code)
            return _parse_response(resp, json_response)
    except Exception as exc:
        logger.error("[BD-PROXY] Failed for %s: %s", url, exc)
        return None


# ── Smart fetch (BD primary, direct fallback) ─────────────────────────────────


async def smart_fetch(
    url: str,
    method: str = "GET",
    timeout: float = 20.0,
    json_response: bool = False,
    force_bright_data: bool = False,
    **request_kwargs,
) -> httpx.Response | dict | str | None:
    """
    Fetch a URL — Bright Data is the primary method.

    Priority:
      1. Bright Data REST API (bearer token)  ← always preferred
      2. Bright Data proxy (legacy creds)     ← if proxy creds present but no API key
      3. Direct HTTP                          ← fallback only if BD unavailable/fails

    Parameters
    ----------
    url               : target URL
    method            : HTTP verb
    timeout           : timeout in seconds
    json_response     : if True, return parsed JSON dict
    force_bright_data : raise immediately if BD is unavailable (no direct fallback)
    """
    guard = BudgetGuard.get_instance()

    # ── Primary: Bright Data REST API (bearer token) ──────────────────────
    if settings.BRIGHT_DATA_API_KEY and guard.can_spend(BDRequestType.WEB_UNLOCKER):
        guard.record_spend(BDRequestType.WEB_UNLOCKER)
        result = await _bright_data_rest_fetch(
            url, method, timeout + 25, json_response, **request_kwargs
        )
        if result is not None:
            return result
        logger.warning("[SMART-FETCH] BD REST API failed for %s — trying fallback", url)

    # ── Fallback A: Legacy proxy credentials ──────────────────────────────
    elif settings.BRIGHT_DATA_USERNAME and settings.BRIGHT_DATA_PASSWORD:
        if guard.can_spend(BDRequestType.WEB_UNLOCKER):
            guard.record_spend(BDRequestType.WEB_UNLOCKER)
            result = await _bright_data_proxy_fetch(
                url, method, timeout + 25, json_response, **request_kwargs
            )
            if result is not None:
                return result

    if force_bright_data:
        logger.error(
            "[SMART-FETCH] force_bright_data=True but BD unavailable/failed for %s", url
        )
        return None

    # ── Fallback B: Direct HTTP (free) ────────────────────────────────────
    logger.debug("[SMART-FETCH] Using direct HTTP for %s", url)
    return await _direct_fetch(url, method, timeout, json_response, **request_kwargs)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _do_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    if method.upper() == "GET":
        return await client.get(url, **kwargs)
    elif method.upper() == "POST":
        return await client.post(url, **kwargs)
    raise ValueError(f"Unsupported HTTP method: {method}")


def _parse_response(
    resp: httpx.Response, json_response: bool
) -> httpx.Response | dict | str:
    if json_response:
        try:
            return resp.json()
        except Exception:
            return resp.text
    return resp


# ── Retry wrapper ─────────────────────────────────────────────────────────────


async def fetch_with_retry(
    url: str,
    max_retries: int = 3,
    base_delay: float = 1.5,
    **smart_fetch_kwargs,
) -> Any | None:
    """smart_fetch with exponential backoff."""
    for attempt in range(max_retries):
        result = await smart_fetch(url, **smart_fetch_kwargs)
        if result is not None:
            return result
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.debug("[FETCH-RETRY] Attempt %d failed — retrying in %.1fs", attempt + 1, delay)
            await asyncio.sleep(delay)

    logger.warning("[FETCH-RETRY] All %d attempts failed for %s", max_retries, url)
    return None


# ── Batch fetcher ─────────────────────────────────────────────────────────────


async def batch_fetch(
    urls: list[str],
    concurrency: int = 8,
    **smart_fetch_kwargs,
) -> list[tuple[str, Any]]:
    """Fetch multiple URLs concurrently via smart_fetch."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(url: str) -> tuple[str, Any]:
        async with semaphore:
            return url, await smart_fetch(url, **smart_fetch_kwargs)

    return list(await asyncio.gather(*[_one(u) for u in urls]))
