"""
tests/test_signal_model.py
--------------------------
Unit tests for the Signal data model.
"""
import json
from datetime import datetime, timezone

import pytest

from models.signal import Signal, SignalSource, SignalType


def make_signal(**kwargs) -> Signal:
    defaults = dict(
        source=SignalSource.CT_LOG,
        target_domain="acme.com",
        signal_type=SignalType.NEW_CERT,
        timestamp=datetime(2026, 4, 14, 12, 0, tzinfo=timezone.utc),
        raw_data={"cert_domain": "crm.acme.com"},
        confidence=0.75,
    )
    defaults.update(kwargs)
    return Signal(**defaults)


def test_signal_creation():
    s = make_signal()
    assert s.target_domain == "acme.com"
    assert s.confidence == 0.75
    assert s.signal_id is not None


def test_signal_to_dict_roundtrip():
    s = make_signal(vendor_hint="Salesforce")
    d = s.to_dict()
    s2 = Signal.from_dict(d)
    assert s2.signal_id == s.signal_id
    assert s2.vendor_hint == "Salesforce"
    assert s2.source == SignalSource.CT_LOG
    assert s2.signal_type == SignalType.NEW_CERT


def test_signal_json_serialisable():
    s = make_signal()
    # Should not raise
    json.dumps(s.to_dict())


def test_signal_unique_ids():
    s1 = make_signal()
    s2 = make_signal()
    assert s1.signal_id != s2.signal_id
