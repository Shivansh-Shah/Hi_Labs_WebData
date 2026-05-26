"""
stage5_ai_enrichment/ai_enricher.py
--------------------------------------
Stage 5 — AI Enrichment Layer.

Takes DealCluster objects from Stage 4 and produces DealIntelligenceObjects
by calling the OpenAI API (gpt-4o-mini).

What the LLM does:
  1. Attributes the most likely vendor from signal patterns
  2. Estimates when the deal closed (based on earliest signal)
  3. Recommends outreach timing (~6-9 months post-close for renewal)
  4. Assigns a refined confidence score with reasoning
  5. Surfaces new subdomain patterns worth adding to VendorFingerprinter

Model: gpt-4o-mini
  - $0.15 / 1M input tokens
  - $0.60 / 1M output tokens
  - Excellent at structured JSON extraction
  - With $15 budget: ~50,000 cluster enrichments
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from config.settings import get_settings
from models.deal_cluster import DealCluster
from models.deal_intelligence import DealIntelligenceObject, VendorPatternSuggestion
from models.signal import ConfidenceTier

logger = logging.getLogger(__name__)
settings = get_settings()

# gpt-4o-mini pricing (USD per token)
_INPUT_COST_PER_TOKEN = 0.15 / 1_000_000
_OUTPUT_COST_PER_TOKEN = 0.60 / 1_000_000

# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a B2B GTM intelligence analyst specialising in identifying enterprise \
software procurement signals from technical data.

You receive a cluster of signals (SSL certificates, DNS subdomains, Wayback Machine \
page changes, GitHub activity) collected for a target company and must:
  1. Identify the most likely vendor being adopted
  2. Estimate the deal close date from the earliest signal
  3. Recommend an outreach window 6–9 months after deal close (contract renewal)
  4. Assign a refined confidence score with clear reasoning
  5. Surface any new subdomain or URL patterns worth tracking

Always respond with a single valid JSON object matching the schema exactly. \
Do not add any text outside the JSON.
"""

_RESPONSE_SCHEMA = {
    "suspected_vendor": "string — most likely vendor name (e.g. 'Salesforce')",
    "vendor_category": "string — product category: crm | payments | identity | analytics | support | observability | data_warehouse | data_platform | cdp | comms | pm | bi | etl | integration | incident | status | cdn | hris | docs | other",
    "confidence": "float 0.0–1.0 — your refined estimate of how certain this deal is",
    "deal_closed_estimate": "string — estimated deal close period, e.g. 'April 2026' or 'Q2 2026'",
    "outreach_window": "string — recommended contact window, e.g. 'Oct–Dec 2026'",
    "reasoning": "string — 2-4 sentence explanation of your attribution and timing logic",
    "signal_summary": "array of strings — concise human-readable descriptions of each key signal",
    "new_vendor_patterns": "array of {pattern: string, vendor: string, category: string, confidence: float} — new subdomain or path patterns discovered that aren't in the fingerprint table (empty array if none)",
}


def _build_user_prompt(cluster: DealCluster) -> str:
    """Serialize a DealCluster into the LLM prompt."""
    window_days = (cluster.window_end - cluster.window_start).days or 1
    window_str = (
        f"{cluster.window_start.strftime('%Y-%m-%d')} → "
        f"{cluster.window_end.strftime('%Y-%m-%d')} ({window_days} days)"
    )

    sources_str = ", ".join(cluster.metadata.get("sources", []))
    signal_types_str = ", ".join(cluster.metadata.get("signal_types", []))
    vendors_str = ", ".join(cluster.vendors[:8]) if cluster.vendors else "unknown"

    prompt = f"""COMPANY: {cluster.target_domain}
SIGNAL WINDOW: {window_str}
SIGNAL COUNT: {cluster.signal_count} signals from {cluster.source_diversity} source(s)
SOURCES: {sources_str}
SIGNAL TYPES: {signal_types_str}
VENDOR HINTS (from fingerprint table): {vendors_str}
STAGE-4 CONFIDENCE: {cluster.confidence:.0%} ({cluster.tier.value.upper()})
CLUSTER SUMMARY: {cluster.summary}

Return a JSON object with these exact fields:
{json.dumps(_RESPONSE_SCHEMA, indent=2)}"""

    return prompt


