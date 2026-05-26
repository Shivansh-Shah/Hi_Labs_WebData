"""
stage6/backend/models.py
--------------------------
SQLAlchemy ORM table definitions + Pydantic response/request schemas.

Tables
------
  signals           ← Stage 1/2 raw Signal objects
  deal_clusters     ← Stage 4 correlation output
  deal_intelligence ← Stage 5 AI enrichment output

Pydantic schemas (for FastAPI request/response validation)
-----------------------------------------------------------
  SignalOut, DealClusterOut, DealIntelligenceOut
  RunRequest, DigestRequest, IngestResponse, StatsOut
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.types import JSON

from .database import Base


# ── ORM Models ────────────────────────────────────────────────────────────────


class SignalDB(Base):
    """Raw intelligence signal (Stage 1/2 output)."""
    __tablename__ = "signals"

    signal_id      = Column(String,  primary_key=True)
    source         = Column(String,  index=True, nullable=False)
    target_domain  = Column(String,  index=True, nullable=False)
    signal_type    = Column(String,  index=True, nullable=False)
    timestamp      = Column(DateTime(timezone=True), nullable=False)
    raw_data       = Column(JSON,    default=dict)
    confidence     = Column(Float,   nullable=False, default=0.5)
    vendor_hint    = Column(String,  nullable=True)
    # Renamed to avoid conflict with SQLAlchemy's Base.metadata attribute
    signal_metadata = Column("metadata", JSON, default=dict)
    ingested_at    = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class DealClusterDB(Base):
    """Correlated signal cluster (Stage 4 output)."""
    __tablename__ = "deal_clusters"

    cluster_id       = Column(String,  primary_key=True)
    target_domain    = Column(String,  index=True, nullable=False)
    signal_ids       = Column(JSON,    default=list)
    signal_count     = Column(Integer, default=0)
    source_diversity = Column(Integer, default=1)
    vendors          = Column(JSON,    default=list)
    window_start     = Column(DateTime(timezone=True))
    window_end       = Column(DateTime(timezone=True))
    confidence       = Column(Float,   nullable=False, default=0.5)
    tier             = Column(String,  index=True, nullable=False, default="low")
    summary          = Column(Text,    default="")
    cluster_metadata = Column("metadata", JSON, default=dict)
    created_at       = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class DealIntelligenceDB(Base):
    """LLM-enriched deal record (Stage 5 output)."""
    __tablename__ = "deal_intelligence"

    intelligence_id      = Column(String, primary_key=True)
    cluster_id           = Column(String, index=True, nullable=False)
    target_company       = Column(String, index=True, nullable=False)
    suspected_vendor     = Column(String, index=True, nullable=False)
    vendor_category      = Column(String, nullable=False, default="other")
    signals              = Column(JSON,   default=list)
    confidence           = Column(Float,  nullable=False, default=0.5)
    tier                 = Column(String, index=True, nullable=False, default="low")
    deal_closed_estimate = Column(String, default="Unknown")
    outreach_window      = Column(String, default="Unknown")
    reasoning            = Column(Text,   default="")
    new_vendor_patterns  = Column(JSON,   default=list)
    enriched_at          = Column(DateTime(timezone=True))
    model_used           = Column(String, default="gpt-4o-mini")
    input_tokens         = Column(Integer, default=0)
    output_tokens        = Column(Integer, default=0)
    estimated_cost_usd   = Column(Float,   default=0.0)
    intel_metadata       = Column("metadata", JSON, default=dict)


# ── Pydantic Response Schemas ─────────────────────────────────────────────────


class SignalOut(BaseModel):
    signal_id:     str
    source:        str
    target_domain: str
    signal_type:   str
    timestamp:     datetime
    confidence:    float
    vendor_hint:   Optional[str] = None

    model_config = {"from_attributes": True}


class DealClusterOut(BaseModel):
    cluster_id:       str
    target_domain:    str
    signal_count:     int
    source_diversity: int
    vendors:          List[str]
    window_start:     datetime
    window_end:       datetime
    confidence:       float
    tier:             str
    summary:          str

    model_config = {"from_attributes": True}


class DealIntelligenceOut(BaseModel):
    intelligence_id:      str
    cluster_id:           str
    target_company:       str
    suspected_vendor:     str
    vendor_category:      str
    signals:              List[str]
    confidence:           float
    tier:                 str
    deal_closed_estimate: str
    outreach_window:      str
    reasoning:            str
    new_vendor_patterns:  List[Dict[str, Any]]
    enriched_at:          datetime
    model_used:           str
    estimated_cost_usd:   float

    model_config = {"from_attributes": True}


class StatsOut(BaseModel):
    signals:             int
    clusters:            int
    intelligence:        int
    tiers:               Dict[str, int]
    total_ai_cost_usd:   float


# ── Pydantic Request Schemas ──────────────────────────────────────────────────


class RunRequest(BaseModel):
    target_domains: List[str] = Field(..., min_length=1)
    window_days:    int       = Field(30, ge=1, le=365)
    enrich:         bool      = True
    github_orgs:    List[str] = []


class DigestRequest(BaseModel):
    to_email:        str  = Field(..., pattern=r".+@.+\..+")
    tier_filter:     str  = "high"
    include_reasoning: bool = True


class IngestResponse(BaseModel):
    ingested:        int
    skipped:         int
    total_in_file:   int
