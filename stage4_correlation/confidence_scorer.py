"""
stage4_correlation/confidence_scorer.py
-----------------------------------------
Confidence scoring function for Stage 4 clusters.

Scoring model (additive, capped at 1.0):

  BASE SCORE from raw signal confidences:
    weighted_avg(signal.confidence for signal in cluster)

  MULTIPLIERS applied on top:
    +0.15 per additional unique source (beyond the first)       → source diversity
    +0.10 per distinct vendor detected                          → vendor specificity
    +0.05 per unique signal type (beyond 1)                     → type diversity
    +0.08 if GitHub COMMIT_SPIKE or CUSTOMER_NAME present       → development activity
    +0.10 if CT_LOG + DNS signals co-occur (cert + subdomain)   → strongest pairing
    +0.08 if WAYBACK integration page co-occurs                 → public evidence
    -0.10 if all signals from single source                     → source concentration penalty
    -0.05 if window_days > 25                                   → signals spread too far apart

  TIER assignment:
    ≥ 0.80 → HIGH
    ≥ 0.55 → MEDIUM
    <  0.55 → LOW
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from models.signal import SignalSource, SignalType, ConfidenceTier

if TYPE_CHECKING:
    from models.parsed_signal import ParsedSignal


def compute_cluster_confidence(
    signals: list["ParsedSignal"],
    window_days: int,
) -> tuple[float, ConfidenceTier]:
    """
    Compute a final confidence score for a cluster of ParsedSignals.

    Parameters
    ----------
    signals     : all ParsedSignals in this cluster
    window_days : how many days the cluster spans

    Returns
    -------
    (confidence: float, tier: ConfidenceTier)
    """
    if not signals:
        return 0.0, ConfidenceTier.LOW

    # ── Base score: confidence-weighted average ───────────────────────────
    weights = [max(s.confidence, 0.01) for s in signals]
    total_weight = sum(weights)
    base = sum(s.confidence * w for s, w in zip(signals, weights)) / total_weight

    # ── Source diversity bonus ────────────────────────────────────────────
    sources = {s.source for s in signals}
    source_bonus = 0.15 * max(len(sources) - 1, 0)

    # ── Vendor specificity bonus ──────────────────────────────────────────
    all_vendors: set[str] = set()
    for s in signals:
        all_vendors.update(s.vendor_hints)
    vendor_bonus = 0.10 * min(len(all_vendors), 3)  # cap at 3 vendors

    # ── Signal type diversity bonus ───────────────────────────────────────
    sig_types = {s.signal_type for s in signals}
    type_bonus = 0.05 * max(len(sig_types) - 1, 0)

    # ── Special pairing bonuses ───────────────────────────────────────────
    pairing_bonus = 0.0

    has_ct = any(s.source == SignalSource.CT_LOG for s in signals)
    has_dns = any(s.source == SignalSource.DNS for s in signals)
    has_github = any(s.source == SignalSource.GITHUB for s in signals)
    has_wayback = any(s.source == SignalSource.WAYBACK for s in signals)
    has_commit_spike = any(s.signal_type == SignalType.COMMIT_SPIKE for s in signals)
    has_customer_mention = any(
        s.signal_type == SignalType.CUSTOMER_NAME_IN_COMMIT for s in signals
    )
    has_integration_page = any(
        s.signal_type == SignalType.NEW_INTEGRATION_PAGE for s in signals
    )

    if has_ct and has_dns:
        pairing_bonus += 0.10  # cert + subdomain = strongest evidence pair
    if has_github and (has_commit_spike or has_customer_mention):
        pairing_bonus += 0.08  # active development signal
    if has_wayback and has_integration_page:
        pairing_bonus += 0.08  # public integration page appeared

    # ── Penalties ─────────────────────────────────────────────────────────
    penalty = 0.0

    if len(sources) == 1:
        penalty += 0.10  # all signals from one source = could be noise

    if window_days > 25:
        penalty += 0.05  # signals spread very thin over time

    # ── Final score ───────────────────────────────────────────────────────
    score = base + source_bonus + vendor_bonus + type_bonus + pairing_bonus - penalty
    score = max(0.0, min(1.0, score))

    # ── Tier ──────────────────────────────────────────────────────────────
    if score >= 0.80:
        tier = ConfidenceTier.HIGH
    elif score >= 0.55:
        tier = ConfidenceTier.MEDIUM
    else:
        tier = ConfidenceTier.LOW

    return round(score, 4), tier
