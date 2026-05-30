"""
Shared email helper — Brevo Transactional Email API over HTTPS.
Uses BREVO_API_KEY + BREVO_SENDER_EMAIL from environment.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def send_email(*, to: str, subject: str, html: str) -> dict:
    """
    Send one transactional email via Brevo.
    Returns Resend-style dict: {"sent": True, "message_id": "..."}.
    Raises ValueError if env vars are missing, RuntimeError on API error.
    """
    api_key      = os.getenv("BREVO_API_KEY", "")
    sender_email = os.getenv("BREVO_SENDER_EMAIL", "")
    sender_name  = os.getenv("BREVO_SENDER_NAME", "GTM Intel")

    if not api_key:
        raise ValueError(
            "BREVO_API_KEY not set. "
            "Add it to .env (and Render env vars) after signing up at brevo.com."
        )
    if not sender_email:
        raise ValueError(
            "BREVO_SENDER_EMAIL not set. "
            "Set it to the Gmail address you verified as a sender in Brevo."
        )

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to":     [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {
        "api-key":      api_key,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=20) as client:
        resp = client.post(_BREVO_URL, json=payload, headers=headers)

    if resp.status_code not in (200, 201):
        body = resp.text[:300]
        logger.error("[BREVO] %s — %s", resp.status_code, body)
        raise RuntimeError(f"Brevo API returned {resp.status_code}: {body}")

    data = resp.json()
    logger.info("[BREVO] Sent to %s — messageId=%s", to, data.get("messageId"))
    return {"sent": True, "message_id": data.get("messageId", "")}
