"""
stage3_parsing/ct_log_parser.py
--------------------------------
Stage 3 — CT Log Signal Parser.

Takes raw CT_LOG Signals and extracts:
  • All unique domain names from SAN entries
  • Issuer organisation and root CA
  • Wildcard vs. single-domain classification
  • Vendor hints from each subdomain prefix
  • Certificate lifespan (short-lived = LE/automated infra signal)

Runs with asyncio.to_thread since parsing is CPU-bound.
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

# Let's Encrypt vs. commercial CA
_LE_ISSUERS = {"let's encrypt", "letsencrypt", "r3", "r10", "e1", "e2"}


def _issuer_is_le(issuer_name: str) -> bool:
    return any(le in issuer_name.lower() for le in _LE_ISSUERS)


def _lifespan_days(not_before: str | None, not_after: str | None) -> int | None:
    """Parse ISO dates from raw cert data and return lifespan in days."""
    if not not_before or not not_after:
        return None
    try:
        fmt = "%Y-%m-%dT%H:%M:%S"
        nb = datetime.strptime(not_before[:19], fmt).replace(tzinfo=timezone.utc)
        na = datetime.strptime(not_after[:19], fmt).replace(tzinfo=timezone.utc)
        return (na - nb).days
    except Exception:
        return None


def _parse_one(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    all_domains: list[str] = raw.get("all_domains", [])
    cert_domain: str = raw.get("cert_domain", "")

    # SAN extraction from name_value (polling path) or all_domains (stream path)
    name_value: str = raw.get("name_value", "")
    if name_value and not all_domains:
        all_domains = [d.strip() for d in name_value.split("\n") if d.strip()]

    # Canonical domain list
    unique_domains = list({d.lstrip("*.").lower() for d in all_domains if d})
    wildcards = [d for d in all_domains if d.startswith("*.")]
    sans = [d for d in all_domains if not d.startswith("*.")]

    # Issuer
    issuer_raw = raw.get("issuer_name", "")
    if isinstance(raw.get("issuer"), dict):
        issuer_raw = raw["issuer"].get("O", raw["issuer"].get("CN", ""))
    is_le = _issuer_is_le(issuer_raw)

    # Lifespan
    lifespan = _lifespan_days(raw.get("not_before"), raw.get("not_after"))

    # Vendor hints from each SAN subdomain
    vendor_hints: list[str] = []
    for fqdn in unique_domains:
        hint = _fingerprinter.match_subdomain(fqdn)
        if hint:
            vendor_hints.append(hint.vendor)

    vendor_hints = list(dict.fromkeys(vendor_hints))  # dedup, preserve order

    # Confidence adjustment:
    # Short-lived cert (≤90d) from LE → automated infra, lower baseline
    # Wildcard → enterprise-grade, higher
    # Multi-SAN with vendor-specific subdomains → highest
    base = signal.confidence
    if wildcards:
        base = max(base, 0.80)
    if vendor_hints:
        base = min(base + 0.10 * len(vendor_hints), 0.95)
    if is_le and lifespan and lifespan <= 90:
        base = max(base - 0.05, 0.30)

    entities = {
        "all_domains": unique_domains,
        "wildcards": wildcards,
        "sans": sans,
        "san_count": len(all_domains),
        "issuer": issuer_raw,
        "is_lets_encrypt": is_le,
        "lifespan_days": lifespan,
        "cert_domain": cert_domain,
        "serial_number": raw.get("serial_number"),
        "fingerprint": raw.get("fingerprint"),
        "not_before": raw.get("not_before"),
        "not_after": raw.get("not_after"),
    }

    return ParsedSignal(
        signal=signal,
        entities=entities,
        vendor_hints=vendor_hints,
        confidence=round(base, 4),
    )


class CTLogParser:
    """
    Async batch parser for CT_LOG signals.

    Usage
    -----
        parser = CTLogParser()
        parsed = await parser.parse_all(ct_signals)
    """

    async def parse(self, signal: Signal) -> ParsedSignal | None:
        if signal.source != SignalSource.CT_LOG:
            return None
        return await asyncio.to_thread(_parse_one, signal)

    async def parse_all(self, signals: Iterable[Signal]) -> list[ParsedSignal]:
        ct_signals = [s for s in signals if s.source == SignalSource.CT_LOG]
        if not ct_signals:
            return []
        results = await asyncio.gather(
            *[self.parse(s) for s in ct_signals], return_exceptions=True
        )
        out: list[ParsedSignal] = []
        for i, r in enumerate(results):
            if isinstance(r, ParsedSignal):
                out.append(r)
            elif isinstance(r, Exception):
                logger.debug("[CT-PARSER] Error: %s", r)
        logger.info("[CT-PARSER] Parsed %d/%d CT signals", len(out), len(ct_signals))
        return out
