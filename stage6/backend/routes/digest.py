"""
stage6/backend/routes/digest.py
---------------------------------
Email digest endpoints (Brevo Transactional Email API).

GET  /api/digest/preview   — returns HTML body + record count (no email sent)
POST /api/digest/send      — sends email via Brevo API (HTTPS, works on Render)

Add to .env:
    BREVO_API_KEY=your-brevo-api-key
    BREVO_SENDER_EMAIL=you@gmail.com   (must be verified in Brevo dashboard)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..email_utils import send_email
from ..models import DealIntelligenceDB, DigestRequest

router = APIRouter(tags=["digest"])
logger = logging.getLogger(__name__)

_TIER_COLORS = {
    "high":   "#ef4444",
    "medium": "#f59e0b",
    "low":    "#6b7280",
}


# ── HTML builder ──────────────────────────────────────────────────────────────


def _build_html_email(
    records: List[DealIntelligenceDB],
    include_reasoning: bool,
) -> str:
    """Produce a clean, self-contained HTML email body."""
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    cards = ""
    for rec in records:
        tier_key = str(rec.tier).lower()
        color = _TIER_COLORS.get(tier_key, "#6b7280")
        reasoning_block = ""
        if include_reasoning and rec.reasoning:
            snippet = rec.reasoning[:220].rstrip()
            if len(rec.reasoning) > 220:
                snippet += "…"
            reasoning_block = (
                f'<p style="margin:8px 0 0;color:#6b7280;font-size:13px;'
                f'line-height:1.5">{snippet}</p>'
            )

        signals_html = ""
        for s in (rec.signals or [])[:3]:
            signals_html += (
                f'<li style="margin:2px 0;color:#374151;font-size:13px">{s[:100]}</li>'
            )

        cards += f"""
        <div style="border:1px solid #e5e7eb;border-radius:10px;padding:20px;
                    margin-bottom:16px;background:#fff">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <p style="margin:0;font-size:18px;font-weight:700;color:#111">
                {rec.target_company}
              </p>
              <p style="margin:4px 0 0;font-size:14px;color:#6b7280">
                {rec.suspected_vendor}
                <span style="font-style:italic">({rec.vendor_category})</span>
              </p>
            </div>
            <span style="background:{color};color:#fff;padding:3px 10px;
                         border-radius:20px;font-size:12px;font-weight:600;
                         white-space:nowrap">
              {tier_key.upper()}
            </span>
          </div>

          <table style="width:100%;margin-top:12px;border-collapse:collapse">
            <tr>
              <td style="padding:3px 8px 3px 0;width:50%;color:#6b7280;font-size:13px">
                Deal closed
              </td>
              <td style="padding:3px 0;font-size:13px;font-weight:600;color:#111">
                {rec.deal_closed_estimate}
              </td>
            </tr>
            <tr>
              <td style="padding:3px 8px 3px 0;color:#6b7280;font-size:13px">
                Outreach window
              </td>
              <td style="padding:3px 0;font-size:13px;font-weight:600;color:#111">
                {rec.outreach_window}
              </td>
            </tr>
            <tr>
              <td style="padding:3px 8px 3px 0;color:#6b7280;font-size:13px">
                Confidence
              </td>
              <td style="padding:3px 0;font-size:13px;font-weight:600;color:#111">
                {rec.confidence:.0%}
              </td>
            </tr>
          </table>

          {f'<ul style="margin:10px 0 0 0;padding-left:18px">{signals_html}</ul>' if signals_html else ""}
          {reasoning_block}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GTM Intelligence Digest</title>
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,
             BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#f9fafb;padding:32px 16px">
    <tr><td>
      <table width="640" style="max-width:640px;margin:0 auto">

        <!-- Header -->
        <tr>
          <td style="background:#fff;border-radius:10px 10px 0 0;
                     border:1px solid #e5e7eb;border-bottom:none;
                     padding:24px 28px">
            <p style="margin:0;font-size:13px;color:#6b7280;
                      text-transform:uppercase;letter-spacing:.06em">
              GTM Intelligence Platform
            </p>
            <h1 style="margin:4px 0 0;font-size:22px;font-weight:700;color:#111">
              Deal Intelligence Digest
            </h1>
            <p style="margin:6px 0 0;font-size:14px;color:#6b7280">
              {date_str} · {len(records)} deal{"s" if len(records) != 1 else ""}
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="background:#f9fafb;border-left:1px solid #e5e7eb;
                     border-right:1px solid #e5e7eb;padding:20px 28px">
            {cards if cards else
             '<p style="text-align:center;color:#9ca3af;padding:32px 0">No deals matched your filter.</p>'}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#fff;border-radius:0 0 10px 10px;
                     border:1px solid #e5e7eb;border-top:none;
                     padding:16px 28px;text-align:center">
            <p style="margin:0;font-size:11px;color:#9ca3af">
              Signals sourced from CT logs · DNS · Wayback Machine · GitHub<br>
              AI enrichment by gpt-4o-mini · GTM Intelligence Platform
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/digest/preview")
def preview_digest(
    tier_filter:       str  = "high",
    include_reasoning: bool = True,
    limit:             int  = 20,
    db: Session = Depends(get_db),
):
    """
    Return the HTML digest for the given tier filter — no email is sent.
    Useful for viewing in a browser before sending.
    """
    records = (
        db.query(DealIntelligenceDB)
        .filter(DealIntelligenceDB.tier == tier_filter.lower())
        .order_by(DealIntelligenceDB.confidence.desc())
        .limit(min(limit, 50))
        .all()
    )
    return {
        "count":       len(records),
        "tier_filter": tier_filter,
        "html":        _build_html_email(records, include_reasoning),
    }


@router.post("/digest/send")
def send_digest(req: DigestRequest, db: Session = Depends(get_db)):
    """Send an HTML email digest via Brevo Transactional Email API."""
    records = (
        db.query(DealIntelligenceDB)
        .filter(DealIntelligenceDB.tier == req.tier_filter.lower())
        .order_by(DealIntelligenceDB.confidence.desc())
        .limit(20)
        .all()
    )

    if not records:
        return {
            "sent":   False,
            "reason": f"No {req.tier_filter.upper()} tier deals found",
        }

    html_body = _build_html_email(records, req.include_reasoning)
    subject = (
        f"GTM Intel: {len(records)} {req.tier_filter.upper()} "
        f"deal{'s' if len(records) != 1 else ''} — "
        f"{datetime.now(timezone.utc).strftime('%b %d, %Y')}"
    )

    try:
        result = send_email(to=req.to_email, subject=subject, html=html_body)
        logger.info("[DIGEST] Sent to %s via Brevo", req.to_email)
        return {"sent": True, "to": req.to_email, "count": len(records), **result}
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except RuntimeError as exc:
        logger.error("[DIGEST] Brevo error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
