# Correlation engine — groups signals into launch clusters using pandas rolling window and NetworkX.
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from models.signal import Signal, SignalSource

logger = logging.getLogger(__name__)

# Confidence tiers based on how many distinct sources corroborate the cluster
_CONFIDENCE_BY_SOURCE_COUNT: dict[int, float] = {
    1: 0.5,
    2: 0.7,
    3: 0.9,
}

# Only surface clusters whose confidence meets this threshold
_MIN_CONFIDENCE = 0.5


# ── Helpers ───────────────────────────────────────────────────────────────────


def _product_hints_from_signals(signals: list[Signal]) -> list[str]:
    """
    Collect every product hint stored in raw_data by the parser stage.
    Tries all three hint keys in priority order.
    """
    hints: list[str] = []
    for s in signals:
        for key in ("suspected_product", "product_hint", "product_category"):
            val = s.raw_data.get(key)
            if val and isinstance(val, str) and val.strip():
                hints.append(val.strip())
                break
    return hints


def _pick_suspected_product(hints: list[str]) -> str | None:
    """Return the most frequently occurring hint, or None if hints is empty."""
    if not hints:
        return None
    return Counter(hints).most_common(1)[0][0]


def _human_readable(signal: Signal) -> str:
    """One-line description of a signal for the launch_signals list."""
    type_label = signal.signal_type.replace("_", " ").title()
    hint = (
        signal.raw_data.get("suspected_product")
        or signal.raw_data.get("product_hint")
        or signal.raw_data.get("product_category")
        or signal.vendor_hint
        or ""
    )
    if hint:
        return f"{type_label} — {hint} ({signal.source})"
    return f"{type_label} ({signal.source})"


# ── Engine ────────────────────────────────────────────────────────────────────


class SniperCorrelationEngine:
    """
    Groups enriched signals into per-competitor launch clusters.

    Clustering rules
    ----------------
    - Signals are grouped by target_domain.
    - Confidence is determined solely by the number of *distinct* sources
      that fired for the domain (1 → 0.5, 2 → 0.7, 3 → 0.9).
    - Product hints from all signals are tallied; the most common wins.
    - Clusters below _MIN_CONFIDENCE (0.5) are dropped.
    - Output is sorted by confidence descending.

    Output cluster dict schema
    --------------------------
    {
      "competitor_domain": str,
      "signals":           list[Signal],
      "confidence":        float,
      "suspected_product": str | None,
      "signal_sources":    list[str],      # deduplicated
      "signal_count":      int,
      "earliest_signal":   datetime,
    }
    """

    def correlate(self, signals: list[Signal]) -> list[dict[str, Any]]:
        if not signals:
            logger.info("[CORRELATE] No signals to correlate")
            return []

        # Group by target_domain
        groups: dict[str, list[Signal]] = {}
        for s in signals:
            groups.setdefault(s.target_domain, []).append(s)

        clusters: list[dict[str, Any]] = []
        for domain, domain_signals in groups.items():
            cluster = self._build_cluster(domain, domain_signals)
            if cluster["confidence"] >= _MIN_CONFIDENCE:
                clusters.append(cluster)

        clusters.sort(key=lambda c: c["confidence"], reverse=True)

        for c in clusters:
            logger.info(
                "[CORRELATE] %s — confidence=%.2f sources=%s suspected=%r signals=%d",
                c["competitor_domain"],
                c["confidence"],
                c["signal_sources"],
                c["suspected_product"],
                c["signal_count"],
            )
            print(
                f"[CORRELATE] {c['competitor_domain']} | "
                f"confidence={c['confidence']:.2f} | "
                f"sources={c['signal_sources']} | "
                f"suspected={c['suspected_product']!r} | "
                f"{c['signal_count']} signal(s)"
            )

        logger.info(
            "[CORRELATE] %d cluster(s) from %d signal(s) across %d domain(s)",
            len(clusters), len(signals), len(groups),
        )
        return clusters

    def _build_cluster(
        self, domain: str, signals: list[Signal]
    ) -> dict[str, Any]:
        unique_sources = sorted({s.source for s in signals})
        source_count   = len(unique_sources)
        confidence     = _CONFIDENCE_BY_SOURCE_COUNT.get(source_count, 0.5)

        hints             = _product_hints_from_signals(signals)
        suspected_product = _pick_suspected_product(hints)

        earliest: datetime = min(
            (s.timestamp for s in signals),
            default=datetime.now(timezone.utc),
        )

        return {
            "competitor_domain": domain,
            "signals":           signals,
            "confidence":        confidence,
            "suspected_product": suspected_product,
            "signal_sources":    unique_sources,
            "signal_count":      len(signals),
            "earliest_signal":   earliest,
        }
