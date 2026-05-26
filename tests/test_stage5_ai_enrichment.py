"""
tests/test_stage5_ai_enrichment.py
------------------------------------
Unit tests for Stage 5 AI Enrichment.

Mocks the OpenAI API — no real API calls in unit tests.
Live test at the end uses the real API key (skipped if not configured).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.signal import Signal, SignalSource, SignalType, ConfidenceTier
from models.parsed_signal import ParsedSignal
from models.deal_cluster import DealCluster
from models.deal_intelligence import DealIntelligenceObject, VendorPatternSuggestion
from stage5_ai_enrichment.ai_enricher import (
    AIEnricher, _build_user_prompt, _parse_llm_response, intelligence_report,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _ts(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _make_cluster(
    domain: str = "acme.com",
    vendors: list[str] | None = None,
    confidence: float = 0.82,
    tier: ConfidenceTier = ConfidenceTier.HIGH,
    sources: list[str] | None = None,
) -> DealCluster:
    return DealCluster(
        target_domain=domain,
        signal_ids=["sig-1", "sig-2", "sig-3"],
        signal_count=3,
        source_diversity=2,
        vendors=vendors or ["Salesforce"],
        window_start=_ts(21),
        window_end=_ts(1),
        confidence=confidence,
        tier=tier,
        summary=f"{domain}: 3 signals via SSL certs + subdomain discovery — likely adopting Salesforce (82% confidence)",
        metadata={
            "window_days": 20,
            "sources": sources or ["ct_log", "dns"],
            "signal_types": ["new_cert", "new_subdomain"],
        },
    )


def _mock_openai_response(vendor: str = "Salesforce") -> dict:
    return {
        "suspected_vendor": vendor,
        "vendor_category": "crm",
        "confidence": 0.88,
        "deal_closed_estimate": "April 2026",
        "outreach_window": "Oct–Dec 2026",
        "reasoning": f"Multiple cert and DNS signals pointing to {vendor} adoption at acme.com. The cert for crm.acme.com appeared April 14 with a commercial issuer, corroborated by a new subdomain. Timing suggests a Q1 deal; renewal window starts Q4.",
        "signal_summary": [
            "New cert for crm.acme.com issued by DigiCert (April 2026)",
            "New subdomain salesforce.acme.com detected via DNS",
        ],
        "new_vendor_patterns": [
            {"pattern": "gong", "vendor": "Gong.io", "category": "sales_intel", "confidence": 0.85}
        ],
    }


def _make_mock_completion(vendor: str = "Salesforce") -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 450
    usage.completion_tokens = 180

    choice = MagicMock()
    choice.message.content = json.dumps(_mock_openai_response(vendor))

    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = usage
    return completion


# ── Prompt builder ────────────────────────────────────────────────────────────


class TestBuildUserPrompt:
    def test_contains_domain(self):
        cluster = _make_cluster("acme.com")
        prompt = _build_user_prompt(cluster)
        assert "acme.com" in prompt

    def test_contains_vendor_hints(self):
        cluster = _make_cluster(vendors=["Salesforce", "Okta"])
        prompt = _build_user_prompt(cluster)
        assert "Salesforce" in prompt
        assert "Okta" in prompt

    def test_contains_confidence(self):
        cluster = _make_cluster(confidence=0.82)
        prompt = _build_user_prompt(cluster)
        assert "82%" in prompt

    def test_contains_sources(self):
        cluster = _make_cluster(sources=["ct_log", "dns", "wayback"])
        prompt = _build_user_prompt(cluster)
        assert "ct_log" in prompt

    def test_contains_schema_keys(self):
        cluster = _make_cluster()
        prompt = _build_user_prompt(cluster)
        assert "suspected_vendor" in prompt
        assert "outreach_window" in prompt
        assert "deal_closed_estimate" in prompt


# ── Response parser ───────────────────────────────────────────────────────────


class TestParseLLMResponse:
    def test_parses_valid_json(self):
        cluster = _make_cluster()
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        resp = json.dumps(_mock_openai_response())
        obj = _parse_llm_response(resp, cluster, "gpt-4o-mini", usage)
        assert isinstance(obj, DealIntelligenceObject)
        assert obj.suspected_vendor == "Salesforce"
        assert obj.vendor_category == "crm"
        assert obj.deal_closed_estimate == "April 2026"
        assert obj.outreach_window == "Oct–Dec 2026"

    def test_blends_confidence(self):
        cluster = _make_cluster(confidence=0.70)
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        llm_json = json.dumps({**_mock_openai_response(), "confidence": 0.90})
        obj = _parse_llm_response(llm_json, cluster, "gpt-4o-mini", usage)
        # Blended = 0.6*0.90 + 0.4*0.70 = 0.54 + 0.28 = 0.82
        assert 0.79 <= obj.confidence <= 0.85

    def test_high_tier_assigned(self):
        cluster = _make_cluster(confidence=0.80)
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        llm_json = json.dumps({**_mock_openai_response(), "confidence": 0.92})
        obj = _parse_llm_response(llm_json, cluster, "gpt-4o-mini", usage)
        assert obj.tier == ConfidenceTier.HIGH

    def test_new_vendor_patterns_parsed(self):
        cluster = _make_cluster()
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        resp = json.dumps(_mock_openai_response())
        obj = _parse_llm_response(resp, cluster, "gpt-4o-mini", usage)
        assert len(obj.new_vendor_patterns) == 1
        assert obj.new_vendor_patterns[0].pattern == "gong"
        assert obj.new_vendor_patterns[0].vendor == "Gong.io"

    def test_cost_computed(self):
        cluster = _make_cluster()
        usage = MagicMock(prompt_tokens=1_000_000, completion_tokens=0)
        resp = json.dumps(_mock_openai_response())
        obj = _parse_llm_response(resp, cluster, "gpt-4o-mini", usage)
        # 1M input tokens at $0.15/1M = $0.15
        assert abs(obj.estimated_cost_usd - 0.15) < 0.001

    def test_malformed_json_fallback(self):
        cluster = _make_cluster()
        usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        obj = _parse_llm_response("this is not json at all", cluster, "gpt-4o-mini", usage)
        # Should not crash — falls back to cluster data
        assert obj.cluster_id == cluster.cluster_id
        assert obj.target_company == "acme.com"

    def test_confidence_clamped_to_range(self):
        cluster = _make_cluster(confidence=0.50)
        usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        llm_json = json.dumps({**_mock_openai_response(), "confidence": 9999.0})
        obj = _parse_llm_response(llm_json, cluster, "gpt-4o-mini", usage)
        assert 0.0 <= obj.confidence <= 1.0


# ── AIEnricher (mocked) ───────────────────────────────────────────────────────


class TestAIEnricher:
    def _make_enricher(self) -> AIEnricher:
        with patch("stage5_ai_enrichment.ai_enricher.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = "sk-test-key"
            mock_settings.OPENAI_MODEL = "gpt-4o-mini"
            mock_settings.OPENAI_MAX_TOKENS = 600
            enricher = AIEnricher.__new__(AIEnricher)
            enricher._client = AsyncMock()
            enricher._model = "gpt-4o-mini"
            enricher._max_tokens = 600
            enricher._concurrency = 5
            enricher._min_tier = ConfidenceTier.LOW
            enricher._fingerprinter = None
            enricher._total_input_tokens = 0
            enricher._total_output_tokens = 0
            enricher._total_cost = 0.0
        return enricher

    def test_raises_without_api_key(self):
        with patch("stage5_ai_enrichment.ai_enricher.settings") as mock_settings:
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.OPENAI_MODEL = "gpt-4o-mini"
            mock_settings.OPENAI_MAX_TOKENS = 600
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                AIEnricher()

    def test_enrich_cluster_returns_intel_object(self):
        enricher = self._make_enricher()
        enricher._client.chat = MagicMock()
        enricher._client.chat.completions = MagicMock()
        enricher._client.chat.completions.create = AsyncMock(
            return_value=_make_mock_completion("Salesforce")
        )
        cluster = _make_cluster("acme.com")
        result = asyncio.run(enricher.enrich_cluster(cluster))
        assert isinstance(result, DealIntelligenceObject)
        assert result.suspected_vendor == "Salesforce"
        assert result.target_company == "acme.com"
        assert result.deal_closed_estimate == "April 2026"

    def test_enrich_clusters_skips_below_min_tier(self):
        enricher = self._make_enricher()
        enricher._min_tier = ConfidenceTier.MEDIUM
        enricher._client.chat = MagicMock()
        enricher._client.chat.completions = MagicMock()
        enricher._client.chat.completions.create = AsyncMock(
            return_value=_make_mock_completion()
        )
        clusters = [
            _make_cluster("high.com", tier=ConfidenceTier.HIGH, confidence=0.90),
            _make_cluster("low.com", tier=ConfidenceTier.LOW, confidence=0.30),
        ]
        results = asyncio.run(enricher.enrich_clusters(clusters))
        # LOW cluster should be skipped
        domains = {r.target_company for r in results}
        assert "high.com" in domains
        assert "low.com" not in domains

    def test_enrich_clusters_concurrent(self):
        enricher = self._make_enricher()
        enricher._client.chat = MagicMock()
        enricher._client.chat.completions = MagicMock()
        enricher._client.chat.completions.create = AsyncMock(
            return_value=_make_mock_completion()
        )
        clusters = [_make_cluster(f"company{i}.com") for i in range(5)]
        results = asyncio.run(enricher.enrich_clusters(clusters))
        assert len(results) == 5

    def test_registers_new_patterns_to_fingerprinter(self):
        from stage3_parsing.vendor_fingerprinter import VendorFingerprinter
        fp = VendorFingerprinter()
        enricher = self._make_enricher()
        enricher._fingerprinter = fp
        enricher._client.chat = MagicMock()
        enricher._client.chat.completions = MagicMock()
        enricher._client.chat.completions.create = AsyncMock(
            return_value=_make_mock_completion("Salesforce")
        )
        cluster = _make_cluster()
        asyncio.run(enricher.enrich_cluster(cluster))
        # "gong" pattern should now be in the fingerprinter
        match = fp.match_subdomain("gong.acme.com")
        assert match is not None
        assert match.vendor == "Gong.io"

    def test_api_error_returns_fallback(self):
        enricher = self._make_enricher()
        enricher._client.chat = MagicMock()
        enricher._client.chat.completions = MagicMock()
        enricher._client.chat.completions.create = AsyncMock(
            side_effect=Exception("API timeout")
        )
        cluster = _make_cluster("fallback.com")
        result = asyncio.run(enricher.enrich_cluster(cluster))
        # Should not raise — fallback DIO returned
        assert isinstance(result, DealIntelligenceObject)
        assert result.target_company == "fallback.com"
        assert "API timeout" in result.reasoning


# ── intelligence_report formatter ─────────────────────────────────────────────


class TestIntelligenceReport:
    def test_empty_returns_message(self):
        report = intelligence_report([])
        assert "No deal intelligence" in report

    def test_contains_vendor_and_company(self):
        cluster = _make_cluster("acme.com", vendors=["Stripe"])
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        llm_json = json.dumps({**_mock_openai_response("Stripe"), "suspected_vendor": "Stripe"})
        obj = _parse_llm_response(llm_json, cluster, "gpt-4o-mini", usage)
        report = intelligence_report([obj])
        assert "acme.com" in report
        assert "Stripe" in report

    def test_contains_outreach_window(self):
        cluster = _make_cluster()
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        obj = _parse_llm_response(json.dumps(_mock_openai_response()), cluster, "gpt-4o-mini", usage)
        report = intelligence_report([obj])
        assert "Oct" in report or "2026" in report

    def test_contains_cost(self):
        cluster = _make_cluster()
        usage = MagicMock(prompt_tokens=400, completion_tokens=150)
        obj = _parse_llm_response(json.dumps(_mock_openai_response()), cluster, "gpt-4o-mini", usage)
        report = intelligence_report([obj])
        assert "$" in report


# ── Live API test (skipped if no key) ─────────────────────────────────────────


@pytest.mark.integration
def test_live_enrich_single_cluster():
    """
    Calls the real OpenAI API with a realistic cluster.
    Requires OPENAI_API_KEY in environment. Costs ~$0.0001.
    """
    from config.settings import get_settings
    cfg = get_settings()
    if not cfg.OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY not configured")

    cluster = DealCluster(
        target_domain="acme.com",
        signal_ids=["s1", "s2", "s3", "s4"],
        signal_count=4,
        source_diversity=3,
        vendors=["Salesforce", "Okta"],
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 4, 22, tzinfo=timezone.utc),
        confidence=0.84,
        tier=ConfidenceTier.HIGH,
        summary="acme.com: 4 signals via SSL certs + subdomain discovery + page changes — likely adopting Salesforce, Okta (84% confidence)",
        metadata={
            "window_days": 21,
            "sources": ["ct_log", "dns", "wayback"],
            "signal_types": ["new_cert", "new_subdomain", "new_integration_page"],
        },
    )

    enricher = AIEnricher()
    result = asyncio.run(enricher.enrich_cluster(cluster))

    assert isinstance(result, DealIntelligenceObject)
    assert result.suspected_vendor != ""
    assert result.deal_closed_estimate != ""
    assert result.outreach_window != ""
    assert 0.0 < result.confidence <= 1.0
    assert result.input_tokens > 0
    assert result.estimated_cost_usd > 0
    assert result.estimated_cost_usd < 0.01  # sanity check — should be tiny

    print(f"\n✅ Live enrichment result:")
    print(f"   Vendor   : {result.suspected_vendor} ({result.vendor_category})")
    print(f"   Confidence: {result.confidence:.0%} ({result.tier.value})")
    print(f"   Deal close: {result.deal_closed_estimate}")
    print(f"   Outreach  : {result.outreach_window}")
    print(f"   Reasoning : {result.reasoning[:120]}")
    print(f"   Tokens    : {result.input_tokens} in + {result.output_tokens} out")
    print(f"   Cost      : ${result.estimated_cost_usd:.6f}")
    if result.new_vendor_patterns:
        print(f"   New patterns: {[(p.pattern, p.vendor) for p in result.new_vendor_patterns]}")
