"""
stage2_bright_data/mcp_orchestrator.py
----------------------------------------
Gemini-powered MCP orchestrator — FREE AI layer.

Uses Google Gemini 2.0 Flash via the new `google-genai` SDK:
  • Free tier: 1,500 requests/day, 1M tokens/minute
  • Function calling / tool use fully supported
  • Model: gemini-2.0-flash (fast, capable, free)

The agent autonomously drives the Bright Data tools to investigate
target domains and emit structured Signal objects.

Tool budget:
  - web_fetch       → smart_fetch (free-first + BD fallback)
  - serp_search     → DuckDuckGo free-first + BD fallback
  - fetch_robots    → direct HTTP (always free)
  - batch_fetch     → smart_fetch (free-first)
  - emit_signal     → local, free

Get your free Gemini API key: https://aistudio.google.com/apikey
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from google import genai  # type: ignore
from google.genai import types as genai_types  # type: ignore

from config.settings import get_settings
from models.signal import Signal, SignalSource, SignalType
from stage2_bright_data.web_unlocker import smart_fetch, batch_fetch
from stage2_bright_data.serp_api import SERPClient
from stage2_bright_data.scraping_browser import fetch_robots_txt
from stage2_bright_data.budget_guard import BudgetGuard

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Gemini Tool Declarations (google-genai SDK style) ─────────────────────────

_TOOL_CONFIG = genai_types.Tool(
    function_declarations=[
        genai_types.FunctionDeclaration(
            name="web_fetch",
            description=(
                "Fetch content from any public URL. Uses free direct HTTP first, "
                "Bright Data Web Unlocker as fallback for blocked sites. "
                "Use for: crt.sh queries, integration pages, pricing pages, APIs."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "url": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="Full URL to fetch (http:// or https://)",
                    ),
                    "return_json": genai_types.Schema(
                        type=genai_types.Type.BOOLEAN,
                        description="True to parse and return JSON, False for HTML/text",
                    ),
                },
                required=["url"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="serp_search",
            description=(
                "Search the web using DuckDuckGo (free) or Google via Bright Data. "
                "Supports operators: site:*.domain.com, intitle:, filetype:. "
                "Best for: subdomain discovery, integration pages, competitor research."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "query": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="Search query (supports Google/DDG operators)",
                    ),
                    "max_results": genai_types.Schema(
                        type=genai_types.Type.INTEGER,
                        description="Max results to return (default 20)",
                    ),
                },
                required=["query"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="fetch_robots_txt",
            description=(
                "Fetch and parse robots.txt for a domain. Free and instant. "
                "Disallowed paths reveal staged pages, beta features, unreleased products."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "domain": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="Apex domain (e.g. 'acme.com')",
                    ),
                },
                required=["domain"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="batch_fetch_urls",
            description=(
                "Fetch multiple URLs concurrently (max 15). "
                "Efficient for checking multiple subdomains or integration pages at once."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "urls": genai_types.Schema(
                        type=genai_types.Type.ARRAY,
                        items=genai_types.Schema(type=genai_types.Type.STRING),
                        description="List of URLs to fetch (max 15)",
                    ),
                },
                required=["urls"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="emit_signal",
            description=(
                "Record a detected intelligence signal. Call this for EVERY meaningful "
                "finding. Each signal feeds into the correlation engine."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "target_domain": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="Company apex domain being monitored (e.g. 'acme.com')",
                    ),
                    "signal_type": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        enum=[t.value for t in SignalType],
                        description="Type of intelligence signal",
                    ),
                    "source": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        enum=[s.value for s in SignalSource],
                        description="Data source that revealed this signal",
                    ),
                    "description": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="Clear human-readable description of what was found",
                    ),
                    "vendor_hint": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="Specific vendor identified (e.g. 'Salesforce', 'Stripe')",
                    ),
                    "confidence": genai_types.Schema(
                        type=genai_types.Type.NUMBER,
                        description="Confidence 0.0–1.0",
                    ),
                    "evidence_url": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="URL evidence supporting this signal",
                    ),
                },
                required=["target_domain", "signal_type", "source", "description", "confidence"],
            ),
        ),
    ]
)


# ── Tool executor ─────────────────────────────────────────────────────────────


class GeminiToolExecutor:
    """Executes tool calls made by Gemini during the agent loop."""

    def __init__(self, collected_signals: list[Signal]) -> None:
        self._signals = collected_signals
        self._serp = SERPClient()

    async def execute(self, name: str, args: dict) -> Any:
        logger.info("[GEMINI-EXEC] Tool: %s(%s)", name, {k: str(v)[:60] for k, v in args.items()})
        try:
            if name == "web_fetch":
                return await self._web_fetch(**args)
            elif name == "serp_search":
                return await self._serp_search(**args)
            elif name == "fetch_robots_txt":
                return await self._fetch_robots(**args)
            elif name == "batch_fetch_urls":
                return await self._batch_fetch(**args)
            elif name == "emit_signal":
                return await self._emit_signal(**args)
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as exc:
            logger.error("[GEMINI-EXEC] Tool %s failed: %s", name, exc)
            return {"error": str(exc)}

    async def _web_fetch(self, url: str, return_json: bool = False) -> dict:
        result = await smart_fetch(url, json_response=return_json, timeout=20.0)
        if result is None:
            return {"error": f"Failed to fetch {url}"}
        if return_json and isinstance(result, (dict, list)):
            return {"ok": True, "url": url, "data": result}
        text = result.text if hasattr(result, "text") else str(result)
        return {"ok": True, "url": url, "content": text[:4000]}

    async def _serp_search(self, query: str, max_results: int = 20) -> dict:
        resp = await self._serp.search(query, max_results=min(max_results, 50))
        return {
            "query": query,
            "source": resp.source,
            "total": resp.total_results,
            "results": [
                {"pos": r.position, "title": r.title, "url": r.url, "snippet": r.snippet[:200]}
                for r in resp.organic
            ],
        }

    async def _fetch_robots(self, domain: str) -> dict:
        data = await fetch_robots_txt(domain)
        return {
            "domain": domain,
            "disallowed_count": len(data["disallowed"]),
            "disallowed": data["disallowed"][:30],
            "interesting_paths": data["interesting_paths"],
            "sitemaps": data["sitemaps"],
        }

    async def _batch_fetch(self, urls: list[str]) -> dict:
        urls = urls[:15]  # Hard cap
        pairs = await batch_fetch(urls, concurrency=5)
        results = {}
        for url, resp in pairs:
            if resp is None:
                results[url] = {"error": "failed"}
            else:
                text = resp.text if hasattr(resp, "text") else str(resp)
                results[url] = {"ok": True, "length": len(text), "preview": text[:500]}
        return results

    async def _emit_signal(
        self,
        target_domain: str,
        signal_type: str,
        source: str,
        description: str,
        confidence: float,
        vendor_hint: str | None = None,
        evidence_url: str | None = None,
    ) -> dict:
        try:
            sig = Signal(
                source=SignalSource(source),
                target_domain=target_domain,
                signal_type=SignalType(signal_type),
                timestamp=datetime.now(timezone.utc),
                raw_data={"description": description, "evidence_url": evidence_url or ""},
                confidence=float(confidence),
                vendor_hint=vendor_hint,
                metadata={"emitted_by": "gemini_agent", "description": description},
            )
            self._signals.append(sig)
            logger.info(
                "[GEMINI-AGENT] ✅ Signal: %s → %s (%.0f%%) vendor=%s",
                target_domain, signal_type, confidence * 100, vendor_hint or "?",
            )
            return {"ok": True, "signal_id": sig.signal_id, "recorded": True}
        except (ValueError, KeyError) as exc:
            return {"error": f"Invalid signal params: {exc}"}


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an elite competitive intelligence analyst with OSINT expertise.
Your mission: investigate target companies to uncover enterprise software deal signals 
and competitor product launch indicators using ONLY public technical data.

INVESTIGATION PLAYBOOK (follow this order):

1. CHECK CERTIFICATES: Fetch https://crt.sh/?q=%.{domain}&output=json
   → Look for new subdomains in SAN entries (crm., analytics., payments., etc.)
   → Each new vendor-specific subdomain = emit a signal

2. ENUMERATE SUBDOMAINS: Search site:*.{domain} 
   → Cross-reference with known vendor subdomain patterns
   → High-value prefixes: crm, analytics, salesforce, hubspot, marketo, okta, stripe

3. CHECK ROBOTS.TXT: Always fetch robots.txt for the domain
   → Disallowed paths reveal staged/hidden content
   → /launch/, /beta/, /preview/, /upcoming/ = product launch signals

4. FETCH INTEGRATION PAGES: Try https://{domain}/integrations and /partners
   → New integrations listed = vendor deal evidence
   → Screenshot changes not possible, but check page content length/structure

5. CHECK WAYBACK: Search site:{domain}/integrations on DDG to find newly indexed pages

SIGNAL CONFIDENCE GUIDE:
  0.9+: Multiple corroborating signals (e.g., new cert + subdomain + integration page)
  0.75: Single strong signal (e.g., crm.domain.com cert issued this week)
  0.60: Weak/indirect signal (e.g., subdomain found but no other corroboration)
  
VENDOR PATTERNS:
  crm.* → Salesforce/HubSpot | analytics.* → Amplitude/Mixpanel
  stripe.* → Stripe | okta.* → Okta | workday.* → Workday
  zendesk.* → Zendesk | datadog.* → Datadog | segment.* → Segment

Always emit_signal for EVERY meaningful finding. Be thorough."""


