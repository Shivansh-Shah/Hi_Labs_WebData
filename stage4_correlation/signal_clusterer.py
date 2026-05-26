"""
stage4_correlation/signal_clusterer.py
----------------------------------------
Stage 4 — Signal Clustering Engine.

This is the core IP of the GTM Intelligence Platform.

Two-layer clustering:
  LAYER 1 — pandas time-window grouping
    Group signals by (target_domain, rolling_window) using pandas
    DatetimeIndex + rolling(window='30D').
    Emit a candidate cluster for each company where signal_count ≥ MIN_SIGNALS.

  LAYER 2 — NetworkX graph clustering
    Build a bipartite graph: signals ↔ vendors.
    Nodes: signal IDs + vendor names.
    Edges: signal → vendor hint (with confidence weight).
    Run connected-components to find tightly-coupled signal clusters.
    Merge graph clusters with pandas window clusters for final result.

Output: list[DealCluster], sorted by confidence (descending).

Why two layers?
  Pandas gives us the time dimension (deals happen in windows, not instants).
  NetworkX gives us the vendor co-occurrence dimension (multiple signals
  pointing to the same vendor from different sources = strong evidence).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

import networkx as nx
import pandas as pd

from models.parsed_signal import ParsedSignal
from models.deal_cluster import DealCluster
from models.signal import ConfidenceTier
from stage4_correlation.confidence_scorer import compute_cluster_confidence

logger = logging.getLogger(__name__)

# Minimum signals to form a cluster (lone signals → noise)
MIN_SIGNALS_FOR_CLUSTER = 2
MIN_SIGNALS_HIGH_TIER = 3  # need at least 3 for HIGH confidence

# Rolling window for co-occurrence
DEFAULT_WINDOW_DAYS = 30


class SignalClusterer:
    """
    Groups ParsedSignals into DealClusters using pandas time-windowing
    and NetworkX graph clustering.

    Usage
    -----
        clusterer = SignalClusterer(window_days=30)
        clusters = await clusterer.cluster(parsed_signals)
    """

    def __init__(self, window_days: int = DEFAULT_WINDOW_DAYS) -> None:
        self.window_days = window_days

    async def cluster(self, signals: Iterable[ParsedSignal]) -> list[DealCluster]:
        """
        Main entry point. Runs clustering in a thread pool to avoid
        blocking the event loop with pandas/networkx work.
        """
        signal_list = list(signals)
        if not signal_list:
            return []
        logger.info("[STAGE4] Clustering %d parsed signals", len(signal_list))
        return await asyncio.to_thread(self._cluster_sync, signal_list)

    def _cluster_sync(self, signals: list[ParsedSignal]) -> list[DealCluster]:
        """Synchronous clustering — runs in thread pool."""
        # ── Layer 1: pandas time-window grouping ──────────────────────────
        pandas_clusters = self._pandas_window_cluster(signals)

        # ── Layer 2: NetworkX vendor co-occurrence refinement ─────────────
        refined_clusters = self._networkx_refine(pandas_clusters, signals)

        # Sort by confidence descending
        refined_clusters.sort(key=lambda c: c.confidence, reverse=True)

        logger.info(
            "[STAGE4] %d raw clusters → %d final clusters",
            len(pandas_clusters),
            len(refined_clusters),
        )
        return refined_clusters

    def _pandas_window_cluster(
        self, signals: list[ParsedSignal]
    ) -> list[list[ParsedSignal]]:
        """
        Group signals by target_domain in a rolling time window.
        Returns candidate cluster groups (each is a list of ParsedSignals).
        """
        # Build DataFrame
        rows = [
            {
                "signal_id": s.signal_id,
                "target_domain": s.target_domain,
                "timestamp": s.timestamp,
                "confidence": s.confidence,
                "source": s.source.value,
                "_signal": s,  # reference for later
            }
            for s in signals
        ]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp")

        candidate_clusters: list[list[ParsedSignal]] = []

        # Group by company
        for domain, group in df.groupby("target_domain"):
            group = group.set_index("timestamp").sort_index()

            if len(group) < MIN_SIGNALS_FOR_CLUSTER:
                # Single-signal domain: still emit as LOW tier for tracking
                candidate_clusters.append([row["_signal"] for _, row in group.iterrows()])
                continue

            # Rolling window: any signal within `window_days` of the latest
            latest_ts = group.index.max()
            cutoff = latest_ts - pd.Timedelta(days=self.window_days)
            window_group = group[group.index >= cutoff]

            if len(window_group) >= MIN_SIGNALS_FOR_CLUSTER:
                candidate_clusters.append(
                    [row["_signal"] for _, row in window_group.iterrows()]
                )
            else:
                # Fallback: use all signals regardless of window
                candidate_clusters.append(
                    [row["_signal"] for _, row in group.iterrows()]
                )

        logger.debug("[STAGE4] Pandas layer: %d candidate clusters", len(candidate_clusters))
        return candidate_clusters

    def _networkx_refine(
        self,
        candidate_clusters: list[list[ParsedSignal]],
        all_signals: list[ParsedSignal],
    ) -> list[DealCluster]:
        """
        Build a bipartite graph per candidate cluster.
        Nodes: signal_ids + vendor names.
        Edges: signal → vendor (weight = signal.confidence).

        Connected components reveal which signals share vendor evidence.
        Merge overlapping components into final DealClusters.
        """
        final_clusters: list[DealCluster] = []

        for candidate in candidate_clusters:
            if not candidate:
                continue

            domain = candidate[0].target_domain

            # ── Build bipartite graph ──────────────────────────────────
            G = nx.Graph()

            for sig in candidate:
                G.add_node(sig.signal_id, node_type="signal")
                for vendor in sig.vendor_hints:
                    G.add_node(f"v:{vendor}", node_type="vendor")
                    G.add_edge(
                        sig.signal_id,
                        f"v:{vendor}",
                        weight=sig.confidence,
                    )

            # ── Find connected components ──────────────────────────────
            components = list(nx.connected_components(G))

            # Map signal IDs back to ParsedSignal objects
            sig_by_id = {s.signal_id: s for s in candidate}

            for component in components:
                # Signals in this component
                sig_ids = [n for n in component if not n.startswith("v:")]
                component_vendors = [
                    n[2:] for n in component if n.startswith("v:")
                ]
                component_signals = [sig_by_id[sid] for sid in sig_ids if sid in sig_by_id]

                if not component_signals:
                    continue

                # Add signals with no vendor hints (they're in their own isolated nodes)
                isolated_signals = [
                    s for s in candidate
                    if s.signal_id not in {n for n in G.nodes if not n.startswith("v:")}
                       or not s.vendor_hints
                ]
                # Merge isolated signals into the cluster if they're in the same domain
                all_cluster_signals = list({
                    s.signal_id: s for s in component_signals + isolated_signals
                }.values())

                if len(all_cluster_signals) < MIN_SIGNALS_FOR_CLUSTER:
                    # Still emit as a low-confidence cluster
                    pass

                # Time range
                timestamps = [s.timestamp for s in all_cluster_signals]
                window_start = min(timestamps)
                window_end = max(timestamps)
                window_days = max((window_end - window_start).days, 1)

                # Vendor dedup
                all_vendors = list(dict.fromkeys(
                    [v for s in all_cluster_signals for v in s.vendor_hints]
                    + component_vendors
                ))

                # Score
                confidence, tier = compute_cluster_confidence(
                    all_cluster_signals, window_days
                )

                # Human-readable summary
                summary = _build_summary(domain, all_cluster_signals, all_vendors, confidence)

                cluster = DealCluster(
                    target_domain=domain,
                    signal_ids=[s.signal_id for s in all_cluster_signals],
                    signal_count=len(all_cluster_signals),
                    source_diversity=len({s.source for s in all_cluster_signals}),
                    vendors=all_vendors[:10],  # top 10
                    window_start=window_start,
                    window_end=window_end,
                    confidence=confidence,
                    tier=tier,
                    summary=summary,
                    metadata={
                        "window_days": window_days,
                        "graph_component_size": len(component),
                        "sources": list({s.source.value for s in all_cluster_signals}),
                        "signal_types": list({s.signal_type.value for s in all_cluster_signals}),
                    },
                )
                final_clusters.append(cluster)

        return final_clusters


def _build_summary(
    domain: str,
    signals: list[ParsedSignal],
    vendors: list[str],
    confidence: float,
) -> str:
    """Generate a compact human-readable cluster summary."""
    sources = list({s.source.value for s in signals})
    sig_count = len(signals)
    vendor_str = ", ".join(vendors[:3]) if vendors else "unknown vendor"
    pct = int(confidence * 100)

    source_labels = {
        "ct_log": "SSL certs", "dns": "subdomain discovery",
        "wayback": "page changes", "github": "GitHub activity",
    }
    source_str = " + ".join(source_labels.get(s, s) for s in sources[:3])

    return (
        f"{domain}: {sig_count} signals via {source_str} — "
        f"likely adopting {vendor_str} ({pct}% confidence)"
    )
