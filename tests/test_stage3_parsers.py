"""
tests/test_stage3_parsers.py
-----------------------------
Unit tests for all four Stage 3 parsers and the VendorFingerprinter.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from models.signal import Signal, SignalSource, SignalType
from models.parsed_signal import ParsedSignal
from stage3_parsing.vendor_fingerprinter import VendorFingerprinter
from stage3_parsing.ct_log_parser import CTLogParser
from stage3_parsing.dns_parser import DNSParser
from stage3_parsing.wayback_parser import WaybackParser
from stage3_parsing.parser_orchestrator import ParserOrchestrator


def _ts() -> datetime:
    return datetime.now(timezone.utc)


def _ct_signal(domains: list[str], **kwargs) -> Signal:
    return Signal(
        source=SignalSource.CT_LOG,
        target_domain="acme.com",
        signal_type=SignalType.NEW_CERT,
        timestamp=_ts(),
        raw_data={
            "all_domains": domains,
            "cert_domain": domains[0] if domains else "",
            "issuer_name": "Let's Encrypt",
            "not_before": "2024-01-01T00:00:00",
            "not_after": "2024-04-01T00:00:00",
            **kwargs,
        },
        confidence=0.65,
    )


def _dns_signal(fqdn: str, provider: str = "crt_sh") -> Signal:
    return Signal(
        source=SignalSource.DNS,
        target_domain="acme.com",
        signal_type=SignalType.NEW_SUBDOMAIN,
        timestamp=_ts(),
        raw_data={"fqdn": fqdn, "source": provider},
        confidence=0.50,
    )


def _wayback_signal(url: str, is_new: bool = True) -> Signal:
    return Signal(
        source=SignalSource.WAYBACK,
        target_domain="acme.com",
        signal_type=SignalType.NEW_INTEGRATION_PAGE if is_new else SignalType.PAGE_CONTENT_CHANGE,
        timestamp=_ts(),
        raw_data={
            "url": url,
            "is_new_url": is_new,
            "wayback_url": f"https://web.archive.org/web/20240101/{url}",
            "content_snippet": "We now support Stripe payments and Salesforce CRM.",
        },
        confidence=0.72,
    )


# ── VendorFingerprinter ───────────────────────────────────────────────────────

class TestVendorFingerprinter:
    def setup_method(self):
        self.fp = VendorFingerprinter()

    def test_exact_prefix_match(self):
        result = self.fp.match_subdomain("salesforce.acme.com")
        assert result is not None
        assert result.vendor == "Salesforce"
        assert result.confidence >= 0.90

    def test_stripe_prefix(self):
        result = self.fp.match_subdomain("stripe.acme.com")
        assert result is not None
        assert result.vendor == "Stripe"

    def test_no_match(self):
        result = self.fp.match_subdomain("www.acme.com")
        assert result is None

    def test_contains_match(self):
        result = self.fp.match_subdomain("my-okta-portal.acme.com")
        assert result is not None
        assert "Okta" in result.vendor

    def test_path_match(self):
        result = self.fp.match_path("https://acme.com/integrations/stripe")
        assert result is not None
        assert result.vendor == "Stripe"

    def test_runtime_registration(self):
        self.fp.register_pattern("gong", "Gong.io", "sales_intel", confidence=0.88)
        result = self.fp.match_subdomain("gong.acme.com")
        assert result is not None
        assert result.vendor == "Gong.io"

    def test_text_match(self):
        matches = self.fp.match_text("We integrated with Salesforce and Stripe this quarter.")
        vendors = [m.vendor for m in matches]
        assert "Salesforce" in vendors
        assert "Stripe" in vendors


# ── CT Log Parser ─────────────────────────────────────────────────────────────

class TestCTLogParser:
    def setup_method(self):
        self.parser = CTLogParser()

    def test_parses_san_domains(self):
        signal = _ct_signal(["acme.com", "crm.acme.com", "www.acme.com"])
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert isinstance(result, ParsedSignal)
        assert "crm.acme.com" in result.entities["all_domains"]

    def test_detects_lets_encrypt(self):
        signal = _ct_signal(["acme.com"], issuer_name="Let's Encrypt Authority X3")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert result.entities["is_lets_encrypt"] is True

    def test_vendor_hint_from_san(self):
        signal = _ct_signal(["salesforce.acme.com", "acme.com"])
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert "Salesforce" in result.vendor_hints

    def test_wildcard_boosts_confidence(self):
        wildcard_signal = _ct_signal(["*.acme.com", "acme.com"])
        wildcard_signal.raw_data["all_domains"] = ["*.acme.com"]
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(wildcard_signal))
        assert result.confidence >= 0.75

    def test_skips_non_ct_signal(self):
        dns_sig = _dns_signal("crm.acme.com")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(dns_sig))
        assert result is None


# ── DNS Parser ────────────────────────────────────────────────────────────────

class TestDNSParser:
    def setup_method(self):
        self.parser = DNSParser()

    def test_parses_fqdn(self):
        signal = _dns_signal("crm.acme.com")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert result is not None
        assert result.entities["fqdn"] == "crm.acme.com"

    def test_vendor_hint_from_prefix(self):
        signal = _dns_signal("salesforce.acme.com")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert "Salesforce" in result.vendor_hints

    def test_invalid_fqdn_low_confidence(self):
        signal = _dns_signal("not_a_valid_fqdn!!!")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert result.confidence <= 0.20

    def test_deep_subdomain_penalty(self):
        # a.b.crm.acme.com → depth 3, should have lower confidence
        signal = _dns_signal("a.b.crm.acme.com")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        shallow = asyncio.get_event_loop().run_until_complete(
            self.parser.parse(_dns_signal("crm.acme.com"))
        )
        assert result.confidence < shallow.confidence


# ── Wayback Parser ────────────────────────────────────────────────────────────

class TestWaybackParser:
    def setup_method(self):
        self.parser = WaybackParser()

    def test_classifies_integration_path(self):
        signal = _wayback_signal("https://acme.com/integrations/stripe")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert result.entities["path_type"] == "integration"

    def test_vendor_from_path(self):
        signal = _wayback_signal("https://acme.com/integrations/salesforce")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        assert "Salesforce" in result.vendor_hints

    def test_new_url_boosts_confidence(self):
        new_sig = _wayback_signal("https://acme.com/integrations/stripe", is_new=True)
        change_sig = _wayback_signal("https://acme.com/pricing", is_new=False)
        new_r = asyncio.get_event_loop().run_until_complete(self.parser.parse(new_sig))
        old_r = asyncio.get_event_loop().run_until_complete(self.parser.parse(change_sig))
        assert new_r.confidence > old_r.confidence

    def test_content_words_extracted(self):
        signal = _wayback_signal("https://acme.com/integrations/stripe")
        result = asyncio.get_event_loop().run_until_complete(self.parser.parse(signal))
        words = result.entities.get("content_words", [])
        assert len(words) > 0  # "stripe", "payments", "salesforce", "crm"


# ── Parser Orchestrator ───────────────────────────────────────────────────────

class TestParserOrchestrator:
    def setup_method(self):
        self.orch = ParserOrchestrator()

    def test_routes_all_source_types(self):
        signals = [
            _ct_signal(["crm.acme.com"]),
            _dns_signal("stripe.acme.com"),
            _wayback_signal("https://acme.com/integrations/slack"),
        ]
        results = asyncio.get_event_loop().run_until_complete(self.orch.parse(signals))
        assert len(results) == 3

    def test_empty_input(self):
        results = asyncio.get_event_loop().run_until_complete(self.orch.parse([]))
        assert results == []

    def test_all_results_are_parsed_signals(self):
        signals = [_ct_signal(["acme.com"]), _dns_signal("okta.acme.com")]
        results = asyncio.get_event_loop().run_until_complete(self.orch.parse(signals))
        assert all(isinstance(r, ParsedSignal) for r in results)
