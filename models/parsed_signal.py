"""
models/parsed_signal.py
------------------------
ParsedSignal — enriched output of Stage 3 parsers.

Wraps the raw Signal with extracted entities so Stage 4 can
correlate without re-parsing.

Entities extracted per source:
  CT_LOG   → domains (list), issuer, san_count, wildcard flag
  DNS      → fqdn, record_type, vendor (from fingerprint table)
  WAYBACK  → url, path_type, content_words (set), is_new
  GITHUB   → repo, commits_per_day, ner_orgs (spaCy), customer_matches
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from models.signal import Signal, SignalSource, SignalType


@dataclass
class ParsedSignal:
    """
    Enriched Signal emitted by Stage 3 parsers.

    Attributes
    ----------
    signal          : original raw Signal
    entities        : structured entities extracted by the parser
    vendor_hints    : all vendors inferred (may be >1 for multi-SAN certs)
    confidence      : refined confidence after parsing (may differ from signal.confidence)
    parser_version  : semver of the parser that produced this (for lineage)
    parsed_at       : UTC timestamp of parsing
    """
    signal: Signal
    entities: dict[str, Any]
    vendor_hints: list[str] = field(default_factory=list)
    confidence: float = 0.0
    parser_version: str = "1.0.0"
    parsed_at: datetime = field(default_factory=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ))

    # Convenience accessors
    @property
    def source(self) -> SignalSource:
        return self.signal.source

    @property
    def target_domain(self) -> str:
        return self.signal.target_domain

    @property
    def signal_type(self) -> SignalType:
        return self.signal.signal_type

    @property
    def timestamp(self) -> datetime:
        return self.signal.timestamp

    @property
    def signal_id(self) -> str:
        return self.signal.signal_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "source": self.source.value,
            "target_domain": self.target_domain,
            "signal_type": self.signal_type.value,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "vendor_hints": self.vendor_hints,
            "entities": self.entities,
            "parser_version": self.parser_version,
            "parsed_at": self.parsed_at.isoformat(),
            "original_signal": self.signal.to_dict(),
        }
