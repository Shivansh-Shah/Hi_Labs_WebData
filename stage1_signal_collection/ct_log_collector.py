"""
stage1_signal_collection/ct_log_collector.py
---------------------------------------------
Certificate Transparency log collector — FULLY FREE.

Two modes (both completely free):

  1. STREAMING — certstream WebSocket (wss://certstream.calidog.io/)
     Real-time feed of every SSL cert issued globally.
     Filter for target domains. Zero cost.

  2. POLLING — crt.sh REST API (free, no key required)
     Historical certs for any domain. Direct request first,
     Bright Data fallback only if crt.sh blocks us (rare).

Additionally extracts subdomain intelligence from SAN entries —
every cert is a DNS enumeration goldmine.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable, Iterable

import certstream  # type: ignore
import httpx

from models.signal import Signal, SignalSource, SignalType
from config.settings import get_settings
from stage2_bright_data.web_unlocker import smart_fetch

logger = logging.getLogger(__name__)
settings = get_settings()

_CERTSTREAM_URL = "wss://certstream.calidog.io/"
_CRT_SH_URL = "https://crt.sh/"


# ── Domain matching ───────────────────────────────────────────────────────────


def _match_target(domain: str, target_domains: set[str]) -> str | None:
    """
    Return the canonical target domain if `domain` is or is a subdomain of any target.
    "crm.acme.com"  → "acme.com"   ✓
    "acme.com.evil" → None          ✗ (prefix attack prevention)
    """
    domain = domain.lstrip("*.").lower().rstrip(".")
    for target in target_domains:
        t = target.lower().rstrip(".")
        if domain == t or domain.endswith("." + t):
            return target
    return None


def _infer_vendor_hint(subdomain: str) -> str | None:
    """Quick lookup of subdomain prefix → likely vendor."""
    prefix = subdomain.split(".")[0].lower()
    _TABLE = {
        "analytics": "Amplitude/Mixpanel",
        "crm": "Salesforce/HubSpot",
        "salesforce": "Salesforce",
        "hubspot": "HubSpot",
        "marketo": "Marketo",
        "pardot": "Salesforce Pardot",
        "stripe": "Stripe",
        "payments": "Stripe/Braintree",
        "workday": "Workday",
        "okta": "Okta",
        "auth": "Okta/Auth0",
        "sso": "Okta/OneLogin",
        "zendesk": "Zendesk",
        "intercom": "Intercom",
        "datadog": "Datadog",
        "newrelic": "New Relic",
        "segment": "Segment",
        "snowflake": "Snowflake",
        "slack": "Slack",
        "jira": "Atlassian Jira",
        "statuspage": "Atlassian Statuspage",
        "pagerduty": "PagerDuty",
        "cloudflare": "Cloudflare",
        "looker": "Looker/Google",
        "tableau": "Tableau",
        "databricks": "Databricks",
    }
    return _TABLE.get(prefix)


# ── certstream callback ───────────────────────────────────────────────────────


def _parse_certstream_message(
    message: dict,
    target_domains: set[str],
    on_signal: Callable[[Signal], None],
) -> None:
    if message.get("message_type") != "certificate_update":
        return

    leaf = message.get("data", {}).get("leaf_cert", {})
    all_domains: list[str] = leaf.get("all_domains", [])
    issued_at_ts: float | None = message.get("data", {}).get("seen")

    timestamp = (
        datetime.fromtimestamp(issued_at_ts, tz=timezone.utc)
        if issued_at_ts
        else datetime.now(timezone.utc)
    )

    for domain in all_domains:
        matched_target = _match_target(domain, target_domains)
        if not matched_target:
            continue

        clean = domain.lstrip("*.")
        is_wildcard = domain.startswith("*.")
        signal_type = SignalType.NEW_WILDCARD_CERT if is_wildcard else SignalType.NEW_CERT

        # Subdomains with specific prefixes are much higher confidence
        vendor_hint = _infer_vendor_hint(clean)
        confidence = 0.85 if vendor_hint else (0.80 if is_wildcard else 0.65)

        signal = Signal(
            source=SignalSource.CT_LOG,
            target_domain=matched_target,
            signal_type=signal_type,
            timestamp=timestamp,
            raw_data={
                "cert_domain": domain,
                "all_domains": all_domains,
                "issuer": leaf.get("issuer", {}),
                "subject": leaf.get("subject", {}),
                "serial_number": leaf.get("serial_number"),
                "fingerprint": leaf.get("fingerprint"),
                "not_before": leaf.get("not_before"),
                "not_after": leaf.get("not_after"),
                "san_count": len(all_domains),
            },
            confidence=confidence,
            vendor_hint=vendor_hint,
            metadata={"mode": "certstream_live", "san_count": len(all_domains)},
        )
        logger.info(
            "[CT-STREAM] %s → %s | target=%s | vendor=%s",
            domain,
            signal_type.value,
            matched_target,
            vendor_hint or "unknown",
        )
        on_signal(signal)


# ── Streaming collector (100% free) ──────────────────────────────────────────


class CTLogStreamingCollector:
    """
    Real-time cert stream via certstream WebSocket. Completely free.
    Reconnects automatically on drop.

    Usage
    -----
        collector = CTLogStreamingCollector(
            target_domains={"acme.com"},
            on_signal=my_handler,
        )
        await collector.run()
    """

    def __init__(
        self,
        target_domains: Iterable[str],
        on_signal: Callable[[Signal], None],
        certstream_url: str = _CERTSTREAM_URL,
    ) -> None:
        self.target_domains: set[str] = set(target_domains)
        self.on_signal = on_signal
        self.certstream_url = certstream_url
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info(
            "[CT-STREAM] Starting live feed for %d domains (FREE certstream)",
            len(self.target_domains),
        )
        while self._running:
            try:
                await asyncio.to_thread(self._blocking_listen)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[CT-STREAM] Connection error: %s — reconnecting in 15s", exc)
                await asyncio.sleep(15)

    def _blocking_listen(self) -> None:
        def _cb(message, context):  # noqa: ANN001
            if not self._running:
                raise SystemExit("Stopping certstream")
            try:
                _parse_certstream_message(message, self.target_domains, self.on_signal)
            except SystemExit:
                raise
            except Exception as exc:
                logger.debug("[CT-STREAM] Parse error: %s", exc)

        certstream.listen_for_events(_cb, url=self.certstream_url)

    def stop(self) -> None:
        self._running = False


# ── Polling collector (crt.sh — free, BD fallback if blocked) ────────────────


class CTLogPollingCollector:
    """
    Poll crt.sh for all certificates issued to target domains.

    crt.sh is free and requires no API key. Direct HTTP is tried first.
    Bright Data Web Unlocker is used as a fallback only if crt.sh blocks us
    (extremely rare — crt.sh is a public service).

    Deduplication: only returns certs issued within `since_days` window.

    Usage
    -----
        collector = CTLogPollingCollector(["acme.com", "globex.io"])
        signals = await collector.collect_all()
    """

    def __init__(
        self,
        target_domains: Iterable[str],
        since_days: int = 30,
    ) -> None:
        self.target_domains = list(target_domains)
        self.since_days = since_days
        self._cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    async def collect_domain(self, domain: str) -> list[Signal]:
        url = f"{_CRT_SH_URL}?q=%.{domain}&output=json"
        signals: list[Signal] = []

        # Direct fetch first (free). smart_fetch auto-falls back to BD if blocked.
        data = await smart_fetch(url, json_response=True)

        if not isinstance(data, list):
            logger.warning("[CT-POLL] Unexpected response for %s: %s", domain, type(data))
            return []

        seen_ids: set[str] = set()
        for cert in data:
            try:
                cert_id = str(cert.get("id", ""))
                if cert_id in seen_ids:
                    continue
                seen_ids.add(cert_id)

                name_value: str = cert.get("name_value", "")
                issued_at_str: str = cert.get("entry_timestamp", cert.get("not_before", ""))
                issuer_name: str = cert.get("issuer_name", "")

                try:
                    ts = datetime.fromisoformat(issued_at_str.replace("Z", "+00:00"))
                except Exception:
                    ts = datetime.now(timezone.utc)

                # Skip certs outside our diff window
                if ts < self._cutoff:
                    continue

                for cn in name_value.split("\n"):
                    cn = cn.strip()
                    if not cn:
                        continue

                    clean = cn.lstrip("*.")
                    is_wildcard = cn.startswith("*.")
                    signal_type = (
                        SignalType.NEW_WILDCARD_CERT if is_wildcard else SignalType.NEW_CERT
                    )
                    vendor_hint = _infer_vendor_hint(clean)
                    confidence = 0.82 if vendor_hint else (0.72 if is_wildcard else 0.60)

                    signal = Signal(
                        source=SignalSource.CT_LOG,
                        target_domain=domain,
                        signal_type=signal_type,
                        timestamp=ts,
                        raw_data={
                            "cert_id": cert_id,
                            "cert_domain": cn,
                            "issuer_name": issuer_name,
                            "serial_number": cert.get("serial_number"),
                            "not_before": cert.get("not_before"),
                            "not_after": cert.get("not_after"),
                        },
                        confidence=confidence,
                        vendor_hint=vendor_hint,
                        metadata={"mode": "crt_sh_polling", "source_url": url},
                    )
                    signals.append(signal)

            except Exception as exc:
                logger.debug("[CT-POLL] Skipping entry for %s: %s", domain, exc)

        logger.info("[CT-POLL] %s → %d cert signals (last %dd)", domain, len(signals), self.since_days)
        return signals

    async def collect_all(self) -> list[Signal]:
        results = await asyncio.gather(
            *[self.collect_domain(d) for d in self.target_domains],
            return_exceptions=True,
        )
        out: list[Signal] = []
        for r in results:
            if isinstance(r, list):
                out.extend(r)
            else:
                logger.error("[CT-POLL] Error: %s", r)
        return out


# ── Convenience async generator ───────────────────────────────────────────────


async def stream_ct_signals(target_domains: Iterable[str]) -> AsyncIterator[Signal]:
    """Async generator wrapper around CTLogStreamingCollector."""
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    collector = CTLogStreamingCollector(
        target_domains=target_domains,
        on_signal=queue.put_nowait,
    )
    task = asyncio.create_task(collector.run())
    try:
        while True:
            yield await queue.get()
    finally:
        collector.stop()
        task.cancel()
