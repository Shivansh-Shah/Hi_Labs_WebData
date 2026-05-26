"""
pipeline/runner.py
------------------
Asynchronous pipeline runner for Stages 1 + 2, with Stage 3 + 4 integration.

Run modes:
  • ONCE    — run all collectors once, return raw Signal list
  • FULL    — run_once() → Stage 3 (parse) → Stage 4 (correlate)
  • STREAM  — long-lived certstream + periodic batch
  • AGENT   — MCP Gemini agent as primary collection driver

Usage
-----
    runner = PipelineRunner(target_domains=["acme.com"], github_orgs=["salesforce"])

    # Raw signals only
    signals = await runner.run_once()

    # Full pipeline: signals → parsed → clusters
    signals, clusters = await runner.run_full_pipeline()
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Iterable

from models.signal import Signal
from stage1_signal_collection import (
    CTLogPollingCollector,
    CTLogStreamingCollector,
    DNSCollector,
    WaybackCollector,
    GitHubOrgCollector,
)
from stage2_bright_data import MCPAgentOrchestrator

logger = logging.getLogger(__name__)


# ── Deduplication ─────────────────────────────────────────────────────────────


class SignalDeduplicator:
    """
    Simple bloom-filter-style deduplicator using a set of fingerprints.
    Fingerprint = (source, target_domain, signal_type, date).
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_duplicate(self, signal: Signal) -> bool:
        fp = (
            f"{signal.source.value}|{signal.target_domain}|"
            f"{signal.signal_type.value}|{signal.timestamp.date().isoformat()}"
        )
        if fp in self._seen:
            return True
        self._seen.add(fp)
        return False

    def filter(self, signals: list[Signal]) -> list[Signal]:
        return [s for s in signals if not self.is_duplicate(s)]


# ── Main runner ───────────────────────────────────────────────────────────────


class PipelineRunner:
    """
    Coordinates Stage 1 + 2 collection with deduplication.
    Optionally chains Stage 3 + 4 via run_full_pipeline().

    Parameters
    ----------
    target_domains      : apex domains to monitor (e.g. ["acme.com"])
    github_orgs         : GitHub org handles to watch (e.g. ["salesforce"])
    customer_names      : company names to search for in GitHub commits
    on_signal           : optional callback fired for each new signal
    enable_ct_streaming : whether to run certstream (long-running process)
    enable_agent        : whether to run the MCP Gemini agent
    """

    def __init__(
        self,
        target_domains: Iterable[str],
        github_orgs: Iterable[str] | None = None,
        customer_names: list[str] | None = None,
        on_signal: Callable[[Signal], None] | None = None,
        enable_ct_streaming: bool = False,
        enable_agent: bool = False,
    ) -> None:
        self.target_domains = list(target_domains)
        self.github_orgs = list(github_orgs or [])
        self.customer_names = customer_names or []
        self.on_signal = on_signal
        self.enable_ct_streaming = enable_ct_streaming
        self.enable_agent = enable_agent
        self._dedup = SignalDeduplicator()
        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue()

    def _dispatch(self, signal: Signal) -> None:
        """Called for each new signal — dedup + route."""
        if not self._dedup.is_duplicate(signal):
            self._signal_queue.put_nowait(signal)
            if self.on_signal:
                self.on_signal(signal)

    async def run_once(self) -> list[Signal]:
        """
        Run all batch collectors once and return the deduplicated signal list.
        Best for cron/scheduled jobs. Feeds directly into Stage 3 via run_full_pipeline().
        """
        logger.info("[PIPELINE] run_once() starting for domains: %s", self.target_domains)
        start = datetime.now(timezone.utc)

        tasks = [
            CTLogPollingCollector(target_domains=self.target_domains).collect_all(),
            DNSCollector(target_domains=self.target_domains).collect_all(),
            WaybackCollector(target_domains=self.target_domains, diff_content=False).collect_all(),
        ]

        if self.github_orgs:
            tasks.append(
                GitHubOrgCollector(
                    org_names=self.github_orgs,
                    customer_names=self.customer_names,
                ).collect_all()
            )

        if self.enable_agent:
            try:
                agent = MCPAgentOrchestrator()
                for domain in self.target_domains:
                    tasks.append(agent.investigate_domain(domain))
            except ValueError as exc:
                logger.warning("[PIPELINE] Agent disabled (no Gemini key?): %s", exc)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_signals: list[Signal] = []
        for result in results:
            if isinstance(result, list):
                for s in result:
                    self._dispatch(s)
                    all_signals.append(s)
            else:
                logger.error("[PIPELINE] Collector error: %s", result)

        deduped: list[Signal] = []
        while not self._signal_queue.empty():
            deduped.append(self._signal_queue.get_nowait())

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info(
            "[PIPELINE] run_once() complete: %d raw → %d deduped in %.1fs",
            len(all_signals), len(deduped), elapsed,
        )
        return deduped

    async def run_full_pipeline(self, window_days: int = 30, enrich: bool = True):
        """
        Full Stages 1 → 2 → 3 → 4 → 5 pipeline.

        Returns
        -------
        tuple[list[Signal], list[DealCluster], list[DealIntelligenceObject]]

        Usage
        -----
            signals, clusters, intel = await runner.run_full_pipeline()
            for obj in intel:
                print(obj.target_company, obj.suspected_vendor, obj.outreach_window)
        """
        from stage4_correlation.correlation_engine import CorrelationEngine, cluster_report
        from stage5_ai_enrichment import AIEnricher, intelligence_report
        from models.signal import ConfidenceTier

        signals = await self.run_once()
        if not signals:
            logger.warning("[PIPELINE] No signals — skipping Stage 3/4/5")
            return signals, [], []

        engine = CorrelationEngine(window_days=window_days)
        clusters = await engine.correlate_raw(signals)
        logger.info("\n%s", cluster_report(clusters))

        if not enrich or not clusters:
            return signals, clusters, []

        try:
            enricher = AIEnricher(min_tier=ConfidenceTier.LOW)
            intel_objects = await enricher.enrich_clusters(clusters)
            logger.info("\n%s", intelligence_report(intel_objects))
            return signals, clusters, intel_objects
        except ValueError as exc:
            logger.warning("[PIPELINE] Stage 5 skipped: %s", exc)
            return signals, clusters, []

    async def run_stream(self) -> None:
        """Long-running mode: certstream + periodic batch."""
        logger.info("[PIPELINE] Starting streaming mode")
        tasks = []
        if self.enable_ct_streaming:
            ct_collector = CTLogStreamingCollector(
                target_domains=self.target_domains,
                on_signal=self._dispatch,
            )
            tasks.append(asyncio.create_task(ct_collector.run()))
        tasks.append(asyncio.create_task(self._periodic_batch_runner()))
        await asyncio.gather(*tasks)

    async def _periodic_batch_runner(self, interval_seconds: int = 300) -> None:
        """Run batch collectors on a schedule."""
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("[PIPELINE] Periodic batch error: %s", exc)
            await asyncio.sleep(interval_seconds)
