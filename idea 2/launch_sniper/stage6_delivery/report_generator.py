# Report generator — produces structured JSON and markdown reports from enriched launch clusters.
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from models.launch_intelligence import LaunchIntelligenceObject

logger = logging.getLogger(__name__)


# ── Section builders ──────────────────────────────────────────────────────────


def _header(results: list[LaunchIntelligenceObject]) -> str:
    now        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total      = len(results)
    detected   = sum(1 for r in results if r.signal_count > 0)
    high_conf  = sum(1 for r in results if r.confidence >= 0.8)

    return (
        f"# 🚨 Launch Sniper Report\n"
        f"Generated: {now}  \n"
        f"Competitors Monitored: {total}  \n"
        f"Launches Detected: {detected}  \n"
        f"High Confidence (>80%): {high_conf}  \n"
    )


def _summary_table(results: list[LaunchIntelligenceObject]) -> str:
    rows: list[str] = []
    for r in results:
        product    = r.suspected_product or "Unknown"
        pct        = f"{r.confidence * 100:.0f}%"
        launch     = r.estimated_launch_date or "Unknown"
        top_seo    = r.counter_playbook.seo[0] if r.counter_playbook.seo else "—"
        # Truncate long cells so the table stays readable
        top_seo    = (top_seo[:60] + "…") if len(top_seo) > 60 else top_seo
        product_td = (product[:40] + "…") if len(product) > 40 else product
        rows.append(
            f"| {r.competitor} | {product_td} | {pct} | {launch} | {top_seo} |"
        )

    header = (
        "| Competitor | Suspected Product | Confidence | Est. Launch | Top SEO Action |\n"
        "|---|---|---|---|---|"
    )
    body = "\n".join(rows) if rows else "| — | — | — | — | — |"
    return f"## Summary Table\n{header}\n{body}\n"


def _detail_section(obj: LaunchIntelligenceObject) -> str:
    product = obj.suspected_product or "Unknown Product"
    pct     = f"{obj.confidence * 100:.0f}"
    launch  = obj.estimated_launch_date or "Unknown"

    evidence = (
        "\n".join(f"- {s}" for s in obj.launch_signals)
        if obj.launch_signals else "- (no signals recorded)"
    )

    seo_actions = (
        "\n".join(f"- {a}" for a in obj.counter_playbook.seo)
        if obj.counter_playbook.seo else "- (none)"
    )
    content_actions = (
        "\n".join(f"- {a}" for a in obj.counter_playbook.content)
        if obj.counter_playbook.content else "- (none)"
    )
    sales_actions = (
        "\n".join(f"- {a}" for a in obj.counter_playbook.sales)
        if obj.counter_playbook.sales else "- (none)"
    )

    return (
        f"### {obj.competitor} — {product}\n"
        f"**Confidence:** {pct}%  \n"
        f"**Estimated Launch:** {launch}  \n"
        f"**Signals Detected:** {obj.signal_count}  \n"
        f"\n"
        f"**Evidence:**\n"
        f"{evidence}\n"
        f"\n"
        f"**Counter-Playbook:**\n"
        f"\n"
        f"**SEO Actions:**\n"
        f"{seo_actions}\n"
        f"\n"
        f"**Content Actions:**\n"
        f"{content_actions}\n"
        f"\n"
        f"**Sales Actions:**\n"
        f"{sales_actions}\n"
    )


# ── Generator ─────────────────────────────────────────────────────────────────


class ReportGenerator:
    """
    Produces a structured markdown report from a list of LaunchIntelligenceObjects.

    generate() — returns the report as a string (no I/O).
    save()     — calls generate() and writes to disk, creating
                 the output directory if it doesn't exist.
    """

    def generate(self, results: list[LaunchIntelligenceObject]) -> str:
        sections: list[str] = [
            _header(results),
            "---",
            _summary_table(results),
            "---",
            "## Detailed Intelligence",
            "",
        ]

        if results:
            for obj in results:
                sections.append(_detail_section(obj))
                sections.append("---")
        else:
            sections.append("*No launch intelligence objects to report.*")

        return "\n".join(sections)

    def save(
        self,
        results: list[LaunchIntelligenceObject],
        output_path: str = "idea2/output/launch_report.md",
    ) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        report = self.generate(results)
        path.write_text(report, encoding="utf-8")

        logger.info("[REPORT] Saved %d result(s) to %s", len(results), path.resolve())
        print(f"[REPORT] ✓ Report saved → {path.resolve()}")
