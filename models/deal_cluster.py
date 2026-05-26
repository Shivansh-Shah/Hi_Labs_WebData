"""
models/deal_cluster.py
-----------------------
DealCluster — output of Stage 4 correlation engine.

A cluster groups multiple ParsedSignals for the same target company
within a rolling time window. Its confidence score is a weighted
function of signal count, source diversity, and vendor signal strength.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from models.signal import ConfidenceTier


@dataclass
class DealCluster:
    """
    Correlated group of signals pointing to a likely enterprise deal.

    Attributes
    ----------
    cluster_id      : stable UUID
    target_domain   : company being monitored
    signal_ids      : IDs of ParsedSignals in this cluster
    signal_count    : total signals
    source_diversity: number of distinct sources (CT, DNS, Wayback, GitHub…)
    vendors         : deduplicated vendor hints from all signals in cluster
    window_start    : earliest signal timestamp in window
    window_end      : latest signal timestamp in window
    confidence      : final correlated confidence score (0.0–1.0)
    tier            : LOW / MEDIUM / HIGH based on confidence
    summary         : human-readable one-line description (filled by Stage 5)
    metadata        : arbitrary k/v for downstream stages
    """
    target_domain: str
    signal_ids: list[str]
    signal_count: int
    source_diversity: int
    vendors: list[str]
    window_start: datetime
    window_end: datetime
    confidence: float
    tier: ConfidenceTier

    cluster_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "target_domain": self.target_domain,
            "signal_count": self.signal_count,
            "source_diversity": self.source_diversity,
            "vendors": self.vendors,
            "confidence": round(self.confidence, 4),
            "tier": self.tier.value,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "window_days": (self.window_end - self.window_start).days,
            "signal_ids": self.signal_ids,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }
