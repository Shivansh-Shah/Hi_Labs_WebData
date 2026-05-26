"""
stage2_bright_data/web_unlocker.py
------------------------------------
Bright Data Web Unlocker — FALLBACK ONLY.

Strategy (budget-conscious):
  1. Try the direct HTTP request first (free)
  2. Only route through Web Unlocker if direct request fails with 403/429/block
  3. Check BudgetGuard before every Bright Data request
  4. Log every spend for accountability

Cost: ~$0.004 per request. With $250 budget ≈ 62,500 requests maximum.
Reserve Bright Data for genuinely blocked targets only.
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

# Statuses that indicate we need Bright Data's help
_BLOCK_STATUSES = {403, 429, 503, 407, 401}

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


# ── Smart fetch (free first, Bright Data fallback) ────────────────────────────


async def smart_fetch(
    url: str,
    method: str = "GET",
    timeout: float = 20.0,
    json_response: bool = False,
    force_bright_data: bool = False,
    **request_kwargs,
) -> httpx.Response | dict | str | None:
    """
    Fetch a URL using the cheapest available method:
      1. Direct request (free, always tried first unless force_bright_data)
      2. Bright Data Web Unlocker (paid fallback if direct is blocked)

    Parameters
    ----------
    url               : target URL
    method            : HTTP verb
    timeout           : timeout in seconds (shorter for direct, longer for BD)
    json_response     : if True, return parsed JSON dict
    force_bright_data : skip direct attempt (use for known-blocked sites)
    **request_kwargs  : passed to httpx

    Returns
    -------
    httpx.Response | dict (json) | str (text) | None on total failure
    """
    guard = BudgetGuard.get_instance()

    # ── Attempt 1: Direct (free) ───────────────────────────────────────────
    if not force_bright_data:
        result = await _direct_fetch(url, method, timeout, json_response, **request_kwargs)
        if result is not None:
            return result
        logger.debug("[SMART-FETCH] Direct failed for %s — checking BD fallback", url)

    # ── Attempt 2: Bright Data fallback (paid) ────────────────────────────
    if not guard.can_spend(BDRequestType.WEB_UNLOCKER):
        logger.warning(
            "[SMART-FETCH] Budget exhausted — no fallback available for %s", url
        )
        return None

    guard.record_spend(BDRequestType.WEB_UNLOCKER)
    return await _bright_data_fetch(url, method, timeout + 25, json_response, **request_kwargs)


async def _direct_fetch(
    url: str,
    method: str,
    timeout: float,
    json_response: bool,
    **kwargs,
) -> httpx.Response | dict | str | None:
    """Attempt a direct HTTPS request without any proxy."""
    try:
        async with httpx.AsyncClient(
            headers=_DIRECT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            resp = await _do_request(client, method, url, **kwargs)
            if resp.status_code in _BLOCK_STATUSES:
                logger.debug(
                    "[DIRECT] %s returned %d — will fallback",
                    url,
                    resp.status_code,
                )
                return None
            resp.raise_for_status()
            logger.debug("[DIRECT] %s → %d", url, resp.status_code)
            return _parse_response(resp, json_response)
    except httpx.TimeoutException:
        logger.debug("[DIRECT] Timeout for %s", url)
        return None
    except Exception as exc:
        logger.debug("[DIRECT] Error for %s: %s", url, exc)
        return None


async def _bright_data_fetch(
    url: str,
    method: str,
    timeout: float,
    json_response: bool,
    **kwargs,
) -> httpx.Response | dict | str | None:
    """Route request through Bright Data Web Unlocker proxy."""
    proxy_url = settings.bright_data_proxy_url
    try:
        async with httpx.AsyncClient(
            proxies={"http://": proxy_url, "https://": proxy_url},
            headers=_DIRECT_HEADERS,
            timeout=timeout,
            verify=False,  # BD terminates TLS
            follow_redirects=True,
        ) as client:
            resp = await _do_request(client, method, url, **kwargs)
            resp.raise_for_status()
            logger.info("[BD-WEB-UNLOCKER] %s → %d (paid)", url, resp.status_code)
            return _parse_response(resp, json_response)
    except Exception as exc:
        logger.error("[BD-WEB-UNLOCKER] Failed for %s: %s", url, exc)
        return None


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
    """
    Call smart_fetch with exponential backoff on transient failures.
    Respects the budget gate — won't burn retries on budget-exceeded state.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            result = await smart_fetch(url, **smart_fetch_kwargs)
            if result is not None:
                return result
        except Exception as exc:
            last_exc = exc

        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            logger.debug("[FETCH-RETRY] Attempt %d failed — retrying in %.1fs", attempt + 1, delay)
            await asyncio.sleep(delay)

    logger.warning("[FETCH-RETRY] All %d attempts failed for %s: %s", max_retries, url, last_exc)
    return None


# ── Batch fetcher ─────────────────────────────────────────────────────────────


async def batch_fetch(
    urls: list[str],
    concurrency: int = 8,
    **smart_fetch_kwargs,
) -> list[tuple[str, Any]]:
    """
    Fetch multiple URLs concurrently using smart_fetch (free-first strategy).
    Returns [(url, result_or_None)] preserving order.

    Higher concurrency than before since most requests are direct (free).
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(url: str) -> tuple[str, Any]:
        async with semaphore:
            return url, await smart_fetch(url, **smart_fetch_kwargs)

    return list(await asyncio.gather(*[_one(u) for u in urls]))
