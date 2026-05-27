"""
stage6/backend/routes/sniper.py
---------------------------------
Launch Sniper pipeline endpoints.

POST /api/sniper/run             — trigger sniper pipeline for given domains
GET  /api/sniper/runs            — list all recent runs (newest first)
GET  /api/sniper/status/{run_id} — status + output for a specific run
GET  /api/sniper/stats           — aggregate stats across all runs
GET  /api/sniper/results         — all detected launches (filterable)
POST /api/sniper/email           — send sniper report via Gmail SMTP
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import smtplib
import sys
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["sniper"], prefix="/sniper")
logger = logging.getLogger(__name__)

# Paths
_PROJECT_ROOT = Path(__file__).resolve().parents[3]   # Hi_Labs_WebData/
_SNIPER_MAIN  = _PROJECT_ROOT / "idea 2" / "launch_sniper" / "main.py"
_OUTPUT_DIR   = _PROJECT_ROOT / "stage6" / "sniper_output"

# In-memory run store  {run_id: {...}}
_runs: dict[str, dict] = {}


# ── Request / response schemas ─────────────────────────────────────────────────


class SniperRunRequest(BaseModel):
    domains: List[str]


class SniperEmailRequest(BaseModel):
    to_email: str
    tier_filter: str = ""          # "high" | "medium" | "low" | ""  (all)
    run_id: Optional[str] = None   # restrict to one run, or None = all runs


# ── Result parsing helpers ─────────────────────────────────────────────────────


_TABLE_RE = re.compile(
    r"""^\s*\|?\s*
        (?P<competitor>[^|]+?)\s*\|\s*
        (?P<product>[^|]+?)\s*\|\s*
        (?P<confidence>\d+)\s*%\s*
        (?:\|\s*(?P<tier>\w+)\s*)?""",
    re.VERBOSE,
)

_MD_TABLE_ROW_RE = re.compile(
    r"""^\|\s*(?P<competitor>[^|]+?)\s*
         \|\s*(?P<product>[^|]+?)\s*
         \|\s*(?P<confidence_raw>[^|]*?\d+[^|]*?)\s*
         \|""",
    re.VERBOSE,
)

_SKIP_HEADERS = {"domain", "competitor", "company", "---", "", "domain/company"}


def _parse_results_from_stdout(lines: list[str]) -> list[dict]:
    """Extract structured launch signals from stdout lines (pipeline table rows)."""
    results = []
    seen: set[tuple] = set()
    for line in lines:
        m = _TABLE_RE.match(line)
        if not m:
            continue
        competitor = m.group("competitor").strip().strip("|").strip()
        product    = m.group("product").strip()
        if competitor.lower() in _SKIP_HEADERS or "---" in competitor:
            continue
        confidence = min(100, max(0, int(m.group("confidence"))))
        tier_raw   = (m.group("tier") or "").strip().lower()
        tier       = tier_raw if tier_raw in ("high", "medium", "low") else (
            "high" if confidence >= 75 else "medium" if confidence >= 45 else "low"
        )
        key = (competitor.lower(), product.lower())
        if key not in seen:
            seen.add(key)
            results.append({
                "competitor":        competitor,
                "suspected_product": product,
                "confidence":        confidence,
                "tier":              tier,
                "signal_count":      0,
                "signals":           [],
                "counter_playbook":  "",
            })
    return results


def _parse_results_from_markdown(report_md: str) -> list[dict]:
    """
    Extract structured results from the generated markdown report.

    Supported formats:
      • Pipe-table rows:  | domain | product | 87% | HIGH |
      • Section headers:  ## domain.com followed by ### Signals / ### Counter-Playbook
    """
    results: list[dict] = []
    seen: set[tuple] = set()

    # ── 1. Parse summary table ────────────────────────────────────────────────
    for line in report_md.splitlines():
        m = _MD_TABLE_ROW_RE.match(line)
        if not m:
            continue
        competitor = m.group("competitor").strip()
        product    = m.group("product").strip()
        if competitor.lower() in _SKIP_HEADERS or "---" in competitor:
            continue
        conf_match = re.search(r"(\d+)", m.group("confidence_raw"))
        if not conf_match:
            continue
        confidence = min(100, max(0, int(conf_match.group(1))))
        tier       = "high" if confidence >= 75 else "medium" if confidence >= 45 else "low"
        key = (competitor.lower(), product.lower())
        if key not in seen:
            seen.add(key)
            results.append({
                "competitor":        competitor,
                "suspected_product": product,
                "confidence":        confidence,
                "tier":              tier,
                "signal_count":      0,
                "signals":           [],
                "counter_playbook":  "",
            })

    # ── 2. Enrich with section-level signals & playbooks ──────────────────────
    sections = re.split(r"\n#{1,2} ", report_md)
    for section in sections:
        lines = section.strip().splitlines()
        if not lines:
            continue
        heading = lines[0].strip().lstrip("#").strip().lower()
        # Find matching result
        match_idx = None
        for i, r in enumerate(results):
            if r["competitor"].lower() in heading or heading in r["competitor"].lower():
                match_idx = i
                break
        if match_idx is None:
            continue

        current_sub = ""
        signals: list[str] = []
        playbook_lines: list[str] = []

        for line in lines[1:]:
            stripped = line.strip()
            low = stripped.lower()
            if re.match(r"^#+", stripped):
                current_sub = re.sub(r"^#+\s*", "", stripped).lower()
            elif stripped.startswith(("- ", "* ", "• ", "· ")):
                item = stripped.lstrip("-*•· ").strip()
                if any(k in current_sub for k in ("signal", "indicator", "evidence")):
                    signals.append(item)
                elif any(k in current_sub for k in ("counter", "playbook", "action", "recommend")):
                    playbook_lines.append(item)
                else:
                    signals.append(item)
            elif stripped and any(k in current_sub for k in ("counter", "playbook", "action")):
                playbook_lines.append(stripped)

        if signals:
            results[match_idx]["signals"]      = signals
            results[match_idx]["signal_count"] = len(signals)
        if playbook_lines:
            results[match_idx]["counter_playbook"] = " ".join(playbook_lines[:3])

    return results


def _parse_results(output_lines: list[str], report_md: str, domains: list[str]) -> list[dict]:
    """
    Best-effort extraction of structured results.
    Tries markdown first (richer), then stdout lines, then returns empty placeholders.
    """
    results = _parse_results_from_markdown(report_md)
    if not results:
        results = _parse_results_from_stdout(output_lines)
    return results


# ── HTML email builder ─────────────────────────────────────────────────────────


_TIER_COLORS = {
    "high":   "#4ae176",
    "medium": "#f4c430",
    "low":    "#849495",
}


def _build_sniper_html(results: list[dict], run_date: str) -> str:
    cards = ""
    for r in results:
        tier     = (r.get("tier") or "low").lower()
        color    = _TIER_COLORS.get(tier, "#849495")
        conf     = r.get("confidence", 0)
        signals  = (r.get("signals") or [])[:4]
        playbook = r.get("counter_playbook", "")

        sigs_html = "".join(
            f'<li style="margin:3px 0;color:#374151;font-size:13px">{s[:120]}</li>'
            for s in signals
        )
        playbook_html = (
            f'<p style="margin:8px 0 0;color:#374151;font-size:13px;line-height:1.5">'
            f'<strong>Counter:</strong> {playbook[:200]}</p>'
        ) if playbook else ""

        cards += f"""
        <div style="border:1px solid #e5e7eb;border-radius:10px;padding:20px;
                    margin-bottom:16px;background:#fff;border-left:4px solid {color}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <p style="margin:0;font-size:18px;font-weight:700;color:#111">
                {r.get("competitor", "Unknown")}
              </p>
              <p style="margin:4px 0 0;font-size:14px;color:#6b7280">
                {r.get("suspected_product", "Pending analysis")}
              </p>
            </div>
            <span style="background:{color};color:#fff;padding:3px 10px;
                         border-radius:20px;font-size:12px;font-weight:600;
                         white-space:nowrap">
              {tier.upper()} · {conf}%
            </span>
          </div>
          {f'<ul style="margin:10px 0 0;padding-left:18px">{sigs_html}</ul>' if sigs_html else ""}
          {playbook_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Launch Sniper Report</title>
</head>
<body style="margin:0;padding:0;background:#f9fafb;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#f9fafb;padding:32px 16px">
    <tr><td>
      <table width="640" style="max-width:640px;margin:0 auto">
        <tr>
          <td style="background:#fff;border-radius:10px 10px 0 0;
                     border:1px solid #e5e7eb;border-bottom:none;padding:24px 28px">
            <p style="margin:0;font-size:13px;color:#6b7280;
                      text-transform:uppercase;letter-spacing:.06em">
              Launch Sniper · GTM Intelligence Platform
            </p>
            <h1 style="margin:4px 0 0;font-size:22px;font-weight:700;color:#111">
              Competitor Launch Intelligence Report
            </h1>
            <p style="margin:6px 0 0;font-size:14px;color:#6b7280">
              {run_date} · {len(results)} competitor{"s" if len(results) != 1 else ""} analysed
            </p>
          </td>
        </tr>
        <tr>
          <td style="background:#f9fafb;border-left:1px solid #e5e7eb;
                     border-right:1px solid #e5e7eb;padding:20px 28px">
            {cards if cards else
             '<p style="text-align:center;color:#9ca3af;padding:32px 0">No launches matched the selected filter.</p>'}
          </td>
        </tr>
        <tr>
          <td style="background:#fff;border-radius:0 0 10px 10px;
                     border:1px solid #e5e7eb;border-top:none;
                     padding:16px 28px;text-align:center">
            <p style="margin:0;font-size:11px;color:#9ca3af">
              Signals sourced from WHOIS · Trademark filings · robots.txt · GitHub<br>
              AI enrichment by gpt-4o-mini · GTM Intelligence Platform
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/run")
async def run_sniper(req: SniperRunRequest):
    """Start a Launch Sniper pipeline run in the background."""
    if not req.domains:
        raise HTTPException(status_code=400, detail="domains list is empty")

    run_id     = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc).isoformat()

    _runs[run_id] = {
        "run_id":       run_id,
        "domains":      req.domains,
        "status":       "running",
        "started_at":   started_at,
        "output":       [],
        "report_md":    "",
        "results":      [],
        "error":        None,
        "returncode":   None,
    }

    asyncio.create_task(_run_sniper_task(run_id, req.domains))

    return {
        "run_id":  run_id,
        "status":  "started",
        "domains": req.domains,
        "message": f"Pipeline running — poll GET /api/sniper/status/{run_id}",
    }


