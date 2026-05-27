# LaunchIntelligence dataclass — correlated output object representing a detected product launch.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class CounterPlaybook:
    seo: list[str] = field(default_factory=list)
    content: list[str] = field(default_factory=list)
    sales: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "seo": self.seo,
            "content": self.content,
            "sales": self.sales,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CounterPlaybook:
        return cls(
            seo=d.get("seo", []),
            content=d.get("content", []),
            sales=d.get("sales", []),
        )


@dataclass
class LaunchIntelligenceObject:
    competitor: str
    launch_signals: list[str]
    confidence: float
    signal_count: int
    signal_sources: list[str]

    suspected_product: str | None = None
    estimated_launch_date: str | None = None
    counter_playbook: CounterPlaybook = field(default_factory=CounterPlaybook)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "competitor": self.competitor,
            "suspected_product": self.suspected_product,
            "launch_signals": self.launch_signals,
            "confidence": self.confidence,
            "estimated_launch_date": self.estimated_launch_date,
            "counter_playbook": self.counter_playbook.to_dict(),
            "signal_count": self.signal_count,
            "signal_sources": self.signal_sources,
            "detected_at": self.detected_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LaunchIntelligenceObject:
        return cls(
            competitor=d["competitor"],
            launch_signals=d.get("launch_signals", []),
            confidence=d.get("confidence", 0.0),
            signal_count=d.get("signal_count", 0),
            signal_sources=d.get("signal_sources", []),
            suspected_product=d.get("suspected_product"),
            estimated_launch_date=d.get("estimated_launch_date"),
            counter_playbook=CounterPlaybook.from_dict(d.get("counter_playbook", {})),
            detected_at=datetime.fromisoformat(d["detected_at"]) if "detected_at" in d else datetime.now(timezone.utc),
        )
