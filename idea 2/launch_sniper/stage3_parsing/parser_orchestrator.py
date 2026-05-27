# Parser orchestrator — runs NER and confidence scoring across raw signals from Stage 1.
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from models.signal import Signal, SignalSource

logger = logging.getLogger(__name__)

# Keywords that suggest a product category from a trademark mark name
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "payments":     ["pay", "payment", "billing", "invoice", "checkout", "wallet", "cash"],
    "analytics":    ["analytics", "insight", "metric", "dashboard", "report", "data", "intel"],
    "security":     ["secure", "security", "auth", "identity", "guard", "protect", "shield"],
    "ai / ml":      ["ai", "gpt", "intelligence", "neural", "model", "predict", "smart"],
    "crm":          ["crm", "customer", "sales", "pipeline", "lead", "contact", "deal"],
    "devtools":     ["sdk", "api", "developer", "cli", "cloud", "deploy", "infra", "platform"],
    "hr / people":  ["hr", "people", "talent", "hire", "recruit", "workforce", "employee"],
    "marketing":    ["market", "campaign", "email", "seo", "content", "brand", "ads"],
    "collaboration":["collab", "team", "workspace", "chat", "meet", "video", "share"],
}


# ── Per-source enrichment functions ──────────────────────────────────────────


def _enrich_whois(signal: Signal) -> Signal:
    """
    Guess a product name from the new domain found in vendor_hint.
    e.g. "acme-payments.com" → "Acme Payments"
    """
    domain = signal.vendor_hint or signal.raw_data.get("new_domain_found", "")
    if not domain:
        return signal

    # Strip TLD and split on hyphens / dots
    stem = re.sub(r"\.[a-z]{2,}$", "", domain.lower())
    parts = re.split(r"[-_.]", stem)

    # Drop single-char tokens and known filler words
    stop = {"com", "io", "co", "the", "get", "my", "app", "inc", "llc"}
    words = [p for p in parts if len(p) > 1 and p not in stop]

    suspected = " ".join(w.capitalize() for w in words) if words else None
    if suspected:
        signal.raw_data["suspected_product"] = suspected
        logger.debug("[PARSER] WHOIS %s → suspected_product=%r", domain, suspected)

    return signal


def _enrich_trademark(signal: Signal) -> Signal:
    """
    Clean the mark name and guess a product category from its keywords.
    """
    mark_name: str = (
        signal.vendor_hint
        or signal.raw_data.get("markName", "")
        or signal.raw_data.get("mark", "")
        or ""
    )
    if not mark_name:
        return signal

    # Clean: strip legal suffixes (®, ™, (R), INC, LLC, LTD, CO.)
    cleaned = re.sub(
        r"\s*(®|™|\(r\)|,?\s*(inc\.?|llc\.?|ltd\.?|co\.?|corp\.?))\s*$",
        "",
        mark_name,
        flags=re.IGNORECASE,
    ).strip()

    if cleaned and cleaned != mark_name:
        signal.raw_data["mark_name_clean"] = cleaned

    # Guess category by scanning keywords in the cleaned name
    lower = cleaned.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            signal.raw_data["product_category"] = category
            logger.debug("[PARSER] TRADEMARK %r → category=%r", cleaned, category)
            break

    return signal


def _enrich_robots_txt(signal: Signal) -> Signal:
    """
    Turn the most suspicious blocked path into a readable product hint.
    e.g. "/new-payments/" → "payments"
         "/beta-checkout-v2" → "checkout v2"
    """
    new_paths: list[str] = signal.raw_data.get("new_paths", [])
    if not new_paths:
        return signal

    # Use vendor_hint (already set to suspicious[0]) or first new path
    raw_path = signal.vendor_hint or new_paths[0]

    # Strip leading slash and known prefixes, then humanise
    hint = re.sub(
        r"^/?(new-|coming-|beta-|launch-|preview-|product-|announce-)",
        "",
        raw_path,
        flags=re.IGNORECASE,
    )
    hint = hint.strip("/").replace("-", " ").replace("_", " ").strip()
    # Remove trailing version tags like "v2", "v3"
    hint = re.sub(r"\s+v\d+$", "", hint, flags=re.IGNORECASE).strip()

    if hint:
        signal.raw_data["product_hint"] = hint
        logger.debug("[PARSER] ROBOTS %r → product_hint=%r", raw_path, hint)

    return signal


# ── Orchestrator ──────────────────────────────────────────────────────────────


class SniperParserOrchestrator:
    """
    Lightweight Stage 3 enrichment pass over raw Stage 1 signals.

    Each signal type gets a dedicated enrichment function that mutates
    signal.raw_data in-place to add a human-readable product hint.
    All three enrichment types run concurrently via asyncio.gather.

    Enrichment added per source
    ---------------------------
    WHOIS       → raw_data["suspected_product"]   (e.g. "Acme Payments")
    TRADEMARK   → raw_data["product_category"]    (e.g. "payments")
                  raw_data["mark_name_clean"]      (legal suffixes stripped)
    ROBOTS_TXT  → raw_data["product_hint"]        (e.g. "checkout v2")
    """

    async def parse(self, signals: list[Signal]) -> list[Signal]:
        if not signals:
            return signals

        # Bucket by source type for concurrent enrichment
        whois_sigs     = [s for s in signals if s.source == SignalSource.WHOIS]
        trademark_sigs = [s for s in signals if s.source == SignalSource.TRADEMARK]
        robots_sigs    = [s for s in signals if s.source == SignalSource.ROBOTS_TXT]

        # Run all three enrichment batches concurrently (pure CPU work wrapped
        # in coroutines so gather can schedule them together)
        enriched_whois, enriched_trademark, enriched_robots = await asyncio.gather(
            self._enrich_batch(whois_sigs,     _enrich_whois),
            self._enrich_batch(trademark_sigs, _enrich_trademark),
            self._enrich_batch(robots_sigs,    _enrich_robots_txt),
        )

        result = enriched_whois + enriched_trademark + enriched_robots
        logger.info(
            "[PARSER] Enriched %d signals — whois=%d trademark=%d robots=%d",
            len(result), len(enriched_whois), len(enriched_trademark), len(enriched_robots),
        )
        print(
            f"[PARSER] Enriched {len(result)} signals "
            f"(whois={len(enriched_whois)}, "
            f"trademark={len(enriched_trademark)}, "
            f"robots={len(enriched_robots)})"
        )
        return result

    @staticmethod
    async def _enrich_batch(
        signals: list[Signal],
        fn: Any,
    ) -> list[Signal]:
        """Apply a synchronous enrichment function to every signal in the batch."""
        return [fn(s) for s in signals]
