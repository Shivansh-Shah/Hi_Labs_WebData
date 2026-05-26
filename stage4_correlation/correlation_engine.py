"""
stage4_correlation/correlation_engine.py
------------------------------------------
Stage 4 — Correlation Engine (top-level orchestrator).

Wires together:
  1. ParserOrchestrator (Stage 3) — if raw Signals are passed in
  2. SignalClusterer               — pandas + NetworkX clustering
  3. FingerprintExpansion          — LLM-discovered patterns fed back to fingerprinter

Entry points:
  correlate_signals(parsed_signals) → list[DealCluster]
  correlate_raw(raw_signals)        → list[DealCluster]  (runs Stage 3 first)

Also provides:
  cluster_report(clusters)          → str  (printable summary table)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Iterable

from models.signal import Signal, ConfidenceTier
from models.parsed_signal import ParsedSignal
from models.deal_cluster import DealCluster
from stage3_parsing.parser_orchestrator import ParserOrchestrator
from stage4_correlation.signal_clusterer import SignalClusterer

logger = logging.getLogger(__name__)


class CorrelationEngine:
    """
    Stage 4 — turns raw Signals into correlated DealClusters.

    Usage — from raw Signals (Stages 1+2 output):
    -----
        engine = CorrelationEngine()
        clusters = await engine.correlate_raw(stage1_signals)
        for c in clusters:
            print(c.to_dict())

    Usage — from already-parsed ParsedSignals:
    -----
        clusters = await engine.correlate_signals(parsed_signals)
    """

    def __init__(self, window_days: int = 30) -> None:
        self._parser = ParserOrchestrator()
        self._clusterer = SignalClusterer(window_days=window_days)
        self._window_days = window_days

    async def correlate_raw(self, signals: Iterable[Signal]) -> list[DealCluster]:
        """
        Full pipeline: raw Signal → parse → cluster → DealCluster list.
        Use this when feeding Stage 1/2 output directly into Stage 4.
        """
        raw = list(signals)
        logger.info("[STAGE4] correlate_raw: %d raw signals", len(raw))

        # Stage 3: parse
        parsed = await self._parser.parse(raw)
        logger.info("[STAGE4] After parsing: %d parsed signals", len(parsed))

        # Stage 4: cluster
        return await self.correlate_signals(parsed)

    async def correlate_signals(
        self, parsed_signals: Iterable[ParsedSignal]
    ) -> list[DealCluster]:
        """
        Cluster already-parsed signals into DealClusters.
        """
        parsed = list(parsed_signals)
        if not parsed:
            logger.warning("[STAGE4] No parsed signals to correlate")
            return []

        clusters = await self._clusterer.cluster(parsed)

        # Log summary
        high = sum(1 for c in clusters if c.tier == ConfidenceTier.HIGH)
        med  = sum(1 for c in clusters if c.tier == ConfidenceTier.MEDIUM)
        low  = sum(1 for c in clusters if c.tier == ConfidenceTier.LOW)
        logger.info(
            "[STAGE4] %d clusters: %d HIGH / %d MEDIUM / %d LOW",
            len(clusters), high, med, low,
        )
        return clusters

    def filter_by_tier(
        self,
        clusters: list[DealCluster],
        min_tier: ConfidenceTier = ConfidenceTier.MEDIUM,
    ) -> list[DealCluster]:
        """Return only clusters at or above the given tier."""
        tier_order = {
            ConfidenceTier.LOW: 0,
            ConfidenceTier.MEDIUM: 1,
            ConfidenceTier.HIGH: 2,
        }
        threshold = tier_order[min_tier]
        return [c for c in clusters if tier_order[c.tier] >= threshold]


# ── Report generator ──────────────────────────────────────────────────────────


def cluster_report(clusters: list[DealCluster]) -> str:
    """
    Generate a human-readable text report of all clusters.
    Useful for CLI output and Slack alerts.
    """
    if not clusters:
        return "No deal clusters detected."

    lines: list[str] = [
        "═" * 72,
        "  GTM INTELLIGENCE PLATFORM — DEAL CLUSTER REPORT",
        f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"  Total clusters: {len(clusters)}",
        "═" * 72,
        "",
    ]

    tier_icons = {
        ConfidenceTier.HIGH: "🔴 HIGH",
        ConfidenceTier.MEDIUM: "🟡 MEDIUM",
        ConfidenceTier.LOW: "⚪ LOW",
    }

    for i, c in enumerate(clusters, 1):
        lines += [
            f"  [{i:02d}] {tier_icons[c.tier]} — {c.target_domain}",
            f"       Confidence  : {c.confidence:.0%}",
            f"       Signals     : {c.signal_count} across {c.source_diversity} source(s)",
            f"       Vendors     : {', '.join(c.vendors[:4]) or 'unknown'}",
            f"       Window      : {c.window_start.strftime('%Y-%m-%d')} → "
                                  f"{c.window_end.strftime('%Y-%m-%d')} "
                                  f"({c.metadata.get('window_days', '?')}d)",
            f"       Sources     : {', '.join(c.metadata.get('sources', []))}",
            f"       Summary     : {c.summary}",
            "",
        ]

    lines += [
        "─" * 72,
        f"  HIGH priority deals: {sum(1 for c in clusters if c.tier == ConfidenceTier.HIGH)}",
        f"  MEDIUM priority    : {sum(1 for c in clusters if c.tier == ConfidenceTier.MEDIUM)}",
        "═" * 72,
    ]

    return "\n".join(lines)
