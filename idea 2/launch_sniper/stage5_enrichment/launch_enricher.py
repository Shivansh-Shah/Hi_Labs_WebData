# Launch enricher — calls GPT-4o-mini to generate launch summaries and estimated release windows.
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from models.signal import Signal
from models.launch_intelligence import CounterPlaybook, LaunchIntelligenceObject

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent.parent / ".env")

_MODEL = "gpt-4o-mini"
_COST_PER_CLUSTER = 0.0002          # ~$0.0002 at gpt-4o-mini pricing
_TEMPERATURE = 0.2

_SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst specializing in detecting "
    "unreleased product launches. You will be given signals collected from "
    "WHOIS records, USPTO trademark filings, and robots.txt changes. "
    "Your job is to analyze these signals and produce actionable intelligence."
)

_JSON_SCHEMA = """{
  "suspected_product":      "string describing the suspected product, or null",
  "estimated_launch_date":  "estimated launch date as a string (e.g. 'Q3 2026'), or null",
  "confidence_adjustment":  <float between -0.2 and +0.2>,
  "reasoning":              "1-3 sentence explanation of your assessment",
  "counter_playbook": {
    "seo":     ["action 1", "action 2", "action 3"],
    "content": ["action 1", "action 2", "action 3"],
    "sales":   ["action 1", "action 2", "action 3"]
  }
}"""


# ── Prompt builder ────────────────────────────────────────────────────────────


def _build_user_prompt(cluster: dict[str, Any]) -> str:
    domain    = cluster["competitor_domain"]
    confidence = cluster["confidence"]
    suspected  = cluster.get("suspected_product") or "unknown"
    signals: list[Signal] = cluster.get("signals", [])

    signal_lines = "\n".join(
        f"  - [{s.source.upper()}] {s.signal_type} | "
        f"vendor_hint={s.vendor_hint!r} | "
        f"raw_data={json.dumps({k: v for k, v in s.raw_data.items() if k not in ('previous_robots', 'current_robots')}, default=str)}"
        for s in signals
    )

    return (
        f"Competitor domain: {domain}\n"
        f"Current confidence score: {confidence:.2f}\n"
        f"Suspected product (from signal analysis): {suspected}\n\n"
        f"Signals collected ({len(signals)} total):\n{signal_lines}\n\n"
        f"Return ONLY a JSON object matching this exact schema:\n{_JSON_SCHEMA}"
    )


# ── Response parser ───────────────────────────────────────────────────────────


def _parse_openai_response(raw: str) -> dict[str, Any]:
    """
    Extract a JSON object from the model response.
    Strips markdown fences if present (```json ... ```).
    """
    text = raw.strip()
    # Strip optional ```json fence
    text = re_sub_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find the first {...} block
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {}


def re_sub_fence(text: str) -> str:
    """Strip markdown code fences that the model sometimes adds."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()


def _build_launch_signals(signals: list[Signal]) -> list[str]:
    """Human-readable one-liner per signal for the launch_signals list."""
    out: list[str] = []
    for s in signals:
        type_label = s.signal_type.replace("_", " ").title()
        hint = (
            s.raw_data.get("suspected_product")
            or s.raw_data.get("product_hint")
            or s.raw_data.get("product_category")
            or s.vendor_hint
            or ""
        )
        out.append(f"{type_label} — {hint} [{s.source}]" if hint else f"{type_label} [{s.source}]")
    return out


# ── Enricher ──────────────────────────────────────────────────────────────────


class LaunchEnricher:
    """
    Stage 5: AI enrichment of correlated launch clusters.

    Calls GPT-4o-mini once per cluster with a structured prompt containing
    all signal data. Parses the JSON response into a LaunchIntelligenceObject.

    Graceful degradation
    --------------------
    If OpenAI returns invalid JSON, a LaunchIntelligenceObject is still built
    from the cluster data with the raw model text stored as reasoning and an
    empty CounterPlaybook — so the pipeline never crashes here.

    Cost
    ----
    ~$0.0002 per cluster at gpt-4o-mini pricing.
    """

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key or api_key == "your_key_here":
            raise ValueError(
                "OPENAI_API_KEY is not set. Add it to idea2/launch_sniper/.env"
            )
        self._client = AsyncOpenAI(api_key=api_key)

    async def enrich(
        self, clusters: list[dict[str, Any]]
    ) -> list[LaunchIntelligenceObject]:
        results: list[LaunchIntelligenceObject] = []
        total_cost = 0.0

        for i, cluster in enumerate(clusters, 1):
            domain = cluster["competitor_domain"]
            print(f"[ENRICHER] Enriching cluster {i}/{len(clusters)}: {domain} ...")
            obj = await self._enrich_cluster(cluster)
            results.append(obj)
            total_cost += _COST_PER_CLUSTER
            logger.info(
                "[ENRICHER] %s → confidence=%.2f suspected=%r cost_so_far=$%.4f",
                domain, obj.confidence, obj.suspected_product, total_cost,
            )

        print(f"[ENRICHER] Done — {len(results)} object(s), estimated cost: ${total_cost:.4f}")
        return results

    async def _enrich_cluster(
        self, cluster: dict[str, Any]
    ) -> LaunchIntelligenceObject:
        domain     = cluster["competitor_domain"]
        base_conf  = cluster["confidence"]
        signals: list[Signal] = cluster.get("signals", [])

        user_prompt = _build_user_prompt(cluster)

        # ── Call OpenAI ───────────────────────────────────────────────────────
        raw_text = ""
        try:
            response = await self._client.chat.completions.create(
                model=_MODEL,
                temperature=_TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
            )
            raw_text = response.choices[0].message.content or ""
            logger.debug("[ENRICHER] Raw response for %s: %s", domain, raw_text[:300])
        except Exception as exc:
            logger.error("[ENRICHER] OpenAI call failed for %s: %s", domain, exc)
            print(f"[ENRICHER] OpenAI error for {domain}: {exc}")

        # ── Parse JSON response ───────────────────────────────────────────────
        parsed = _parse_openai_response(raw_text) if raw_text else {}

        if not parsed:
            logger.warning("[ENRICHER] JSON parse failed for %s — using fallback", domain)
            print(f"[ENRICHER] Could not parse JSON for {domain} — building fallback object")

        # ── Build LaunchIntelligenceObject ────────────────────────────────────
        adjustment = float(parsed.get("confidence_adjustment", 0.0))
        # Clamp to [-0.2, +0.2] then final clamp to [0.0, 1.0]
        adjustment = max(-0.2, min(0.2, adjustment))
        final_confidence = max(0.0, min(1.0, base_conf + adjustment))

        playbook_data = parsed.get("counter_playbook", {})
        playbook = (
            CounterPlaybook.from_dict(playbook_data)
            if isinstance(playbook_data, dict)
            else CounterPlaybook()
        )

        suspected = (
            parsed.get("suspected_product")
            or cluster.get("suspected_product")
        )

        return LaunchIntelligenceObject(
            competitor=domain,
            suspected_product=suspected or None,
            launch_signals=_build_launch_signals(signals),
            confidence=final_confidence,
            estimated_launch_date=parsed.get("estimated_launch_date"),
            counter_playbook=playbook,
            signal_count=cluster["signal_count"],
            signal_sources=cluster["signal_sources"],
        )
