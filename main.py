#!/usr/bin/env python3
"""
main.py
-------
Entry point for the GTM Intelligence Platform — Stages 1 & 2.

Usage
-----
# Run once (batch mode) for specific domains:
    python main.py --domains acme.com globex.io --github-orgs salesforce

# Stream mode (continuous monitoring):
    python main.py --domains acme.com --stream

# Agent mode (Claude-driven investigation):
    python main.py --domains acme.com --agent

# Product launch sniper (Module 2):
    python main.py --domains competitor.com --launch-sniper

# Output as JSON to file:
    python main.py --domains acme.com --output signals.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.runner import PipelineRunner
from models.signal import Signal
from stage2_bright_data.mcp_orchestrator import MCPAgentOrchestrator

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("gtm_pipeline.log"),
    ],
)
logger = logging.getLogger("main")


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GTM Intelligence Platform — Signal Collection (Stages 1 & 2)"
    )
    parser.add_argument(
        "--domains",
        nargs="+",
        required=True,
        metavar="DOMAIN",
        help="Apex domain(s) to monitor (e.g. acme.com globex.io)",
    )
    parser.add_argument(
        "--github-orgs",
        nargs="*",
        default=[],
        metavar="ORG",
        help="GitHub org handles to watch for commit spikes and new repos",
    )
    parser.add_argument(
        "--customer-names",
        nargs="*",
        default=[],
        metavar="NAME",
        help="Customer company names to search for in GitHub commits",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Run in continuous streaming mode (long-lived process)",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Use Claude MCP agent for autonomous investigation",
    )
    parser.add_argument(
        "--launch-sniper",
        action="store_true",
        help="Run Module 2: product launch detection mode",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write signals as JSON to this file (in addition to stdout)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser.parse_args()


# ── Signal printer ────────────────────────────────────────────────────────────


def print_signal(signal: Signal) -> None:
    tier_emoji = {
        "ct_log": "🔐",
        "dns": "🌐",
        "wayback": "📸",
        "github": "⚙️ ",
        "whois": "📋",
        "robots_txt": "🤖",
        "trademark": "™️ ",
    }
    emoji = tier_emoji.get(signal.source.value, "📡")
    confidence_bar = "█" * int(signal.confidence * 10) + "░" * (10 - int(signal.confidence * 10))

    print(
        f"\n{emoji} [{signal.source.value.upper()}] {signal.signal_type.value}"
        f"\n   Target  : {signal.target_domain}"
        f"\n   Time    : {signal.timestamp.strftime('%Y-%m-%d %H:%M UTC')}"
        f"\n   Confidence: [{confidence_bar}] {signal.confidence:.0%}"
        + (f"\n   Vendor  : {signal.vendor_hint}" if signal.vendor_hint else "")
        + f"\n   ID      : {signal.signal_id}"
    )


# ── Main entrypoint ───────────────────────────────────────────────────────────


async def main() -> None:
    args = parse_args()

    # Adjust log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    print("\n" + "═" * 60)
    print("  🔭 GTM Intelligence Platform — Stage 1 & 2")
    print("═" * 60)
    print(f"  Domains   : {', '.join(args.domains)}")
    print(f"  GH Orgs   : {', '.join(args.github_orgs) or 'none'}")
    print(f"  Mode      : {'STREAM' if args.stream else 'AGENT' if args.agent else 'BATCH'}")
    print("═" * 60 + "\n")

    all_signals: list[Signal] = []

    # ── Module 2: Product launch sniper ──────────────────────────────────────
    if args.launch_sniper:
        logger.info("Running Module 2: Product Launch Sniper")
        agent = MCPAgentOrchestrator()
        for domain in args.domains:
            signals = await agent.detect_product_launch(domain)
            all_signals.extend(signals)
            for s in signals:
                print_signal(s)

    # ── Standard pipeline run ─────────────────────────────────────────────────
    elif args.stream:
        # Stream mode: long-running
        def on_signal(s: Signal) -> None:
            print_signal(s)
            all_signals.append(s)

        runner = PipelineRunner(
            target_domains=args.domains,
            github_orgs=args.github_orgs,
            customer_names=args.customer_names,
            on_signal=on_signal,
            enable_ct_streaming=True,
            enable_agent=args.agent,
        )
        await runner.run_stream()

    else:
        # Batch / once mode
        runner = PipelineRunner(
            target_domains=args.domains,
            github_orgs=args.github_orgs,
            customer_names=args.customer_names,
            enable_ct_streaming=False,
            enable_agent=args.agent,
        )
        all_signals = await runner.run_once()
        for s in all_signals:
            print_signal(s)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print(f"  ✅ Collection complete — {len(all_signals)} signals detected")
    if all_signals:
        by_source = {}
        for s in all_signals:
            by_source[s.source.value] = by_source.get(s.source.value, 0) + 1
        for source, count in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"     {source:20s}: {count} signals")
    print("═" * 60 + "\n")

    # ── Write JSON output ─────────────────────────────────────────────────────
    if args.output and all_signals:
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "domains_monitored": args.domains,
                    "signal_count": len(all_signals),
                    "signals": [s.to_dict() for s in all_signals],
                },
                indent=2,
            )
        )
        logger.info("Signals written to %s", output_path)


if __name__ == "__main__":
    asyncio.run(main())
