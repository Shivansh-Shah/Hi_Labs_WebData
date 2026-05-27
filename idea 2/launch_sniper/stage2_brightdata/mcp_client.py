# Bright Data MCP client — wraps scrape_as_markdown and search_engine MCP tool calls.
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

# Load .env from the launch_sniper root (two levels up from this file)
load_dotenv(Path(__file__).parent.parent / ".env")

_MCP_CALL_LIMIT = 100
_MCP_WARN_THRESHOLD = 80
_TOOL_RETRIES = 2        # retry failed MCP calls up to this many times
_RETRY_DELAY = 2.0       # seconds between retries (doubles each attempt)


class BudgetExceededError(RuntimeError):
    """Raised when the per-run MCP call hard limit is hit."""


# ── Exception helpers ─────────────────────────────────────────────────────────


def _unwrap_exc(exc: BaseException) -> str:
    """
    Return a readable error string.
    Python 3.11+ TaskGroup failures raise ExceptionGroup; unwrap the first
    sub-exception so logs show the actual cause, not just
    'unhandled errors in a TaskGroup (1 sub-exception)'.
    """
    if isinstance(exc, BaseExceptionGroup):
        parts = "; ".join(f"{type(e).__name__}: {e}" for e in exc.exceptions)
        return f"TaskGroup[{parts}]"
    return f"{type(exc).__name__}: {exc}"


class BrightDataMCPClient:
    """
    Single entry point for all Bright Data MCP tool calls in launch_sniper.

    Budget guard
    ------------
    - Tracks total MCP calls made this run (per instance).
    - Prints a warning at 80 calls.
    - Raises BudgetExceededError at 100 calls (hard stop).
    This protects the $250 Bright Data credit allocation.

    Usage
    -----
        client = BrightDataMCPClient()
        markdown = await client.scrape_url("https://example.com/robots.txt")
        results  = await client.search("acme corp new product launch", num_results=10)
        pages    = await client.batch_scrape(["https://a.com", "https://b.com"])
    """

    def __init__(self) -> None:
        token = os.getenv("BRIGHT_DATA_MCP_TOKEN", "")
        if not token or token == "your_token_here":
            raise ValueError(
                "BRIGHT_DATA_MCP_TOKEN is not set. "
                "Add your token to idea2/launch_sniper/.env"
            )
        self._mcp_url = f"https://mcp.brightdata.com/sse?token={token}"
        self._call_count = 0

    # ── Budget guard ──────────────────────────────────────────────────────────

    def _check_budget(self) -> None:
        if self._call_count >= _MCP_CALL_LIMIT:
            raise BudgetExceededError(
                f"[BUDGET] Hard limit of {_MCP_CALL_LIMIT} MCP calls reached. "
                "Aborting to protect Bright Data credits. "
                f"Calls used this run: {self._call_count}"
            )
        if self._call_count >= _MCP_WARN_THRESHOLD:
            print(
                f"[BUDGET WARNING] {self._call_count}/{_MCP_CALL_LIMIT} "
                "MCP calls used — approaching the per-run limit."
            )

    # ── Low-level tool dispatcher ─────────────────────────────────────────────

    async def _call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        """
        Open a fresh SSE session, call one tool, close session.
        Retries up to _TOOL_RETRIES times on transient failures with
        exponential backoff. Each attempt counts against the budget.
        """
        last_exc: BaseException | None = None

        for attempt in range(_TOOL_RETRIES + 1):
            self._check_budget()
            self._call_count += 1
            logger.debug(
                "[MCP] call #%d (attempt %d/%d) — tool=%s  args=%s",
                self._call_count, attempt + 1, _TOOL_RETRIES + 1,
                tool_name, arguments,
            )
            try:
                async with sse_client(url=self._mcp_url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool(tool_name, arguments)
            except BudgetExceededError:
                raise
            except BaseException as exc:
                last_exc = exc
                if attempt < _TOOL_RETRIES:
                    delay = _RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        "[MCP] attempt %d failed (%s) — retrying in %.0fs",
                        attempt + 1, _unwrap_exc(exc), delay,
                    )
                    await asyncio.sleep(delay)

        # All attempts exhausted — raise with the unwrapped message so callers
        # see the real cause rather than the opaque ExceptionGroup string
        raise RuntimeError(
            f"MCP tool '{tool_name}' failed after {_TOOL_RETRIES + 1} attempts: "
            f"{_unwrap_exc(last_exc)}"
        ) from last_exc

    # ── Public API ────────────────────────────────────────────────────────────

    async def scrape_url(self, url: str) -> str:
        """
        Fetch any URL as clean markdown via the scrape_as_markdown MCP tool.

        Used by robots_txt_collector to retrieve robots.txt content without
        being blocked by bot-detection.

        Returns empty string on any failure.
        """
        try:
            result = await self._call_tool("scrape_as_markdown", {"url": url})
            if result.isError:
                logger.warning("[MCP] scrape_as_markdown error for %s", url)
                return ""
            return result.content[0].text if result.content else ""
        except BudgetExceededError:
            raise
        except Exception as exc:
            logger.error("[MCP] scrape_url failed for %s: %s", url, exc)
            return ""

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[dict[str, str]]:
        """
        Run a Google search via the search_engine MCP tool.

        Returns a list of {title, link, description} dicts (up to num_results).
        Google returns JSON; the parser handles both JSON and markdown fallback.

        Returns empty list on any failure.
        """
        try:
            result = await self._call_tool(
                "search_engine",
                {"query": query, "engine": "google"},
            )
            if result.isError:
                logger.warning("[MCP] search_engine error for query=%r", query)
                return []

            raw = result.content[0].text if result.content else ""
            if not raw:
                return []

            # Google responses are JSON; Bing/Yandex come back as markdown
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [_normalise_result(r) for r in data[:num_results]]
                if isinstance(data, dict) and "organic" in data:
                    return [
                        _normalise_result(r)
                        for r in data["organic"][:num_results]
                    ]
                return []
            except json.JSONDecodeError:
                # Markdown fallback — wrap entire text as a single description entry
                logger.debug("[MCP] search response is markdown (non-JSON engine)")
                return [{"title": "", "link": "", "description": raw}]

        except BudgetExceededError:
            raise
        except Exception as exc:
            logger.error("[MCP] search failed for %r: %s", query, exc)
            return []

    async def batch_scrape(self, urls: list[str]) -> list[str]:
        """
        Scrape multiple URLs concurrently.

        Calls scrape_url() for each URL via asyncio.gather so all requests
        fire in parallel. Returns markdown strings in the same order as input.
        Each scrape_url() call counts against the budget independently.
        """
        return list(await asyncio.gather(*[self.scrape_url(u) for u in urls]))

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def calls_used(self) -> int:
        return self._call_count

    @property
    def calls_remaining(self) -> int:
        return max(0, _MCP_CALL_LIMIT - self._call_count)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalise_result(r: dict[str, Any]) -> dict[str, str]:
    """Normalise a raw search result dict to {title, link, description}."""
    return {
        "title": r.get("title", ""),
        "link": r.get("link", r.get("url", "")),
        "description": r.get("snippet", r.get("description", "")),
    }
