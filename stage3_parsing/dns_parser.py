"""
stage3_parsing/dns_parser.py
-----------------------------
Stage 3 — DNS Signal Parser.

Takes raw DNS Signals and extracts:
  • Fully-qualified domain name (FQDN)
  • Subdomain depth (e.g., a.b.c.target.com = depth 3)
  • Vendor hint from subdomain prefix via VendorFingerprinter
  • Category (crm, analytics, identity, etc.)
  • First-seen vs. re-seen flag (tracked in-memory across runs)

Also validates that FQDNs are syntactically well-formed.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from models.signal import Signal, SignalSource
from models.parsed_signal import ParsedSignal
from stage3_parsing.vendor_fingerprinter import VendorFingerprinter

logger = logging.getLogger(__name__)
_fingerprinter = VendorFingerprinter()

# Valid FQDN pattern (RFC 1123)
_FQDN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I
)

# Track first-seen FQDNs (in-memory; production would use Redis/PG)
_FQDN_FIRST_SEEN: dict[str, datetime] = {}


def _is_valid_fqdn(fqdn: str) -> bool:
    return bool(_FQDN_RE.match(fqdn)) and len(fqdn) <= 253


def _subdomain_depth(fqdn: str, apex: str) -> int:
    """How many subdomain labels sit above the apex domain."""
    # "crm.staging.acme.com" with apex "acme.com" → depth 2
    stripped = fqdn.lower().removesuffix("." + apex.lower()).removesuffix(apex.lower())
    return len(stripped.split(".")) if stripped else 0


def _parse_one(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    fqdn: str = raw.get("fqdn", "").lower().strip().rstrip(".")
    provider: str = raw.get("source", "unknown")
    apex = signal.target_domain

    if not _is_valid_fqdn(fqdn):
        # Still parse, just flag it
        entities = {"fqdn": fqdn, "valid": False, "provider": provider}
        return ParsedSignal(signal=signal, entities=entities, confidence=0.10)

    # First-seen tracking
    now = datetime.now(timezone.utc)
    is_first_seen = fqdn not in _FQDN_FIRST_SEEN
    if is_first_seen:
        _FQDN_FIRST_SEEN[fqdn] = now

    # Vendor fingerprinting
    fp_result = _fingerprinter.match_subdomain(fqdn)
    vendor_hints = [fp_result.vendor] if fp_result else []
    category = fp_result.category if fp_result else None
    fp_confidence = fp_result.confidence if fp_result else 0.0

    depth = _subdomain_depth(fqdn, apex)

    # Subdomain label for quick rule matching
    prefix = fqdn.split(".")[0].lower()

    # Confidence heuristics:
    # - Vendor-specific prefix → boost by fingerprint confidence
    # - Deep subdomains (staging.crm.acme.com) → operational, not deal signal
    # - First time seen → higher value
    base = signal.confidence
    if fp_result:
        base = max(base, fp_confidence)
    if depth >= 3:
        base *= 0.80   # deep subdomain = infrastructure noise
    if not is_first_seen:
        base *= 0.70   # re-seen = less interesting

    entities = {
        "fqdn": fqdn,
        "prefix": prefix,
        "depth": depth,
        "valid": True,
        "provider": provider,
        "vendor": fp_result.vendor if fp_result else None,
        "category": category,
        "is_first_seen": is_first_seen,
        "first_seen_at": _FQDN_FIRST_SEEN.get(fqdn, now).isoformat(),
    }

    return ParsedSignal(
        signal=signal,
        entities=entities,
        vendor_hints=vendor_hints,
        confidence=round(min(base, 0.95), 4),
    )


class DNSParser:
    """
    Async batch parser for DNS signals.

    Usage
    -----
        parser = DNSParser()
        parsed = await parser.parse_all(dns_signals)
    """

    async def parse(self, signal: Signal) -> ParsedSignal | None:
        if signal.source != SignalSource.DNS:
            return None
        return await asyncio.to_thread(_parse_one, signal)

    async def parse_all(self, signals: Iterable[Signal]) -> list[ParsedSignal]:
        dns_signals = [s for s in signals if s.source == SignalSource.DNS]
        if not dns_signals:
            return []
        results = await asyncio.gather(
            *[self.parse(s) for s in dns_signals], return_exceptions=True
        )
        out = [r for r in results if isinstance(r, ParsedSignal)]
        logger.info("[DNS-PARSER] Parsed %d/%d DNS signals", len(out), len(dns_signals))
        return out
