# Email notifier — sends the launch report via Resend API with Gmail SMTP fallback.
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from dotenv import load_dotenv

from models.launch_intelligence import LaunchIntelligenceObject

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent.parent / ".env")

_RESEND_API = "https://api.resend.com/emails"
_FROM_RESEND = "onboarding@resend.dev"
_TIMEOUT = 15.0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_subject(results: list[LaunchIntelligenceObject]) -> str:
    high = sum(1 for r in results if r.confidence >= 0.8)
    if high:
        return f"Launch Sniper — {high} high-confidence detection(s)"
    return f"Launch Sniper Report — {len(results)} competitor(s) analysed"


def _md_to_html(md: str) -> str:
    """Minimal markdown → HTML conversion for email rendering."""
    h = md
    h = re.sub(r"^### (.+)$", r"<h3>\1</h3>", h, flags=re.MULTILINE)
    h = re.sub(r"^## (.+)$",  r"<h2>\1</h2>", h, flags=re.MULTILINE)
    h = re.sub(r"^# (.+)$",   r"<h1>\1</h1>", h, flags=re.MULTILINE)
    h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
    h = re.sub(r"^\|[-| ]+\|$", "", h, flags=re.MULTILINE)  # table separator rows
    h = re.sub(r"^---$", "<hr>", h, flags=re.MULTILINE)
    h = re.sub(r"^- (.+)$", r"<li>\1</li>", h, flags=re.MULTILINE)
    h = h.replace("\n", "<br>\n")
    return (
        "<html><body style='font-family:monospace;max-width:820px;"
        "line-height:1.5;padding:16px'>"
        f"{h}"
        "</body></html>"
    )


# ── Notifier ──────────────────────────────────────────────────────────────────


class EmailNotifier:
    """
    Delivers the launch intelligence markdown report by email.

    Delivery chain
    --------------
    1. Resend REST API  (RESEND_API_KEY)
    2. Gmail SMTP       (GMAIL_USER + GMAIL_APP_PASSWORD) — fallback
    3. Console print    — if neither method is configured

    Recipient is REPORT_EMAIL if set, else GMAIL_USER.
    Sends ALL results every run so the analyst has full context.
    """

    def __init__(self) -> None:
        self._resend_key  = os.getenv("RESEND_API_KEY", "")
        self._gmail_user  = os.getenv("GMAIL_USER", "")
        # Google app passwords are shown with spaces — strip them for SMTP login
        self._gmail_pass  = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
        self._to_email    = os.getenv("REPORT_EMAIL") or self._gmail_user

    async def send(
        self,
        results: list[LaunchIntelligenceObject],
        report_markdown: str,
    ) -> None:
        if not self._to_email:
            logger.info("[EMAIL] No recipient configured — skipping")
            print("[EMAIL] No REPORT_EMAIL configured — skipping email delivery")
            return

        subject  = _build_subject(results)
        html     = _md_to_html(report_markdown)
        sent     = False

        if self._resend_key:
            sent = await self._send_resend(subject, html, report_markdown)

        if not sent and self._gmail_user and self._gmail_pass:
            sent = self._send_gmail(subject, html, report_markdown)

        if not sent:
            print("[EMAIL] No delivery method available — printing report to console")
            print(report_markdown)

    # ── Resend ────────────────────────────────────────────────────────────────

    async def _send_resend(self, subject: str, html: str, text: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _RESEND_API,
                    headers={
                        "Authorization": f"Bearer {self._resend_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from":    _FROM_RESEND,
                        "to":      [self._to_email],
                        "subject": subject,
                        "html":    html,
                        "text":    text,
                    },
                )
            if resp.status_code in (200, 201):
                logger.info("[EMAIL] Resend OK → %s", self._to_email)
                print(f"[EMAIL] ✓ Report sent via Resend → {self._to_email}")
                return True
            else:
                logger.warning(
                    "[EMAIL] Resend HTTP %d: %s", resp.status_code, resp.text[:200]
                )
                print(
                    f"[EMAIL] Resend returned HTTP {resp.status_code} "
                    "— falling back to Gmail SMTP"
                )
                return False
        except Exception as exc:
            logger.error("[EMAIL] Resend failed: %s", exc)
            print(f"[EMAIL] Resend error: {exc} — trying Gmail SMTP")
            return False

    # ── Gmail SMTP ────────────────────────────────────────────────────────────

    def _send_gmail(self, subject: str, html: str, text: str) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = self._gmail_user
            msg["To"]      = self._to_email
            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html",  "utf-8"))

            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
                smtp.login(self._gmail_user, self._gmail_pass)
                smtp.sendmail(self._gmail_user, self._to_email, msg.as_string())

            logger.info("[EMAIL] Gmail SMTP OK → %s", self._to_email)
            print(f"[EMAIL] ✓ Report sent via Gmail SMTP → {self._to_email}")
            return True
        except Exception as exc:
            logger.error("[EMAIL] Gmail SMTP failed: %s", exc)
            print(f"[EMAIL] Gmail SMTP error: {exc}")
            return False
