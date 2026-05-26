"""
stage6/backend/routes/deals.py
--------------------------------
Deal intelligence CRUD + pipeline trigger endpoints.

GET  /api/stats              — summary statistics
GET  /api/signals            — raw signals (filterable)
GET  /api/clusters           — deal clusters (filterable)
GET  /api/clusters/{id}      — single cluster
GET  /api/intelligence       — enriched deal records (filterable)
GET  /api/intelligence/{id}  — single intelligence record
POST /api/ingest             — load a sample_deals JSON file into the DB
POST /api/run                — trigger full pipeline in background
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import (
    DealClusterDB,
    DealClusterOut,
    DealIntelligenceDB,
    DealIntelligenceOut,
    IngestResponse,
    RunRequest,
    SignalDB,
    SignalOut,
    StatsOut,
)

router = APIRouter(tags=["deals"])
logger = logging.getLogger(__name__)

# Project root (for pipeline imports)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # …/Hi_Labs_WebData/


# ── Stats ─────────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    """Return summary counts and cost across all stored data."""
    total_cost = (
        db.query(func.sum(DealIntelligenceDB.estimated_cost_usd)).scalar() or 0.0
    )
    return StatsOut(
        signals=db.query(SignalDB).count(),
        clusters=db.query(DealClusterDB).count(),
        intelligence=db.query(DealIntelligenceDB).count(),
        tiers={
            "high":   db.query(DealIntelligenceDB).filter(DealIntelligenceDB.tier == "high").count(),
            "medium": db.query(DealIntelligenceDB).filter(DealIntelligenceDB.tier == "medium").count(),
            "low":    db.query(DealIntelligenceDB).filter(DealIntelligenceDB.tier == "low").count(),
        },
        total_ai_cost_usd=round(total_cost, 6),
    )


# ── Signals ───────────────────────────────────────────────────────────────────


@router.get("/signals", response_model=List[SignalOut])
def list_signals(
    domain:      Optional[str] = None,
    signal_type: Optional[str] = None,
    source:      Optional[str] = None,
    limit:       int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(SignalDB)
    if domain:
        q = q.filter(SignalDB.target_domain.ilike(f"%{domain}%"))
    if signal_type:
        q = q.filter(SignalDB.signal_type == signal_type)
    if source:
        q = q.filter(SignalDB.source == source)
    return q.order_by(SignalDB.timestamp.desc()).limit(min(limit, 500)).all()


# ── Clusters ──────────────────────────────────────────────────────────────────


@router.get("/clusters", response_model=List[DealClusterOut])
def list_clusters(
    tier:   Optional[str] = None,
    domain: Optional[str] = None,
    limit:  int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(DealClusterDB)
    if tier:
        q = q.filter(DealClusterDB.tier == tier.lower())
    if domain:
        q = q.filter(DealClusterDB.target_domain.ilike(f"%{domain}%"))
    return q.order_by(DealClusterDB.confidence.desc()).limit(min(limit, 200)).all()


@router.get("/clusters/{cluster_id}", response_model=DealClusterOut)
def get_cluster(cluster_id: str, db: Session = Depends(get_db)):
    obj = db.query(DealClusterDB).filter(DealClusterDB.cluster_id == cluster_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return obj


# ── Intelligence ──────────────────────────────────────────────────────────────


@router.get("/intelligence", response_model=List[DealIntelligenceOut])
def list_intelligence(
    tier:   Optional[str] = None,
    vendor: Optional[str] = None,
    domain: Optional[str] = None,
    limit:  int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(DealIntelligenceDB)
    if tier:
        q = q.filter(DealIntelligenceDB.tier == tier.lower())
    if vendor:
        q = q.filter(DealIntelligenceDB.suspected_vendor.ilike(f"%{vendor}%"))
    if domain:
        q = q.filter(DealIntelligenceDB.target_company.ilike(f"%{domain}%"))
    return q.order_by(DealIntelligenceDB.confidence.desc()).limit(min(limit, 200)).all()


@router.get("/intelligence/{intelligence_id}", response_model=DealIntelligenceOut)
def get_intelligence(intelligence_id: str, db: Session = Depends(get_db)):
    obj = db.query(DealIntelligenceDB).filter(
        DealIntelligenceDB.intelligence_id == intelligence_id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Intelligence record not found")
    return obj


# ── Ingest JSON ───────────────────────────────────────────────────────────────


@router.post("/ingest", response_model=IngestResponse)
def ingest_json(filename: str = "acme.json", db: Session = Depends(get_db)):
    """
    Load a sample_deals JSON file into deal_intelligence table.

    Pass ?filename=acme.json  (relative to stage6/sample_deals/)
    or an absolute path.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = _PROJECT_ROOT / "stage6" / "sample_deals" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    records: list[dict] = raw if isinstance(raw, list) else [raw]

    ingested = skipped = 0
    for rec in records:
        iid = rec.get("intelligence_id") or str(uuid.uuid4())
        if db.query(DealIntelligenceDB).filter(
            DealIntelligenceDB.intelligence_id == iid
        ).first():
            skipped += 1
            continue

        db.add(DealIntelligenceDB(
            intelligence_id      = iid,
            cluster_id           = rec.get("cluster_id", str(uuid.uuid4())),
            target_company       = rec.get("target_company", ""),
            suspected_vendor     = rec.get("suspected_vendor", "unknown"),
            vendor_category      = rec.get("vendor_category", "other"),
            signals              = rec.get("signals", []),
            confidence           = float(rec.get("confidence", 0.5)),
            tier                 = str(rec.get("tier", "low")).lower(),
            deal_closed_estimate = rec.get("deal_closed_estimate", "Unknown"),
            outreach_window      = rec.get("outreach_window", "Unknown"),
            reasoning            = rec.get("reasoning", ""),
            new_vendor_patterns  = rec.get("new_vendor_patterns", []),
            enriched_at          = datetime.now(timezone.utc),
            model_used           = rec.get("model_used", "gpt-4o-mini"),
            input_tokens         = int(rec.get("input_tokens", 0)),
            output_tokens        = int(rec.get("output_tokens", 0)),
            estimated_cost_usd   = float(rec.get("estimated_cost_usd", 0.0)),
            intel_metadata       = rec.get("metadata", {}),
        ))
        ingested += 1

    db.commit()
    logger.info("[INGEST] %s: ingested=%d, skipped=%d", filename, ingested, skipped)
    return IngestResponse(ingested=ingested, skipped=skipped, total_in_file=len(records))


