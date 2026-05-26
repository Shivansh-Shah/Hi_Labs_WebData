"""
models/signal.py
----------------
Canonical Signal object emitted by every Stage 1 collector.
All parsers must produce a Signal; Stage 3+ consumers depend on this contract.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SignalSource(str, Enum):
    CT_LOG = "ct_log"
    DNS = "dns"
    WAYBACK = "wayback"
    GITHUB = "github"
    WHOIS = "whois"
    ROBOTS_TXT = "robots_txt"
    TRADEMARK = "trademark"


class SignalType(str, Enum):
    # Certificate Transparency
    NEW_CERT = "new_cert"
    NEW_WILDCARD_CERT = "new_wildcard_cert"
    CERT_SAN_MATCH = "cert_san_match"

    # DNS
    NEW_SUBDOMAIN = "new_subdomain"
    NEW_A_RECORD = "new_a_record"
    NEW_CNAME = "new_cname"

    # Wayback
    NEW_INTEGRATION_PAGE = "new_integration_page"
    NEW_DOCS_PAGE = "new_docs_page"
    NEW_STATUS_PAGE = "new_status_page"
    PAGE_CONTENT_CHANGE = "page_content_change"

    # GitHub
    COMMIT_SPIKE = "commit_spike"
    NEW_REPO = "new_repo"
    CUSTOMER_NAME_IN_COMMIT = "customer_name_in_commit"
    ORG_MEMBERSHIP_CHANGE = "org_membership_change"

    # Module 2
    NEW_DOMAIN_REGISTRATION = "new_domain_registration"
    TRADEMARK_FILING = "trademark_filing"
    ROBOTS_TXT_CHANGE = "robots_txt_change"


class ConfidenceTier(str, Enum):
    LOW = "low"        # single signal, weak pattern
    MEDIUM = "medium"  # 2 corroborating signals
    HIGH = "high"      # 3+ signals within cluster window


@dataclass
class Signal:
    """
    Normalised intelligence unit produced by every Stage 1 collector.

    Attributes
    ----------
    source          : which collector emitted this signal
    target_domain   : the company / domain being monitored (e.g. "acme.com")
    signal_type     : semantic classification of the detected event
    timestamp       : when the underlying event occurred (UTC)
    raw_data        : original payload from the data source (for auditability)
    detected_at     : when *our* system detected it (may lag the event)
    signal_id       : stable unique ID for deduplication
    vendor_hint     : optional vendor name implied by the signal (e.g. "Salesforce")
    confidence      : raw confidence score 0–1 at collection time (pre-correlation)
    metadata        : arbitrary k/v for parser-specific extras
    """
    source: SignalSource
    target_domain: str
    signal_type: SignalType
    timestamp: datetime
    raw_data: dict[str, Any]

    # Auto-populated
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Optional enrichment fields (populated by Stage 4+)
    vendor_hint: str | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "source": self.source.value,
            "target_domain": self.target_domain,
            "signal_type": self.signal_type.value,
            "timestamp": self.timestamp.isoformat(),
            "detected_at": self.detected_at.isoformat(),
            "vendor_hint": self.vendor_hint,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "raw_data": self.raw_data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signal:
        return cls(
            source=SignalSource(d["source"]),
            target_domain=d["target_domain"],
            signal_type=SignalType(d["signal_type"]),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            raw_data=d.get("raw_data", {}),
            detected_at=datetime.fromisoformat(d["detected_at"]),
            signal_id=d.get("signal_id", str(uuid.uuid4())),
            vendor_hint=d.get("vendor_hint"),
            confidence=d.get("confidence", 0.0),
            metadata=d.get("metadata", {}),
        )
