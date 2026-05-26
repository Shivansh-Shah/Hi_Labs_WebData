"""
tests/test_stage4_correlation.py
----------------------------------
Unit tests for Stage 4: confidence scorer + signal clusterer.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from models.signal import Signal, SignalSource, SignalType, ConfidenceTier
from models.parsed_signal import ParsedSignal
from models.deal_cluster import DealCluster
from stage4_correlation.confidence_scorer import compute_cluster_confidence
from stage4_correlation.signal_clusterer import SignalClusterer
from stage4_correlation.correlation_engine import CorrelationEngine, cluster_report


def _ts(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _parsed(
    domain: str,
    source: SignalSource,
    sig_type: SignalType,
    vendor_hints: list[str],
    confidence: float,
    days_ago: int = 0,
) -> ParsedSignal:
    raw_signal = Signal(
        source=source,
        target_domain=domain,
        signal_type=sig_type,
        timestamp=_ts(days_ago),
        raw_data={},
        confidence=confidence,
    )
    return ParsedSignal(
        signal=raw_signal,
        entities={},
        vendor_hints=vendor_hints,
        confidence=confidence,
    )


# ── Confidence Scorer ─────────────────────────────────────────────────────────

class TestConfidenceScorer:
    def test_single_signal_low_tier(self):
        sigs = [_parsed("a.com", SignalSource.CT_LOG, SignalType.NEW_CERT, [], 0.60)]
        score, tier = compute_cluster_confidence(sigs, window_days=5)
        # Single source penalty applies
        assert tier == ConfidenceTier.LOW

    def test_multi_source_boosts_score(self):
        sigs = [
            _parsed("a.com", SignalSource.CT_LOG, SignalType.NEW_CERT, ["Salesforce"], 0.75),
            _parsed("a.com", SignalSource.DNS,    SignalType.NEW_SUBDOMAIN, ["Salesforce"], 0.70),
        ]
        score, tier = compute_cluster_confidence(sigs, window_days=7)
        assert score > 0.75  # source diversity + vendor + CT+DNS pairing bonus

    def test_ct_plus_dns_pairing_bonus(self):
        ct_only = [_parsed("a.com", SignalSource.CT_LOG, SignalType.NEW_CERT, [], 0.70)]
        ct_dns = ct_only + [
            _parsed("a.com", SignalSource.DNS, SignalType.NEW_SUBDOMAIN, [], 0.65)
        ]
        score_ct, _ = compute_cluster_confidence(ct_only, 5)
        score_both, _ = compute_cluster_confidence(ct_dns, 5)
        assert score_both > score_ct

    def test_high_tier_requires_multi_source_and_vendor(self):
        sigs = [
            _parsed("a.com", SignalSource.CT_LOG,    SignalType.NEW_CERT,       ["Salesforce"], 0.85),
            _parsed("a.com", SignalSource.DNS,        SignalType.NEW_SUBDOMAIN, ["Salesforce"], 0.80),
            _parsed("a.com", SignalSource.WAYBACK,    SignalType.NEW_INTEGRATION_PAGE, ["Salesforce"], 0.78),
        ]
        score, tier = compute_cluster_confidence(sigs, window_days=10)
        assert tier == ConfidenceTier.HIGH
        assert score >= 0.80

    def test_wide_window_penalty(self):
        # Use single-source signals to avoid bonus saturation
        sigs = [
            _parsed("a.com", SignalSource.CT_LOG, SignalType.NEW_CERT, [], 0.52),
            _parsed("a.com", SignalSource.CT_LOG, SignalType.NEW_CERT, [], 0.50),
        ]
        score_narrow, _ = compute_cluster_confidence(sigs, window_days=5)
        score_wide, _   = compute_cluster_confidence(sigs, window_days=28)
        # Wide window has -0.05 spread penalty on top of single-source -0.10
        assert score_narrow >= score_wide

    def test_empty_signals_returns_zero(self):
        score, tier = compute_cluster_confidence([], 0)
        assert score == 0.0
        assert tier == ConfidenceTier.LOW


# ── Signal Clusterer ──────────────────────────────────────────────────────────

class TestSignalClusterer:
    def setup_method(self):
        self.clusterer = SignalClusterer(window_days=30)

    def _run(self, sigs):
        return asyncio.get_event_loop().run_until_complete(self.clusterer.cluster(sigs))

    def test_empty_returns_empty(self):
        assert self._run([]) == []

    def test_single_domain_creates_one_cluster(self):
        sigs = [
            _parsed("acme.com", SignalSource.CT_LOG, SignalType.NEW_CERT, ["Stripe"], 0.75),
            _parsed("acme.com", SignalSource.DNS,    SignalType.NEW_SUBDOMAIN, ["Stripe"], 0.70),
        ]
        clusters = self._run(sigs)
        assert len(clusters) >= 1
        assert clusters[0].target_domain == "acme.com"

    def test_separate_domains_separate_clusters(self):
        sigs = [
            _parsed("acme.com",   SignalSource.CT_LOG, SignalType.NEW_CERT, ["Stripe"], 0.75),
            _parsed("globex.com", SignalSource.CT_LOG, SignalType.NEW_CERT, ["Okta"], 0.72),
        ]
        clusters = self._run(sigs)
        domains = {c.target_domain for c in clusters}
        assert "acme.com" in domains
        assert "globex.com" in domains

    def test_cluster_has_correct_source_diversity(self):
        sigs = [
            _parsed("acme.com", SignalSource.CT_LOG,  SignalType.NEW_CERT,       ["Stripe"], 0.75),
            _parsed("acme.com", SignalSource.DNS,      SignalType.NEW_SUBDOMAIN, ["Stripe"], 0.70),
            _parsed("acme.com", SignalSource.WAYBACK,  SignalType.NEW_INTEGRATION_PAGE, ["Stripe"], 0.78),
        ]
        clusters = self._run(sigs)
        # Find the acme.com cluster
        acme = next((c for c in clusters if c.target_domain == "acme.com"), None)
        assert acme is not None
        assert acme.source_diversity >= 2

    def test_vendors_are_collected(self):
        sigs = [
            _parsed("acme.com", SignalSource.CT_LOG, SignalType.NEW_CERT, ["Salesforce"], 0.80),
            _parsed("acme.com", SignalSource.DNS,    SignalType.NEW_SUBDOMAIN, ["Stripe"], 0.75),
        ]
        clusters = self._run(sigs)
        acme = next((c for c in clusters if c.target_domain == "acme.com"), None)
        assert acme is not None
        assert "Salesforce" in acme.vendors or "Stripe" in acme.vendors

    def test_sorted_by_confidence_descending(self):
        sigs = [
            # High confidence cluster
            _parsed("acme.com",   SignalSource.CT_LOG,  SignalType.NEW_CERT,       ["Salesforce"], 0.90),
            _parsed("acme.com",   SignalSource.DNS,      SignalType.NEW_SUBDOMAIN, ["Salesforce"], 0.85),
            _parsed("acme.com",   SignalSource.WAYBACK,  SignalType.NEW_INTEGRATION_PAGE, [], 0.80),
            # Low confidence cluster
            _parsed("globex.com", SignalSource.CT_LOG,  SignalType.NEW_CERT,       [], 0.45),
        ]
        clusters = self._run(sigs)
        if len(clusters) >= 2:
            assert clusters[0].confidence >= clusters[1].confidence


# ── Correlation Engine ────────────────────────────────────────────────────────

class TestCorrelationEngine:
    def setup_method(self):
        self.engine = CorrelationEngine(window_days=30)

    def _run_correlate(self, sigs):
        return asyncio.get_event_loop().run_until_complete(self.engine.correlate_signals(sigs))

    def test_filter_by_tier(self):
        sigs = [
            _parsed("acme.com",   SignalSource.CT_LOG, SignalType.NEW_CERT, ["Salesforce"], 0.90),
            _parsed("acme.com",   SignalSource.DNS,    SignalType.NEW_SUBDOMAIN, ["Salesforce"], 0.85),
            _parsed("acme.com",   SignalSource.WAYBACK, SignalType.NEW_INTEGRATION_PAGE, [], 0.80),
            _parsed("globex.com", SignalSource.CT_LOG, SignalType.NEW_CERT, [], 0.35),
        ]
        clusters = self._run_correlate(sigs)
        medium_plus = self.engine.filter_by_tier(clusters, ConfidenceTier.MEDIUM)
        # globex with single low-confidence signal should be filtered out
        domains = {c.target_domain for c in medium_plus}
        # acme should be present (high confidence)
        assert "acme.com" in domains


# ── Cluster report ────────────────────────────────────────────────────────────

class TestClusterReport:
    def test_empty_clusters(self):
        report = cluster_report([])
        assert "No deal clusters" in report

    def test_report_contains_domain(self):
        sigs = [
            _parsed("acme.com", SignalSource.CT_LOG, SignalType.NEW_CERT, ["Stripe"], 0.80),
            _parsed("acme.com", SignalSource.DNS,    SignalType.NEW_SUBDOMAIN, ["Stripe"], 0.75),
        ]
        engine = CorrelationEngine()
        clusters = asyncio.get_event_loop().run_until_complete(engine.correlate_signals(sigs))
        report = cluster_report(clusters)
        assert "acme.com" in report
        assert "Stripe" in report
