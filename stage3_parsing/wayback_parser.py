"""
stage3_parsing/wayback_parser.py
----------------------------------
Stage 3 — Wayback Machine Signal Parser.

Takes raw WAYBACK Signals and extracts:
  • URL path classification (integrations / docs / status / changelog / pricing)
  • Snapshot delta (is this a brand-new URL or a content change?)
  • Path-level vendor hints (e.g., /integrations/stripe → Stripe)
  • Content word extraction from snapshot text (for NER in Stage 4)
  • URL structure signals (depth, slug patterns)

Uses difflib.SequenceMatcher to compare content snippets when available.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Iterable
from urllib.parse import urlparse

from models.signal import Signal, SignalSource, SignalType
from models.parsed_signal import ParsedSignal
from stage3_parsing.vendor_fingerprinter import VendorFingerprinter

logger = logging.getLogger(__name__)
_fingerprinter = VendorFingerprinter()

# Path → type mapping (ordered by specificity)
_PATH_CLASSIFIERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"/integrat",    re.I), "integration"),
    (re.compile(r"/partner",     re.I), "partner"),
    (re.compile(r"/marketplace", re.I), "marketplace"),
    (re.compile(r"/ecosystem",   re.I), "ecosystem"),
    (re.compile(r"/changelog",   re.I), "changelog"),
    (re.compile(r"/pricing",     re.I), "pricing"),
    (re.compile(r"/customer",    re.I), "customer_story"),
    (re.compile(r"/case-stud",   re.I), "case_study"),
    (re.compile(r"/docs?/",      re.I), "docs"),
    (re.compile(r"/api/",        re.I), "api_docs"),
    (re.compile(r"/status",      re.I), "status"),
    (re.compile(r"/launch",      re.I), "launch"),
    (re.compile(r"/beta",        re.I), "beta"),
]

# Vendor slug patterns in URL paths
_PATH_VENDOR_RE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"/stripe",    re.I), "Stripe"),
    (re.compile(r"/salesforc", re.I), "Salesforce"),
    (re.compile(r"/hubspot",   re.I), "HubSpot"),
    (re.compile(r"/okta",      re.I), "Okta"),
    (re.compile(r"/zendesk",   re.I), "Zendesk"),
    (re.compile(r"/intercom",  re.I), "Intercom"),
    (re.compile(r"/slack",     re.I), "Slack"),
    (re.compile(r"/datadog",   re.I), "Datadog"),
    (re.compile(r"/snowflak",  re.I), "Snowflake"),
    (re.compile(r"/databrick", re.I), "Databricks"),
    (re.compile(r"/segment",   re.I), "Segment"),
    (re.compile(r"/workday",   re.I), "Workday"),
    (re.compile(r"/marketo",   re.I), "Marketo"),
    (re.compile(r"/pagerduty", re.I), "PagerDuty"),
    (re.compile(r"/tableau",   re.I), "Tableau"),
    (re.compile(r"/looker",    re.I), "Looker"),
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "will", "would", "could", "should", "may", "can",
    "not", "no", "this", "that", "these", "those", "it", "its",
}


def _classify_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    for pattern, label in _PATH_CLASSIFIERS:
        if pattern.search(path):
            return label
    return "other"


def _path_vendors(url: str) -> list[str]:
    vendors: list[str] = []
    for pattern, vendor in _PATH_VENDOR_RE:
        if pattern.search(url):
            vendors.append(vendor)
    return vendors


def _url_depth(url: str) -> int:
    """Number of path segments in the URL."""
    parsed = urlparse(url)
    return len([p for p in parsed.path.split("/") if p])


def _extract_words(text: str, max_words: int = 200) -> list[str]:
    """Extract significant words from page content snippet."""
    words = re.findall(r"[a-zA-Z]{3,}", text)
    return [w.lower() for w in words if w.lower() not in _STOPWORDS][:max_words]


def _content_similarity(old_snippet: str, new_snippet: str) -> float:
    """Returns 0.0–1.0 similarity score using SequenceMatcher."""
    if not old_snippet or not new_snippet:
        return 0.0
    return difflib.SequenceMatcher(None, old_snippet[:2000], new_snippet[:2000]).ratio()


def _parse_one(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    url: str = raw.get("url", "")
    wayback_url: str = raw.get("wayback_url", "")
    content_snippet: str = raw.get("content_snippet", "")
    is_new: bool = raw.get("is_new_url", False) or signal.signal_type == SignalType.NEW_INTEGRATION_PAGE
    old_digest: str = raw.get("old_digest", "")
    new_digest: str = raw.get("new_digest", raw.get("digest", ""))

    path_type = _classify_path(url)
    url_depth = _url_depth(url)
    path_vendors = _path_vendors(url)
    words = _extract_words(content_snippet) if content_snippet else []

    # Vendor hints: fingerprint from path + any already on signal
    vendor_hints = list(dict.fromkeys(
        ([signal.vendor_hint] if signal.vendor_hint else []) + path_vendors
    ))

    # Confidence heuristics:
    # New URL on integration/partner/marketplace path → strong signal
    # Content change on pricing page → medium signal
    # Status page → weak (just infra noise usually)
    type_weights = {
        "integration": 0.82, "partner": 0.80, "marketplace": 0.80,
        "ecosystem": 0.78, "launch": 0.90, "beta": 0.85,
        "changelog": 0.65, "pricing": 0.60, "customer_story": 0.72,
        "case_study": 0.75, "docs": 0.45, "api_docs": 0.55,
        "status": 0.30, "other": 0.35,
    }
    base = type_weights.get(path_type, 0.35)
    if is_new:
        base = min(base + 0.10, 0.95)
    if path_vendors:
        base = min(base + 0.05 * len(path_vendors), 0.95)

    entities = {
        "url": url,
        "wayback_url": wayback_url,
        "path_type": path_type,
        "url_depth": url_depth,
        "is_new_url": is_new,
        "path_vendors": path_vendors,
        "content_words": words[:100],   # top 100 for NER
        "content_length": raw.get("content_length", len(content_snippet)),
        "old_digest": old_digest,
        "new_digest": new_digest,
        "wayback_timestamp": raw.get("wayback_timestamp", ""),
    }

    return ParsedSignal(
        signal=signal,
        entities=entities,
        vendor_hints=vendor_hints,
        confidence=round(base, 4),
    )


class WaybackParser:
    """
    Async batch parser for WAYBACK signals.

    Usage
    -----
        parser = WaybackParser()
        parsed = await parser.parse_all(wayback_signals)
    """

    async def parse(self, signal: Signal) -> ParsedSignal | None:
        if signal.source != SignalSource.WAYBACK:
            return None
        return await asyncio.to_thread(_parse_one, signal)

    async def parse_all(self, signals: Iterable[Signal]) -> list[ParsedSignal]:
        wb_signals = [s for s in signals if s.source == SignalSource.WAYBACK]
        if not wb_signals:
            return []
        results = await asyncio.gather(
            *[self.parse(s) for s in wb_signals], return_exceptions=True
        )
        out = [r for r in results if isinstance(r, ParsedSignal)]
        logger.info("[WB-PARSER] Parsed %d/%d Wayback signals", len(out), len(wb_signals))
        return out