@router.get("/runs")
def list_runs():
    """Return all runs, newest first."""
    return list(reversed(list(_runs.values())))


@router.get("/stats")
def get_stats():
    """Aggregate stats across all runs."""
    all_runs   = list(_runs.values())
    completed  = [r for r in all_runs if r["status"] == "completed"]
    all_results: list[dict] = []
    for r in completed:
        all_results.extend(r.get("results") or [])

    high   = sum(1 for r in all_results if r.get("tier") == "high")
    medium = sum(1 for r in all_results if r.get("tier") == "medium")

    domains_set: set[str] = set()
    for r in all_runs:
        domains_set.update(r.get("domains") or [])

    return {
        "total_runs":        len(all_runs),
        "completed_runs":    len(completed),
        "active_runs":       sum(1 for r in all_runs if r["status"] == "running"),
        "domains_scanned":   len(domains_set),
        "launches_detected": len(all_results),
        "tiers": {
            "high":   high,
            "medium": medium,
            "low":    max(0, len(all_results) - high - medium),
        },
    }


@router.get("/results")
def get_results(tier: str = "", domain: str = ""):
    """Return all detected launches across all completed runs, sorted by confidence."""
    all_results: list[dict] = []
    for run in _runs.values():
        for result in (run.get("results") or []):
            enriched = {
                **result,
                "run_id":    run["run_id"],
                "scan_date": run.get("started_at", ""),
                "domains":   run.get("domains", []),
            }
            if tier and enriched.get("tier") != tier.lower():
                continue
            if domain and domain.lower() not in enriched.get("competitor", "").lower():
                continue
            all_results.append(enriched)

    return sorted(all_results, key=lambda r: r.get("confidence", 0), reverse=True)


