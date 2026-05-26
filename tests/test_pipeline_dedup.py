"""
tests/test_pipeline_dedup.py
-----------------------------
Tests for the SignalDeduplicator in the pipeline runner.
"""
from datetime import datetime, timezone

from models.signal import Signal, SignalSource, SignalType
from pipeline.runner import SignalDeduplicator


def _sig(source=SignalSource.CT_LOG, domain="acme.com", stype=SignalType.NEW_CERT, day=14):
    return Signal(
        source=source,
        target_domain=domain,
        signal_type=stype,
        timestamp=datetime(2026, 4, day, 12, 0, tzinfo=timezone.utc),
        raw_data={},
        confidence=0.7,
    )


def test_dedup_first_signal_is_accepted():
    d = SignalDeduplicator()
    s = _sig()
    assert not d.is_duplicate(s)


def test_dedup_same_signal_twice():
    d = SignalDeduplicator()
    s = _sig()
    d.is_duplicate(s)  # first — accepted
    assert d.is_duplicate(s)  # second — duplicate


def test_dedup_different_day_accepted():
    d = SignalDeduplicator()
    s1 = _sig(day=14)
    s2 = _sig(day=15)  # different date
    d.is_duplicate(s1)
    assert not d.is_duplicate(s2)


def test_dedup_different_domain_accepted():
    d = SignalDeduplicator()
    s1 = _sig(domain="acme.com")
    s2 = _sig(domain="globex.io")
    d.is_duplicate(s1)
    assert not d.is_duplicate(s2)


def test_dedup_filter():
    d = SignalDeduplicator()
    signals = [_sig(day=14), _sig(day=14), _sig(day=15)]
    filtered = d.filter(signals)
    assert len(filtered) == 2  # only unique ones