# ── Main orchestrator ─────────────────────────────────────────────────────────


class MCPAgentOrchestrator:
    """
    Gemini 2.0 Flash-powered autonomous intelligence agent.

    FREE: Uses Google Gemini API (1500 req/day free tier) via google-genai SDK.
    Tools are executed with free-first strategy (Bright Data only as fallback).

    Usage
    -----
        orchestrator = MCPAgentOrchestrator()
        signals = await orchestrator.investigate_domain("acme.com")
        signals = await orchestrator.detect_product_launch("competitor.com")
    """

    def __init__(
        self,
        model: str | None = None,
        max_turns: int = 15,
    ) -> None:
        if not settings.has_gemini:
            raise ValueError("GEMINI_API_KEY not set. Get free key at https://aistudio.google.com/apikey")

        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model_name = model or settings.GEMINI_MODEL
        self.max_turns = max_turns

    async def investigate_domain(
        self, target_domain: str, context: str = ""
    ) -> list[Signal]:
        """
        Autonomous investigation of a target domain for enterprise deal signals.
        """
        task = (
            f"Investigate {target_domain} for enterprise software deal signals.\n"
            f"Target domain: {target_domain}\n"
            + (f"Context: {context}\n" if context else "")
            + "\nFollow the investigation playbook. Emit a signal for every meaningful finding."
        )
        return await self._run_agent(task)

    async def detect_product_launch(
        self,
        competitor_domain: str,
        product_keywords: list[str] | None = None,
    ) -> list[Signal]:
        """
        Module 2: Detect staged competitor product launches.
        """
        kws = ", ".join(product_keywords) if product_keywords else "any new product"
        task = (
            f"Investigate {competitor_domain} for signs of an upcoming product launch.\n"
            f"Keywords to watch: {kws}\n\n"
            "FOCUS ON:\n"
            "1. robots.txt disallowed paths (staged pages)\n"
            "2. New domains registered by the same company (check crt.sh for similar names)\n"
            "3. Recently indexed pages via DDG search\n"
            "4. Subdomains like staging., launch., beta., preview.\n"
            "5. Any /changelog or /pricing page content changes\n\n"
            "Emit signals with source='robots_txt' for robots.txt findings, "
            "'wayback' for indexed page discoveries, 'dns' for new subdomains."
        )
        return await self._run_agent(task)

    async def bulk_investigate(
        self, domains: list[str], concurrency: int = 2
    ) -> dict[str, list[Signal]]:
        """Investigate multiple domains with concurrency control."""
        semaphore = asyncio.Semaphore(concurrency)

        async def investigate_one(d: str) -> tuple[str, list[Signal]]:
            async with semaphore:
                return d, await self.investigate_domain(d)

        results = await asyncio.gather(
            *[investigate_one(d) for d in domains],
            return_exceptions=True,
        )
        return {
            d: sigs
            for result in results
            if isinstance(result, tuple)
            for d, sigs in [result]
        }

    async def _run_agent(self, task: str) -> list[Signal]:
        """
        Core Gemini agent loop with function calling (google-genai SDK).
        Runs until model stops calling tools or max_turns reached.
        """
        collected_signals: list[Signal] = []
        executor = GeminiToolExecutor(collected_signals)

        # Build conversation history
        contents: list[genai_types.Content] = [
            genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=task)],
            )
        ]

        config = genai_types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            tools=[_TOOL_CONFIG],
            temperature=0.1,  # Low temperature for consistent tool use
            max_output_tokens=4096,
        )

        logger.info("[GEMINI-AGENT] Starting: %s...", task[:80])

        for turn in range(self.max_turns):
            logger.info("[GEMINI-AGENT] Turn %d/%d", turn + 1, self.max_turns)

            # Call Gemini API (in thread — SDK is sync)
            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                logger.error("[GEMINI-AGENT] API error on turn %d: %s", turn + 1, exc)
                break

            # Append assistant response to history
            if response.candidates:
                contents.append(response.candidates[0].content)

            # Extract function calls from response
            function_calls = []
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        function_calls.append(part.function_call)

            if not function_calls:
                # Model finished — extract any final text
                try:
                    final_text = response.text
                    if final_text:
                        logger.info("[GEMINI-AGENT] Final: %s", final_text[:200])
                except Exception:
                    pass
                logger.info(
                    "[GEMINI-AGENT] Done after %d turns — %d signals",
                    turn + 1, len(collected_signals),
                )
                break

            # Execute all tool calls concurrently
            tool_outputs = await asyncio.gather(
                *[
                    executor.execute(fc.name, dict(fc.args))
                    for fc in function_calls
                ],
                return_exceptions=True,
            )

            # Build tool result content and append to history
            tool_response_parts: list[genai_types.Part] = []
            for fc, output in zip(function_calls, tool_outputs):
                if isinstance(output, Exception):
                    output = {"error": str(output)}
                tool_response_parts.append(
                    genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=fc.name,
                            response={"result": json.dumps(output, default=str)[:6000]},
                        )
                    )
                )

            contents.append(
                genai_types.Content(role="tool", parts=tool_response_parts)
            )

        # Log budget summary after each investigation
        BudgetGuard.get_instance().log_summary()

        logger.info("[GEMINI-AGENT] Complete — %d signals total", len(collected_signals))
        return collected_signals