@router.get("/status/{run_id}")
def get_run_status(run_id: str):
    """Return full status and stdout output for a run."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.post("/email")
def send_sniper_email(req: SniperEmailRequest):
    """Send a Launch Sniper intelligence report via Gmail SMTP."""
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        raise HTTPException(
            status_code=503,
            detail=(
                "GMAIL_USER or GMAIL_APP_PASSWORD not configured. "
                "Add both to .env and restart the server."
            ),
        )

    # Collect results
    target_runs = (
        [_runs[req.run_id]] if req.run_id and req.run_id in _runs
        else [r for r in _runs.values() if r["status"] == "completed"]
    )
    all_results: list[dict] = []
    for run in target_runs:
        for result in (run.get("results") or []):
            if req.tier_filter and result.get("tier") != req.tier_filter.lower():
                continue
            all_results.append(result)

    all_results.sort(key=lambda r: r.get("confidence", 0), reverse=True)

    run_date  = datetime.now(timezone.utc).strftime("%B %d, %Y")
    html_body = _build_sniper_html(all_results, run_date)

    tier_label = req.tier_filter.upper() if req.tier_filter else "ALL"
    subject    = (
        f"Launch Sniper: {len(all_results)} {tier_label} competitor signal"
        f"{'s' if len(all_results) != 1 else ''} — {run_date}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"GTM Intel <{gmail_user}>"
    msg["To"]      = req.to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_pass.replace(" ", ""))
            smtp.sendmail(gmail_user, req.to_email, msg.as_string())
        logger.info("[SNIPER EMAIL] Sent to %s — %d results", req.to_email, len(all_results))
        return {"sent": True, "to": req.to_email, "count": len(all_results)}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Gmail auth failed — check GMAIL_USER and GMAIL_APP_PASSWORD in .env",
        )
    except Exception as exc:
        logger.error("[SNIPER EMAIL] SMTP error: %s", exc)
        raise HTTPException(status_code=500, detail=f"SMTP error: {exc}")


# ── Background worker ──────────────────────────────────────────────────────────


async def _run_sniper_task(run_id: str, domains: list[str]) -> None:
    store = _runs[run_id]
    try:
        domains_str  = ",".join(domains)
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path  = str(_OUTPUT_DIR / f"sniper_{run_id}.md")

        if not _SNIPER_MAIN.exists():
            raise FileNotFoundError(f"Launch Sniper not found at {_SNIPER_MAIN}")

        cmd = [
            sys.executable, str(_SNIPER_MAIN),
            "--domains", domains_str,
            "--output",  output_path,
            "--quiet",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_SNIPER_MAIN.parent),
        )

        lines: list[str] = []
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            lines.append(line)
            store["output"] = lines[-200:]      # keep last 200 lines live
            logger.info("[SNIPER %s] %s", run_id, line)

        await proc.wait()

        # Read generated markdown report
        report_md = ""
        if Path(output_path).exists():
            report_md = Path(output_path).read_text(encoding="utf-8")

        # Parse structured results
        results = _parse_results(lines, report_md, domains)

        store.update(
            status       = "completed" if proc.returncode == 0 else "failed",
            returncode   = proc.returncode,
            report_md    = report_md,
            results      = results,
            completed_at = datetime.now(timezone.utc).isoformat(),
        )
        logger.info(
            "[SNIPER %s] Done — rc=%s, %d results extracted",
            run_id, proc.returncode, len(results),
        )

    except Exception as exc:
        logger.error("[SNIPER %s] Crashed: %s", run_id, exc, exc_info=True)
        store.update(
            status       = "failed",
            error        = str(exc),
            completed_at = datetime.now(timezone.utc).isoformat(),
        )
