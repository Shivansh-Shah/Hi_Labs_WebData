# Entry point for the launch_sniper pipeline — parses CLI args and orchestrates all stages.
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 so emoji print correctly on Windows (cp1252 default)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

# Load .env before any module that reads os.getenv at import time
load_dotenv(Path(__file__).parent / ".env")

from collectors.whois_collector import WhoisCollector
from collectors.trademark_collector import TrademarkCollector
from collectors.robots_txt_collector import RobotsTxtCollector
from models.signal import Signal, SignalSource
from models.launch_intelligence import LaunchIntelligenceObject
from stage2_brightdata.mcp_client import BrightDataMCPClient, BudgetExceededError
from stage3_parsing.parser_orchestrator import SniperParserOrchestrator
from stage4_correlation.correlation_engine import SniperCorrelationEngine
from stage5_enrichment.launch_enricher import LaunchEnricher
from stage6_delivery.report_generator import ReportGenerator
from stage6_delivery.slack_notifier import SniperSlackNotifier
from stage6_delivery.email_notifier import EmailNotifier
from stage6_delivery.db_writer import DatabaseWriter

logger = logging.getLogger(__name__)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="launch_sniper",
        description="Detect unreleased competitor product launches from public signals.",
    )
    parser.add_argument(
        "--domains",
        required=True,
        help="Comma-separated competitor domains to monitor (e.g. acme.com,rival.io)",
    )
    parser.add_argument(
        "--output",
        default="idea2/output/launch_report.md",
        help="Output path for the markdown report (default: idea2/output/launch_report.md)",
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        help="Send Slack alerts for high-confidence detections",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose logging (warnings and errors only)",
    )
    return parser.parse_args()


