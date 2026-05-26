"""
models/deal_intelligence.py
----------------------------
DealIntelligenceObject — structured output of Stage 5 AI enrichment.

Wraps a DealCluster with LLM-generated intelligence:
  - Vendor attribution (most likely vendor + category)
  - Deal close date estimate
  - Recommended outreach window
  - Confidence refined by LLM reasoning
  - New vendor patterns for VendorFingerprinter expansion
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from models.signal import ConfidenceTier


@dataclass
class VendorPatternSuggestion:
    """A new subdomain/path pattern the LLM identified for the fingerprint table."""
    pattern: str
    vendor: str
    category: str
    confidence: float = 0.75
    source: str = "llm_discovery"


@dataclass
class DealIntelligenceObject:
    """
    Final enriched intelligence record produced by Stage 5.

    Attributes
    ----------
    cluster_id          : links back to the originating DealCluster
    target_company      : apex domain being monitored (e.g. "acme.com")
    suspected_vendor    : most likely vendor (e.g. "Salesforce")
    vendor_category     : product category (e.g. "crm", "payments", "identity")
    signals             : human-readable list of signal descriptions
    confidence          : LLM-refined confidence 0.0–1.0
    tier                : LOW / MEDIUM / HIGH based on confidence
    deal_closed_estimate: estimated deal close month/quarter (e.g. "April 2026")
    outreach_window     : recommended contact window (e.g. "Oct–Dec 2026")
    reasoning           : LLM's chain-of-thought explanation
    new_vendor_patterns : patterns to register back to VendorFingerprinter
    enriched_at         : UTC timestamp of enrichment
    model_used          : OpenAI model that produced this record
    input_tokens        : tokens consumed (for cost tracking)
    output_tokens       : tokens generated (for cost tracking)
    estimated_cost_usd  : estimated API spend for this enrichment
    """
    cluster_id: str
    target_company: str
    suspected_vendor: str
    vendor_category: str
    signals: list[str]
    confidence: float
    tier: ConfidenceTier
    deal_closed_estimate: str
    outreach_window: str
    reasoning: str

    intelligence_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    new_vendor_patterns: list[VendorPatternSuggestion] = field(default_factory=list)
    enriched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: str = "gpt-4o-mini"
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intelligence_id": self.intelligence_id,
            "cluster_id": self.cluster_id,
            "target_company": self.target_company,
            "suspected_vendor": self.suspected_vendor,
            "vendor_category": self.vendor_category,
            "confidence": round(self.confidence, 4),
            "tier": self.tier.value,
            "deal_closed_estimate": self.deal_closed_estimate,
            "outreach_window": self.outreach_window,
            "signals": self.signals,
            "reasoning": self.reasoning,
            "new_vendor_patterns": [
                {
                    "pattern": p.pattern,
                    "vendor": p.vendor,
                    "category": p.category,
                    "confidence": p.confidence,
                }
                for p in self.new_vendor_patterns
            ],
            "enriched_at": self.enriched_at.isoformat(),
            "model_used": self.model_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "metadata": self.metadata,
        }
