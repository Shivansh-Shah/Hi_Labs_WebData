"""
Shared email helper — Resend SDK over HTTPS.
Uses RESEND_API_KEY from environment.
Free plan: sends from onboarding@resend.dev to your own registered email.
"""
from __future__ import annotations

import logging
import os

import resend

logger = logging.getLogger(__name__)


def send_email(*, to: str, subject: str, html: str) -> dict:
    """
    Send one transactional email via Resend.
    Returns {"sent": True, "message_id": "..."}.
    Raises ValueError if RESEND_API_KEY is missing, RuntimeError on API error.
    """
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        raise ValueError(
            "RESEND_API_KEY not set. Add it to your Render environment variables."
        )

    resend.api_key = api_key

    sender = os.getenv("RESEND_FROM", "GTM Intel <onboarding@resend.dev>")

    try:
        params: resend.Emails.SendParams = {
            "from":    sender,
            "to":      [to],
            "subject": subject,
            "html":    html,
        }
        result = resend.Emails.send(params)
        logger.info("[RESEND] Sent to %s — id=%s", to, result.get("id"))
        return {"sent": True, "message_id": result.get("id", "")}
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