def _configure_logging(quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


# ── Summary table ─────────────────────────────────────────────────────────────


def _print_summary(
    results: list[LaunchIntelligenceObject],
    output_path: str,
    mcp_calls_used: int,
) -> None:
    divider = "═" * 44
    print(f"\n{divider}")
    print("LAUNCH SNIPER — FINAL RESULTS")
    print(divider)

    if results:
        print(f"{'Competitor':<22} | {'Product':<16} | Confidence")
        for r in results:
            product = (r.suspected_product or "Unknown")[:16]
            pct     = f"{r.confidence * 100:.0f}%"
            print(f"{r.competitor:<22} | {product:<16} | {pct}")
    else:
        print("  (no launches detected)")

    print(divider)
    print(f"Report saved to: {output_path}")
    print(f"MCP calls used:  {mcp_calls_used}/100")
    print(divider + "\n")


# ── Stage 1 helpers ───────────────────────────────────────────────────────────


def _count_by_source(signals: list[Signal], source: str) -> int:
    return sum(1 for s in signals if s.source == source)


# ── Pipeline ──────────────────────────────────────────────────────────────────


async def run_pipeline(
    domains: list[str],
    output_path: str,
    send_slack: bool,
) -> tuple[list[LaunchIntelligenceObject], int]:
    """
    Execute all six stages in order.
    Returns (results, mcp_calls_used) even if a later stage fails.
    """
    results: list[LaunchIntelligenceObject] = []
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")

    # ── Initialise MCP client (shared across WHOIS + robots collectors) ───────
    mcp_client: BrightDataMCPClient | None = None
    try:
        mcp_client = BrightDataMCPClient()
    except ValueError as exc:
        print(f"⚠️  Bright Data MCP unavailable: {exc}")
        print("   WHOIS and Robots.txt collectors will be skipped.")

    # ── STAGE 1 — Collect signals ─────────────────────────────────────────────
    print("\nStage 1 — Collecting signals...")

    whois_signals:   list[Signal] = []
    tm_signals:      list[Signal] = []
    robots_signals:  list[Signal] = []

    try:
        collect_coros = [TrademarkCollector().collect(domains)]

        if mcp_client:
            collect_coros.insert(0, WhoisCollector(mcp_client).collect(domains))
            collect_coros.append(RobotsTxtCollector(mcp_client).collect(domains))

        gathered = await asyncio.gather(*collect_coros, return_exceptions=True)

        if mcp_client:
            whois_r, tm_r, robots_r = gathered
        else:
            tm_r = gathered[0]
            whois_r = robots_r = []

        whois_signals  = whois_r  if isinstance(whois_r,  list) else []
        tm_signals     = tm_r     if isinstance(tm_r,     list) else []
        robots_signals = robots_r if isinstance(robots_r, list) else []

        if isinstance(whois_r,  Exception): logger.error("[S1] WHOIS error: %s",     whois_r)
        if isinstance(tm_r,     Exception): logger.error("[S1] Trademark error: %s", tm_r)
        if isinstance(robots_r, Exception): logger.error("[S1] Robots error: %s",    robots_r)

    except Exception as exc:
        logger.error("[S1] Collection failed: %s", exc)
        print(f"⚠️  Stage 1 error: {exc}")

    all_signals = whois_signals + tm_signals + robots_signals
    n = len(all_signals)

    print(f"✅ Stage 1 complete — {n} signal(s) collected")
    print(f"   WHOIS:      {len(whois_signals)} signal(s)")
    print(f"   Trademark:  {len(tm_signals)} signal(s)")
    print(f"   Robots.txt: {len(robots_signals)} signal(s)")

    # ── STAGE 2 — MCP budget report ───────────────────────────────────────────
    mcp_used      = mcp_client.calls_used      if mcp_client else 0
    mcp_remaining = mcp_client.calls_remaining if mcp_client else 0
    print(f"\nStage 2 — Bright Data MCP calls used: {mcp_used}")
    print(f"Stage 2 — Budget remaining: {mcp_remaining} calls")

    if not all_signals:
        print("\n✅ No launch signals detected for monitored domains")
        return results, mcp_used

    # ── STAGE 3 — Parse signals ───────────────────────────────────────────────
    print("\nStage 3 — Parsing and enriching signals...")
    try:
        enriched_signals = await SniperParserOrchestrator().parse(all_signals)
        print(f"✅ Stage 3 complete — {len(enriched_signals)} signal(s) enriched")
    except Exception as exc:
        logger.error("[S3] Parser failed: %s", exc)
        print(f"⚠️  Stage 3 error: {exc} — using raw signals")
        enriched_signals = all_signals

    # ── STAGE 4 — Correlate signals ───────────────────────────────────────────
    print("\nStage 4 — Correlating signals...")
    clusters: list[dict] = []
    try:
        clusters = SniperCorrelationEngine().correlate(enriched_signals)
        print(f"✅ Stage 4 complete — {len(clusters)} cluster(s) found")
    except Exception as exc:
        logger.error("[S4] Correlation failed: %s", exc)
        print(f"⚠️  Stage 4 error: {exc}")

    if not clusters:
        print("\n✅ No launch signals detected for monitored domains")
        return results, mcp_used

    # ── STAGE 5 — AI enrichment ───────────────────────────────────────────────
    print("\nStage 5 — Running AI enrichment...")
    try:
        results = await LaunchEnricher().enrich(clusters)
        print(f"✅ Stage 5 complete — {len(results)} launch(es) detected")
    except ValueError as exc:
        # Missing OPENAI_API_KEY — build minimal objects from clusters so the
        # report still has something useful
        logger.error("[S5] LaunchEnricher init failed: %s", exc)
        print(f"⚠️  Stage 5 skipped ({exc}) — building minimal results from clusters")
        results = _clusters_to_minimal_objects(clusters)
    except Exception as exc:
        logger.error("[S5] Enrichment failed: %s", exc)
        print(f"⚠️  Stage 5 error: {exc} — building minimal results from clusters")
        results = _clusters_to_minimal_objects(clusters)

    # ── STAGE 6 — Delivery ────────────────────────────────────────────────────
    print("\nStage 6 — Generating report...")
    report_markdown = ""
    try:
        rg = ReportGenerator()
        report_markdown = rg.generate(results)
        rg.save(results, output_path)
        print(f"✅ Stage 6 complete — report saved to {output_path}")
    except Exception as exc:
        logger.error("[S6] Report save failed: %s", exc)
        print(f"⚠️  Stage 6 report error: {exc}")

    # Persist signals + results to Supabase
    try:
        await DatabaseWriter().save_run(all_signals, results, run_id)
    except Exception as exc:
        logger.error("[S6] DB write failed: %s", exc)
        print(f"⚠️  DB persistence error: {exc}")

    # Email the report
    try:
        await EmailNotifier().send(results, report_markdown)
    except Exception as exc:
        logger.error("[S6] Email failed: %s", exc)
        print(f"⚠️  Email delivery error: {exc}")

    if send_slack:
        try:
            await SniperSlackNotifier().notify(results)
        except Exception as exc:
            logger.error("[S6] Slack notify failed: %s", exc)
            print(f"⚠️  Slack notification error: {exc}")

    return results, mcp_used


def _clusters_to_minimal_objects(
    clusters: list[dict],
) -> list[LaunchIntelligenceObject]:
    """Fallback: convert raw clusters into LaunchIntelligenceObjects without AI."""
    from models.launch_intelligence import CounterPlaybook
    objects = []
    for c in clusters:
        signals = c.get("signals", [])
        objects.append(LaunchIntelligenceObject(
            competitor=c["competitor_domain"],
            suspected_product=c.get("suspected_product"),
            launch_signals=[
                f"{s.signal_type.replace('_', ' ').title()} [{s.source}]"
                for s in signals
            ],
            confidence=c["confidence"],
            signal_count=c["signal_count"],
            signal_sources=c["signal_sources"],
            counter_playbook=CounterPlaybook(),
        ))
    return objects


# ── Entry point ───────────────────────────────────────────────────────────────


async def main() -> None:
    args = _parse_args()
    _configure_logging(args.quiet)

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    if not domains:
        print("❌ No valid domains supplied. Use --domains acme.com,rival.io")
        sys.exit(1)

    print("🚀 Launch Sniper starting...")
    print(f"📡 Monitoring: {', '.join(domains)}")

    results: list[LaunchIntelligenceObject] = []
    mcp_used = 0
    try:
        results, mcp_used = await run_pipeline(
            domains=domains,
            output_path=args.output,
            send_slack=args.slack,
        )
    except Exception as exc:
        logger.error("Pipeline crashed: %s", exc, exc_info=True)
        print(f"\n❌ Pipeline error: {exc}")
        # Best-effort report with whatever we have
        if results:
            try:
                ReportGenerator().save(results, args.output)
                print(f"⚠️  Partial report saved to {args.output}")
            except Exception:
                pass

    _print_summary(results, args.output, mcp_used)


if __name__ == "__main__":
    asyncio.run(main())