# ── Pipeline trigger ──────────────────────────────────────────────────────────


@router.post("/run")
async def run_pipeline(req: RunRequest, background_tasks: BackgroundTasks):
    """
    Trigger a full Stages 1–5 pipeline run in the background.
    Results are persisted to the database when the run completes.
    Poll GET /api/stats to watch the record counts grow.
    """
    run_id = str(uuid.uuid4())

    async def _run() -> None:
        # Ensure project root is importable
        if str(_PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(_PROJECT_ROOT))

        db = SessionLocal()
        try:
            from pipeline.runner import PipelineRunner

            runner = PipelineRunner(
                target_domains=req.target_domains,
                github_orgs=req.github_orgs,
            )
            signals, clusters, intel_objects = await runner.run_full_pipeline(
                window_days=req.window_days,
                enrich=req.enrich,
            )

            # ── Persist signals ───────────────────────────────────────────
            for sig in signals:
                if db.query(SignalDB).filter(SignalDB.signal_id == sig.signal_id).first():
                    continue
                db.add(SignalDB(
                    signal_id       = sig.signal_id,
                    source          = sig.source.value,
                    target_domain   = sig.target_domain,
                    signal_type     = sig.signal_type.value,
                    timestamp       = sig.timestamp,
                    raw_data        = sig.raw_data,
                    confidence      = sig.confidence,
                    vendor_hint     = sig.vendor_hint,
                    signal_metadata = sig.metadata,
                ))

            # ── Persist clusters ──────────────────────────────────────────
            for cluster in clusters:
                if db.query(DealClusterDB).filter(DealClusterDB.cluster_id == cluster.cluster_id).first():
                    continue
                db.add(DealClusterDB(
                    cluster_id       = cluster.cluster_id,
                    target_domain    = cluster.target_domain,
                    signal_ids       = cluster.signal_ids,
                    signal_count     = cluster.signal_count,
                    source_diversity = cluster.source_diversity,
                    vendors          = cluster.vendors,
                    window_start     = cluster.window_start,
                    window_end       = cluster.window_end,
                    confidence       = cluster.confidence,
                    tier             = cluster.tier.value,
                    summary          = cluster.summary,
                    cluster_metadata = cluster.metadata,
                ))

            # ── Persist intelligence ──────────────────────────────────────
            for intel in intel_objects:
                if db.query(DealIntelligenceDB).filter(
                    DealIntelligenceDB.intelligence_id == intel.intelligence_id
                ).first():
                    continue
                db.add(DealIntelligenceDB(
                    intelligence_id      = intel.intelligence_id,
                    cluster_id           = intel.cluster_id,
                    target_company       = intel.target_company,
                    suspected_vendor     = intel.suspected_vendor,
                    vendor_category      = intel.vendor_category,
                    signals              = intel.signals,
                    confidence           = intel.confidence,
                    tier                 = intel.tier.value,
                    deal_closed_estimate = intel.deal_closed_estimate,
                    outreach_window      = intel.outreach_window,
                    reasoning            = intel.reasoning,
                    new_vendor_patterns  = [
                        {"pattern": p.pattern, "vendor": p.vendor,
                         "category": p.category, "confidence": p.confidence}
                        for p in intel.new_vendor_patterns
                    ],
                    enriched_at          = intel.enriched_at,
                    model_used           = intel.model_used,
                    input_tokens         = intel.input_tokens,
                    output_tokens        = intel.output_tokens,
                    estimated_cost_usd   = intel.estimated_cost_usd,
                    intel_metadata       = intel.metadata,
                ))

            db.commit()
            logger.info(
                "[RUN %s] Complete — %d signals, %d clusters, %d intel",
                run_id, len(signals), len(clusters), len(intel_objects),
            )

        except Exception as exc:
            db.rollback()
            logger.error("[RUN %s] Failed: %s", run_id, exc)
        finally:
            db.close()

    background_tasks.add_task(_run)
    return {
        "run_id": run_id,
        "status": "started",
        "domains": req.target_domains,
        "message": "Pipeline running in background — poll GET /api/stats to track progress",
    }
