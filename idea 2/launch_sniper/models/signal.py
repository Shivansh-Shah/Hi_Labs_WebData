# Signal dataclass and enums (SignalSource, SignalType) for the launch_sniper module.
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class SignalSource:
    WHOIS = "whois"
    TRADEMARK = "trademark"
    ROBOTS_TXT = "robots_txt"


class SignalType:
    NEW_DOMAIN_REGISTRATION = "new_domain_registration"
    TRADEMARK_FILING = "trademark_filing"
    NEW_BLOCKED_PATH = "new_blocked_path"


@dataclass
class Signal:
    source: str                  # SignalSource constant
    target_domain: str
    signal_type: str             # SignalType constant
    timestamp: datetime
    raw_data: dict[str, Any]

    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 0.0
    vendor_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "source": self.source,
            "target_domain": self.target_domain,
            "signal_type": self.signal_type,
            "timestamp": self.timestamp.isoformat(),
            "detected_at": self.detected_at.isoformat(),
            "confidence": self.confidence,
            "vendor_hint": self.vendor_hint,
            "raw_data": self.raw_data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Signal:
        return cls(
            source=d["source"],
            target_domain=d["target_domain"],
            signal_type=d["signal_type"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            raw_data=d.get("raw_data", {}),
            signal_id=d.get("signal_id", str(uuid.uuid4())),
            detected_at=datetime.fromisoformat(d["detected_at"]),
            confidence=d.get("confidence", 0.0),
            vendor_hint=d.get("vendor_hint"),
        )
