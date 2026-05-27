# Slack notifier — posts launch intelligence alerts to a Slack webhook.
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from models.launch_intelligence import LaunchIntelligenceObject

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent.parent / ".env")

_HIGH_CONFIDENCE_THRESHOLD = 0.8
_TIMEOUT = 10.0


# ── Message formatter ─────────────────────────────────────────────────────────


def _format_message(obj: LaunchIntelligenceObject) -> str:
    pct = f"{obj.confidence * 100:.0f}"
    product = obj.suspected_product or "Unknown"
    launch  = obj.estimated_launch_date or "Unknown"

    signal_lines  = "\n".join(f"• {s}" for s in obj.launch_signals) or "• (none)"
    seo_lines     = "\n".join(f"• {a}" for a in obj.counter_playbook.seo)     or "• (none)"
    content_lines = "\n".join(f"• {a}" for a in obj.counter_playbook.content) or "• (none)"
    sales_lines   = "\n".join(f"• {a}" for a in obj.counter_playbook.sales)   or "• (none)"

    return (
        f"🚨 COMPETITOR LAUNCH DETECTED\n"
        f"\n"
        f"🏢 Competitor: {obj.competitor}\n"
        f"📦 Suspected Product: {product}\n"
        f"📊 Confidence: {pct}%\n"
        f"📅 Estimated Launch: {launch}\n"
        f"\n"
        f"📡 Signals Detected:\n"
        f"{signal_lines}\n"
        f"\n"
        f"🎯 Counter-Playbook:\n"
        f"SEO:\n{seo_lines}\n"
        f"Content:\n{content_lines}\n"
        f"Sales:\n{sales_lines}"
    )


# ── Notifier ──────────────────────────────────────────────────────────────────


class SniperSlackNotifier:
    """
    Sends Slack webhook alerts for high-confidence launch detections.

    Behaviour
    ---------
    - Only fires for results where confidence >= 0.8.
    - If SLACK_WEBHOOK_URL is absent or a placeholder, prints to console
      instead — pipeline never crashes due to missing Slack config.
    - Uses httpx for the POST; any network failure is caught and logged.
    """

    def __init__(self) -> None:
        url = os.getenv("SLACK_WEBHOOK_URL", "")
        self._webhook_url: str | None = (
            url if url and url != "your_webhook_here" else None
        )
        if not self._webhook_url:
            logger.info(
                "[SLACK] SLACK_WEBHOOK_URL not configured — will print alerts to console"
            )

    async def notify(self, results: list[LaunchIntelligenceObject]) -> None:
        high_conf = [r for r in results if r.confidence >= _HIGH_CONFIDENCE_THRESHOLD]

        if not high_conf:
            print(
                f"[SLACK] No high-confidence results (>= {_HIGH_CONFIDENCE_THRESHOLD:.0%}) "
                "to notify about"
            )
            return

        print(f"[SLACK] Sending {len(high_conf)} alert(s) ...")

        for obj in high_conf:
            message = _format_message(obj)
            if self._webhook_url:
                await self._post_to_slack(obj.competitor, message)
            else:
                self._print_to_console(obj.competitor, message)

    async def _post_to_slack(self, competitor: str, message: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    self._webhook_url,
                    json={"text": message},
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    logger.info("[SLACK] Alert sent for %s (HTTP 200)", competitor)
                    print(f"[SLACK] ✓ Alert sent for {competitor}")
                else:
                    logger.warning(
                        "[SLACK] Unexpected HTTP %d for %s: %s",
                        resp.status_code, competitor, resp.text[:200],
                    )
                    print(
                        f"[SLACK] ✗ HTTP {resp.status_code} for {competitor} — "
                        "falling back to console"
                    )
                    self._print_to_console(competitor, message)
        except Exception as exc:
            logger.error("[SLACK] Webhook POST failed for %s: %s", competitor, exc)
            print(f"[SLACK] ✗ POST failed for {competitor}: {exc} — printing to console")
            self._print_to_console(competitor, message)

    @staticmethod
    def _print_to_console(competitor: str, message: str) -> None:
        separator = "─" * 60
        print(f"\n{separator}")
        print(f"[SLACK → CONSOLE] Alert for {competitor}:")
        print(separator)
        print(message)
        print(separator + "\n")
