# Database writer — persists signals and launch results to Supabase PostgreSQL.
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from models.signal import Signal
from models.launch_intelligence import LaunchIntelligenceObject

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent.parent / ".env")

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS sniper_signals (
    signal_id     TEXT PRIMARY KEY,
    source        TEXT,
    signal_type   TEXT,
    target_domain TEXT,
    timestamp     TIMESTAMPTZ,
    raw_data      JSONB,
    confidence    FLOAT,
    vendor_hint   TEXT,
    detected_at   TIMESTAMPTZ,
    run_id        TEXT
);
"""

_CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS sniper_results (
    id                    SERIAL PRIMARY KEY,
    run_id                TEXT,
    competitor            TEXT,
    suspected_product     TEXT,
    confidence            FLOAT,
    estimated_launch_date TEXT,
    signal_count          INT,
    signal_sources        JSONB,
    launch_signals        JSONB,
    counter_playbook      JSONB,
    detected_at           TIMESTAMPTZ
);
"""

_INSERT_SIGNAL = """
INSERT INTO sniper_signals
    (signal_id, source, signal_type, target_domain,
     timestamp, raw_data, confidence, vendor_hint, detected_at, run_id)
VALUES
    (%(signal_id)s, %(source)s, %(signal_type)s, %(target_domain)s,
     %(timestamp)s, %(raw_data)s::jsonb, %(confidence)s, %(vendor_hint)s,
     %(detected_at)s, %(run_id)s)
ON CONFLICT (signal_id) DO NOTHING;
"""

_INSERT_RESULT = """
INSERT INTO sniper_results
    (run_id, competitor, suspected_product, confidence,
     estimated_launch_date, signal_count, signal_sources,
     launch_signals, counter_playbook, detected_at)
VALUES
    (%(run_id)s, %(competitor)s, %(suspected_product)s, %(confidence)s,
     %(estimated_launch_date)s, %(signal_count)s, %(signal_sources)s::jsonb,
     %(launch_signals)s::jsonb, %(counter_playbook)s::jsonb, %(detected_at)s);
"""


# ── Row builders ──────────────────────────────────────────────────────────────


def _signal_row(sig: Signal, run_id: str) -> dict:
    return {
        "signal_id":    sig.signal_id,
        "source":       sig.source,
        "signal_type":  sig.signal_type,
        "target_domain": sig.target_domain,
        "timestamp":    sig.timestamp,
        "raw_data":     json.dumps(sig.raw_data, default=str),
        "confidence":   sig.confidence,
        "vendor_hint":  sig.vendor_hint,
        "detected_at":  sig.detected_at,
        "run_id":       run_id,
    }


def _result_row(obj: LaunchIntelligenceObject, run_id: str) -> dict:
    cp = obj.counter_playbook
    return {
        "run_id":                run_id,
        "competitor":            obj.competitor,
        "suspected_product":     obj.suspected_product,
        "confidence":            obj.confidence,
        "estimated_launch_date": obj.estimated_launch_date,
        "signal_count":          obj.signal_count,
        "signal_sources":        json.dumps(obj.signal_sources),
        "launch_signals":        json.dumps(obj.launch_signals),
        "counter_playbook":      json.dumps({
            "seo":     cp.seo,
            "content": cp.content,
            "sales":   cp.sales,
        }),
        "detected_at":           obj.detected_at,
    }


# ── Writer ────────────────────────────────────────────────────────────────────


class DatabaseWriter:
    """
    Persists sniper signals and launch results to Supabase PostgreSQL.

    Signal deduplication
    --------------------
    signal_id is the PRIMARY KEY — ON CONFLICT DO NOTHING means re-running
    on the same domains never creates duplicate signal rows.

    Results are always inserted (one row per run per competitor) so the
    full history of detections across runs is preserved.
    """

    def __init__(self) -> None:
        self._url = os.getenv("DATABASE_URL", "")
        self._available = bool(self._url)
        if not self._available:
            logger.info("[DB] DATABASE_URL not set — persistence disabled")

    async def save_run(
        self,
        signals: list[Signal],
        results: list[LaunchIntelligenceObject],
        run_id: str,
    ) -> None:
        if not self._available:
            print("[DB] No DATABASE_URL — skipping persistence")
            return
        try:
            await asyncio.to_thread(self._write_sync, signals, results, run_id)
            print(
                f"[DB] ✓ Persisted {len(signals)} signal(s) and "
                f"{len(results)} result(s)  (run_id={run_id})"
            )
        except Exception as exc:
            logger.error("[DB] Persistence failed: %s", exc)
            print(f"[DB] ✗ Persistence error: {exc}")

    def _write_sync(
        self,
        signals: list[Signal],
        results: list[LaunchIntelligenceObject],
        run_id: str,
    ) -> None:
        try:
            import psycopg
        except ImportError:
            raise RuntimeError(
                "psycopg not installed. Run: pip install 'psycopg[binary]>=3.1'"
            )

        with psycopg.connect(self._url) as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SIGNALS)
                cur.execute(_CREATE_RESULTS)

                new_signals = 0
                for sig in signals:
                    cur.execute(_INSERT_SIGNAL, _signal_row(sig, run_id))
                    if cur.rowcount:
                        new_signals += 1

                for obj in results:
                    cur.execute(_INSERT_RESULT, _result_row(obj, run_id))

            conn.commit()

        logger.info(
            "[DB] run_id=%s — %d new signal(s) (of %d total), %d result(s)",
            run_id, new_signals, len(signals), len(results),
        )