def _parse_llm_response(
    raw: str,
    cluster: DealCluster,
    model: str,
    usage: Any,
) -> DealIntelligenceObject:
    """
    Parse the LLM JSON response into a DealIntelligenceObject.
    Falls back to safe defaults if parsing fails.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON block if wrapped in markdown
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            logger.error("[STAGE5] Failed to parse LLM response as JSON: %s", raw[:200])
            data = {}

    # Normalise confidence
    llm_confidence = float(data.get("confidence", cluster.confidence))
    llm_confidence = max(0.0, min(1.0, llm_confidence))

    # Blend Stage-4 score with LLM score (60% LLM, 40% Stage-4)
    blended_confidence = round(0.6 * llm_confidence + 0.4 * cluster.confidence, 4)

    if blended_confidence >= 0.80:
        tier = ConfidenceTier.HIGH
    elif blended_confidence >= 0.55:
        tier = ConfidenceTier.MEDIUM
    else:
        tier = ConfidenceTier.LOW

    # Token cost
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    cost = input_tokens * _INPUT_COST_PER_TOKEN + output_tokens * _OUTPUT_COST_PER_TOKEN

    # New vendor patterns
    raw_patterns = data.get("new_vendor_patterns", [])
    new_patterns = [
        VendorPatternSuggestion(
            pattern=p.get("pattern", "").lower().strip(),
            vendor=p.get("vendor", ""),
            category=p.get("category", "other"),
            confidence=float(p.get("confidence", 0.75)),
            source="llm_discovery",
        )
        for p in raw_patterns
        if p.get("pattern") and p.get("vendor")
    ]

    return DealIntelligenceObject(
        cluster_id=cluster.cluster_id,
        target_company=cluster.target_domain,
        suspected_vendor=data.get("suspected_vendor", ", ".join(cluster.vendors[:2]) or "unknown"),
        vendor_category=data.get("vendor_category", "other"),
        signals=data.get("signal_summary", [cluster.summary]),
        confidence=blended_confidence,
        tier=tier,
        deal_closed_estimate=data.get("deal_closed_estimate", "Unknown"),
        outreach_window=data.get("outreach_window", "Unknown"),
        reasoning=data.get("reasoning", ""),
        new_vendor_patterns=new_patterns,
        model_used=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
        metadata={
            "stage4_confidence": cluster.confidence,
            "stage4_tier": cluster.tier.value,
            "llm_raw_confidence": llm_confidence,
            "blended_confidence": blended_confidence,
        },
    )


# ── AIEnricher ────────────────────────────────────────────────────────────────


class AIEnricher:
    """
    Stage 5 — Enriches DealClusters with LLM-generated intelligence.

    Uses gpt-4o-mini via the OpenAI API.
    Registers newly discovered vendor patterns back to VendorFingerprinter.

    Usage
    -----
        enricher = AIEnricher()
        intel_objects = await enricher.enrich_clusters(clusters)
        for obj in intel_objects:
            print(obj.to_dict())
    """

    def __init__(
        self,
        model: str | None = None,
        concurrency: int = 5,
        min_tier: ConfidenceTier = ConfidenceTier.LOW,
        fingerprinter=None,
    ) -> None:
        if not settings.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY not configured. "
                "Add it to your .env file."
            )
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._model = model or settings.OPENAI_MODEL
        self._max_tokens = settings.OPENAI_MAX_TOKENS
        self._concurrency = concurrency
        self._min_tier = min_tier
        self._fingerprinter = fingerprinter
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost = 0.0
        logger.info(
            "[STAGE5] AIEnricher ready — model=%s, concurrency=%d",
            self._model, self._concurrency,
        )

    async def enrich_cluster(self, cluster: DealCluster) -> DealIntelligenceObject:
        """
        Enrich a single DealCluster. Calls OpenAI once per cluster.
        """
        user_prompt = _build_user_prompt(cluster)

        try:
            response: ChatCompletion = await self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                max_tokens=self._max_tokens,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

            raw_content = response.choices[0].message.content or "{}"
            usage = response.usage

            intel = _parse_llm_response(raw_content, cluster, self._model, usage)

            # Track spend
            self._total_input_tokens += intel.input_tokens
            self._total_output_tokens += intel.output_tokens
            self._total_cost += intel.estimated_cost_usd

            logger.info(
                "[STAGE5] %s → vendor=%s | confidence=%.0f%% (%s) | "
                "outreach=%s | tokens=%d+%d | cost=$%.5f",
                cluster.target_domain,
                intel.suspected_vendor,
                intel.confidence * 100,
                intel.tier.value,
                intel.outreach_window,
                intel.input_tokens,
                intel.output_tokens,
                intel.estimated_cost_usd,
            )

            # Register new patterns back to fingerprinter
            if self._fingerprinter and intel.new_vendor_patterns:
                for p in intel.new_vendor_patterns:
                    try:
                        self._fingerprinter.register_pattern(
                            p.pattern, p.vendor, p.category, p.confidence
                        )
                        logger.info(
                            "[STAGE5] Registered new pattern: %s → %s",
                            p.pattern, p.vendor,
                        )
                    except Exception as exc:
                        logger.debug("[STAGE5] Pattern registration failed: %s", exc)

            return intel

        except Exception as exc:
            logger.error(
                "[STAGE5] Enrichment failed for %s: %s",
                cluster.target_domain, exc,
            )
            # Return a minimal fallback object so the pipeline doesn't crash
            return DealIntelligenceObject(
                cluster_id=cluster.cluster_id,
                target_company=cluster.target_domain,
                suspected_vendor=", ".join(cluster.vendors[:2]) or "unknown",
                vendor_category="other",
                signals=[cluster.summary],
                confidence=cluster.confidence,
                tier=cluster.tier,
                deal_closed_estimate="Unknown",
                outreach_window="Unknown",
                reasoning=f"AI enrichment failed: {exc}",
                model_used=self._model,
                metadata={"error": str(exc)},
            )

    async def enrich_clusters(
        self, clusters: list[DealCluster]
    ) -> list[DealIntelligenceObject]:
        """
        Enrich all clusters concurrently (respects concurrency limit).
        Skips clusters below min_tier to conserve API budget.
        """
        tier_order = {ConfidenceTier.LOW: 0, ConfidenceTier.MEDIUM: 1, ConfidenceTier.HIGH: 2}
        min_threshold = tier_order[self._min_tier]

        eligible = [c for c in clusters if tier_order[c.tier] >= min_threshold]
        skipped = len(clusters) - len(eligible)

        if skipped > 0:
            logger.info("[STAGE5] Skipping %d LOW-tier clusters (below min_tier=%s)", skipped, self._min_tier.value)

        if not eligible:
            logger.warning("[STAGE5] No clusters meet min_tier=%s", self._min_tier.value)
            return []

        logger.info(
            "[STAGE5] Enriching %d clusters with %s (concurrency=%d)",
            len(eligible), self._model, self._concurrency,
        )

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _guarded(cluster: DealCluster) -> DealIntelligenceObject:
            async with semaphore:
                return await self.enrich_cluster(cluster)

        results = await asyncio.gather(
            *[_guarded(c) for c in eligible],
            return_exceptions=True,
        )

        intel_objects: list[DealIntelligenceObject] = []
        for r in results:
            if isinstance(r, DealIntelligenceObject):
                intel_objects.append(r)
            else:
                logger.error("[STAGE5] Cluster enrichment raised: %s", r)

        self.log_spend_summary()
        return intel_objects

    def log_spend_summary(self) -> None:
        logger.info(
            "[STAGE5] Spend summary — input=%d tok, output=%d tok, total=$%.5f",
            self._total_input_tokens,
            self._total_output_tokens,
            self._total_cost,
        )

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost


# ── Report formatter ──────────────────────────────────────────────────────────


def intelligence_report(objects: list[DealIntelligenceObject]) -> str:
    """
    Formatted text report of all DealIntelligenceObjects.
    Extends cluster_report() with LLM-derived insights.
    """
    if not objects:
        return "No deal intelligence generated."

    tier_icons = {
        ConfidenceTier.HIGH: "🔴 HIGH",
        ConfidenceTier.MEDIUM: "🟡 MEDIUM",
        ConfidenceTier.LOW: "⚪ LOW",
    }

    total_cost = sum(o.estimated_cost_usd for o in objects)

    lines = [
        "═" * 72,
        "  GTM INTELLIGENCE PLATFORM — DEAL INTELLIGENCE REPORT",
        f"  Generated : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"  Records   : {len(objects)}",
        f"  AI Cost   : ${total_cost:.5f}",
        "═" * 72,
        "",
    ]

    for i, obj in enumerate(objects, 1):
        lines += [
            f"  [{i:02d}] {tier_icons[obj.tier]} — {obj.target_company}",
            f"       Vendor      : {obj.suspected_vendor} ({obj.vendor_category})",
            f"       Confidence  : {obj.confidence:.0%}",
            f"       Deal closed : {obj.deal_closed_estimate}",
            f"       Reach out   : {obj.outreach_window}",
            f"       Reasoning   : {obj.reasoning[:120]}",
        ]
        for sig in obj.signals[:3]:
            lines.append(f"         • {sig[:80]}")
        if obj.new_vendor_patterns:
            pats = ", ".join(f"{p.pattern}→{p.vendor}" for p in obj.new_vendor_patterns[:3])
            lines.append(f"       New patterns: {pats}")
        lines.append("")

    lines += [
        "─" * 72,
        f"  HIGH priority : {sum(1 for o in objects if o.tier == ConfidenceTier.HIGH)}",
        f"  MEDIUM        : {sum(1 for o in objects if o.tier == ConfidenceTier.MEDIUM)}",
        f"  Total AI cost : ${total_cost:.5f}",
        "═" * 72,
    ]

    return "\n".join(lines)
