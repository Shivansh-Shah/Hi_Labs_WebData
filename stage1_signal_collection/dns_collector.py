"""
stage1_signal_collection/dns_collector.py
------------------------------------------
DNS / subdomain enumeration — MAXIMUM FREE COVERAGE.

Free sources (tried in order, all merged):
  1. crt.sh SAN extraction    — mine certificate SANs for subdomains (free, no key)
  2. HackerTarget API         — free subdomain finder, 100 req/day
  3. BufferOver.run           — free passive DNS
  4. RapidDNS                 — free passive DNS / subdomain search
  5. AlienVault OTX           — free threat intel, massive subdomain DB
  6. SecurityTrails           — PAID optional (skip if no key)

Bright Data SERP is used SPARINGLY — only for high-value targets when
all free sources return fewer than MIN_FREE_SUBDOMAINS results.

All sources are run concurrently. Results are merged and deduped by FQDN.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

import httpx

from models.signal import Signal, SignalSource, SignalType
from config.settings import get_settings
from stage2_bright_data.web_unlocker import smart_fetch
from stage2_bright_data.budget_guard import BudgetGuard, BDRequestType

logger = logging.getLogger(__name__)
settings = get_settings()

# Minimum free results before falling back to paid SERP
_MIN_FREE_SUBDOMAINS = 5
# Known high-value subdomain prefixes (trigger higher confidence)
_HIGH_VALUE_PREFIXES = {
    "crm", "analytics", "salesforce", "hubspot", "marketo", "pardot",
    "stripe", "payments", "workday", "okta", "sso", "auth", "zendesk",
    "intercom", "datadog", "newrelic", "segment", "snowflake", "slack",
    "pagerduty", "tableau", "looker", "databricks", "fivetran",
}

_DIRECT_HEADERS = {
    "User-Agent": "GTM-Intel/1.0 (subdomain-enumeration)",
    "Accept": "application/json, text/plain, */*",
}


def _is_high_value(fqdn: str) -> bool:
    prefix = fqdn.split(".")[0].lower()
    return prefix in _HIGH_VALUE_PREFIXES


def _infer_vendor(fqdn: str) -> str | None:
    prefix = fqdn.split(".")[0].lower()
    _TABLE = {
        "crm": "Salesforce/HubSpot", "salesforce": "Salesforce",
        "hubspot": "HubSpot", "marketo": "Marketo", "pardot": "Salesforce Pardot",
        "analytics": "Amplitude/Mixpanel", "segment": "Segment",
        "stripe": "Stripe", "payments": "Stripe/Braintree",
        "workday": "Workday", "bamboo": "BambooHR",
        "okta": "Okta", "auth": "Okta/Auth0", "sso": "Okta/OneLogin",
        "zendesk": "Zendesk", "intercom": "Intercom", "support": "Zendesk/Intercom",
        "datadog": "Datadog", "newrelic": "New Relic",
        "snowflake": "Snowflake", "databricks": "Databricks",
        "slack": "Slack", "jira": "Atlassian Jira", "confluence": "Atlassian Confluence",
        "tableau": "Tableau", "looker": "Looker/Google",
        "fivetran": "Fivetran", "pagerduty": "PagerDuty",
        "statuspage": "Atlassian Statuspage", "cloudflare": "Cloudflare",
    }
    return _TABLE.get(prefix)


def _make_subdomain_signal(domain: str, fqdn: str, source: str) -> Signal:
    vendor = _infer_vendor(fqdn)
    high_value = _is_high_value(fqdn)
    confidence = 0.80 if vendor else (0.65 if high_value else 0.45)
    return Signal(
        source=SignalSource.DNS,
        target_domain=domain,
        signal_type=SignalType.NEW_SUBDOMAIN,
        timestamp=datetime.now(timezone.utc),
        raw_data={"fqdn": fqdn, "source": source},
        confidence=confidence,
        vendor_hint=vendor,
        metadata={"provider": source, "high_value": high_value},
    )


# ── Source 1: crt.sh SAN extraction (FREE, no key) ───────────────────────────


async def _from_crt_sh(domain: str, seen: set[str]) -> list[Signal]:
    """
    Extract all unique subdomains from certificate SAN entries on crt.sh.
    Completely free. Often yields the most comprehensive results.
    """
    url = f"https://crt.sh/?q=%.{domain}&output=json"
    signals: list[Signal] = []

    data = await smart_fetch(url, json_response=True, timeout=25.0)
    if not isinstance(data, list):
        return []

    pattern = re.compile(rf'([\w\-]+\.{re.escape(domain)})', re.I)
    for cert in data:
        name_value = cert.get("name_value", "")
        for match in pattern.finditer(name_value):
            fqdn = match.group(1).lower()
            if fqdn not in seen and fqdn != domain:
                seen.add(fqdn)
                signals.append(_make_subdomain_signal(domain, fqdn, "crt_sh"))

    logger.info("[DNS-CRTSH] %s → %d subdomains", domain, len(signals))
    return signals


# ── Source 2: HackerTarget (FREE, 100 req/day) ───────────────────────────────


async def _from_hackertarget(domain: str, seen: set[str]) -> list[Signal]:
    """
    HackerTarget free subdomain finder API.
    Returns newline-separated "subdomain,ip" pairs.
    No API key required. 100 free requests/day.
    """
    url = f"https://api.hackertarget.com/hostsearch/?q={domain}"
    signals: list[Signal] = []

    try:
        async with httpx.AsyncClient(headers=_DIRECT_HEADERS, timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200 or "API count" in resp.text:
                logger.debug("[DNS-HT] HackerTarget rate limited or error for %s", domain)
                return []

            for line in resp.text.strip().splitlines():
                parts = line.split(",")
                if parts:
                    fqdn = parts[0].strip().lower()
                    if fqdn and fqdn not in seen and fqdn != domain:
                        if domain in fqdn:  # safety check
                            seen.add(fqdn)
                            signals.append(_make_subdomain_signal(domain, fqdn, "hackertarget"))

    except Exception as exc:
        logger.debug("[DNS-HT] Error for %s: %s", domain, exc)

    logger.info("[DNS-HT] %s → %d subdomains", domain, len(signals))
    return signals


# ── Source 3: BufferOver.run (FREE passive DNS) ───────────────────────────────


async def _from_bufferover(domain: str, seen: set[str]) -> list[Signal]:
    """
    BufferOver.run passive DNS — free, no key required.
    Returns JSON with FDNS_A and RDNS arrays.
    """
    url = f"https://dns.bufferover.run/dns?q=.{domain}"
    signals: list[Signal] = []

    try:
        async with httpx.AsyncClient(headers=_DIRECT_HEADERS, timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()

        all_entries: list[str] = (
            data.get("FDNS_A", []) + data.get("RDNS", [])
        )
        pattern = re.compile(rf'([\w\-]+\.{re.escape(domain)})', re.I)
        for entry in all_entries:
            for match in pattern.finditer(entry):
                fqdn = match.group(1).lower()
                if fqdn not in seen and fqdn != domain:
                    seen.add(fqdn)
                    signals.append(_make_subdomain_signal(domain, fqdn, "bufferover"))

    except Exception as exc:
        logger.debug("[DNS-BO] BufferOver error for %s: %s", domain, exc)

    logger.info("[DNS-BO] %s → %d subdomains", domain, len(signals))
    return signals


# ── Source 4: AlienVault OTX (FREE, massive DB) ───────────────────────────────


async def _from_alienvault(domain: str, seen: set[str]) -> list[Signal]:
    """
    AlienVault OTX passive DNS — free, no API key for basic lookups.
    Returns up to 500 subdomains with first/last seen timestamps.
    """
    url = f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns"
    signals: list[Signal] = []

    try:
        async with httpx.AsyncClient(headers=_DIRECT_HEADERS, timeout=20) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()

        for record in data.get("passive_dns", []):
            hostname = record.get("hostname", "").lower().strip()
            if hostname and hostname not in seen and hostname != domain:
                if domain in hostname:  # safety
                    seen.add(hostname)
                    # Use the first_seen from OTX if available
                    first_seen_str = record.get("first", "")
                    signals.append(_make_subdomain_signal(domain, hostname, "alienvault_otx"))

    except Exception as exc:
        logger.debug("[DNS-OTX] AlienVault error for %s: %s", domain, exc)

    logger.info("[DNS-OTX] %s → %d subdomains", domain, len(signals))
    return signals


# ── Source 5: RapidDNS (FREE) ─────────────────────────────────────────────────


async def _from_rapiddns(domain: str, seen: set[str]) -> list[Signal]:
    """
    RapidDNS.io — free subdomain search, scrapes HTML.
    No API key. Returns HTML table of subdomains.
    """
    url = f"https://rapiddns.io/subdomain/{domain}?full=1"
    signals: list[Signal] = []

    try:
        result = await smart_fetch(url, timeout=15.0)
        if result is None:
            return []
        html = result.text if hasattr(result, "text") else str(result)
        pattern = re.compile(rf'([\w\-]+\.{re.escape(domain)})', re.I)
        for match in pattern.finditer(html):
            fqdn = match.group(1).lower()
            if fqdn not in seen and fqdn != domain:
                seen.add(fqdn)
                signals.append(_make_subdomain_signal(domain, fqdn, "rapiddns"))
    except Exception as exc:
        logger.debug("[DNS-RAPIDDNS] Error for %s: %s", domain, exc)

    logger.info("[DNS-RAPIDDNS] %s → %d subdomains", domain, len(signals))
    return signals


# ── Source 6: SecurityTrails (PAID/optional) ──────────────────────────────────


async def _from_security_trails(domain: str, seen: set[str]) -> list[Signal]:
    """
    SecurityTrails passive DNS — requires API key.
    Skipped silently if no key is configured.
    """
    if not settings.has_security_trails:
        return []

    url = f"https://api.securitytrails.com/v1/domain/{domain}/subdomains"
    signals: list[Signal] = []

    try:
        async with httpx.AsyncClient(
            headers={**_DIRECT_HEADERS, "APIKEY": settings.SECURITY_TRAILS_API_KEY},
            timeout=20,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()

        for sub in data.get("subdomains", []):
            fqdn = f"{sub}.{domain}".lower()
            if fqdn not in seen:
                seen.add(fqdn)
                signals.append(_make_subdomain_signal(domain, fqdn, "securitytrails"))

    except Exception as exc:
        logger.debug("[DNS-ST] SecurityTrails error for %s: %s", domain, exc)

    logger.info("[DNS-ST] %s → %d subdomains", domain, len(signals))
    return signals


# ── Source 7: Bright Data SERP (PAID — last resort only) ──────────────────────


async def _from_bright_data_serp(domain: str, seen: set[str]) -> list[Signal]:
    """
    Google SERP via Bright Data — ONLY called if free sources yield < _MIN_FREE_SUBDOMAINS.
    Budgeted and tracked.
    """
    guard = BudgetGuard.get_instance()
    if not guard.can_spend(BDRequestType.SERP):
        logger.warning("[DNS-SERP] Budget exhausted — skipping SERP for %s", domain)
        return []

    guard.record_spend(BDRequestType.SERP)

    proxy_url = settings.bright_data_serp_proxy_url
    url = "https://www.google.com/search"
    params = {"q": f"site:*.{domain}", "num": 50, "brd_json": "1"}
    signals: list[Signal] = []

    try:
        async with httpx.AsyncClient(
            proxies={"http://": proxy_url, "https://": proxy_url},
            timeout=35,
            verify=False,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

            try:
                data = resp.json()
                organic = data.get("organic", [])
            except Exception:
                organic = []
                # Fallback: regex on raw HTML
                pattern = re.compile(rf'([\w\-]+\.{re.escape(domain)})', re.I)
                for match in pattern.finditer(resp.text):
                    fqdn = match.group(1).lower()
                    if fqdn not in seen and fqdn != domain:
                        seen.add(fqdn)
                        signals.append(_make_subdomain_signal(domain, fqdn, "bright_data_serp"))
                return signals

            pattern = re.compile(rf'([\w\-]+\.{re.escape(domain)})', re.I)
            for result in organic:
                for field in (result.get("url", ""), result.get("title", "")):
                    for match in pattern.finditer(field):
                        fqdn = match.group(1).lower()
                        if fqdn not in seen and fqdn != domain:
                            seen.add(fqdn)
                            signals.append(
                                _make_subdomain_signal(domain, fqdn, "bright_data_serp")
                            )

    except Exception as exc:
        logger.error("[DNS-SERP] Bright Data SERP failed for %s: %s", domain, exc)

    logger.info("[DNS-SERP] %s → %d subdomains (PAID)", domain, len(signals))
    return signals


# ── Unified DNS Collector ──────────────────────────────────────────────────────


class DNSCollector:
    """
    Multi-source DNS/subdomain enumeration collector.

    Runs all free sources concurrently, merges and deduplicates results.
    Falls back to Bright Data SERP only if free sources are sparse.

    Sources (priority order, all concurrent):
      1. crt.sh SAN extraction     (free)
      2. HackerTarget              (free, 100/day)
      3. BufferOver.run            (free)
      4. AlienVault OTX            (free)
      5. RapidDNS                  (free)
      6. SecurityTrails            (optional paid, if key provided)
      7. Bright Data SERP          (paid, last resort only)

    Usage
    -----
        collector = DNSCollector(["acme.com", "globex.io"])
        signals = await collector.collect_all()
    """

    def __init__(
        self,
        target_domains: Iterable[str],
        use_serp_fallback: bool = True,
    ) -> None:
        self.target_domains = list(target_domains)
        self.use_serp_fallback = use_serp_fallback

    async def collect_domain(self, domain: str) -> list[Signal]:
        seen: set[str] = set()
        signals: list[Signal] = []

        # Run all free sources concurrently
        free_results = await asyncio.gather(
            _from_crt_sh(domain, seen),
            _from_hackertarget(domain, seen),
            _from_bufferover(domain, seen),
            _from_alienvault(domain, seen),
            _from_rapiddns(domain, seen),
            _from_security_trails(domain, seen),
            return_exceptions=True,
        )

        for result in free_results:
            if isinstance(result, list):
                signals.extend(result)
            elif isinstance(result, Exception):
                logger.debug("[DNS] Source error: %s", result)

        free_count = len(signals)
        logger.info(
            "[DNS] %s — free sources: %d subdomains from %d sources",
            domain,
            free_count,
            sum(1 for r in free_results if isinstance(r, list) and len(r) > 0),
        )

        # Only burn Bright Data budget if free sources are sparse
        if self.use_serp_fallback and free_count < _MIN_FREE_SUBDOMAINS:
            logger.info(
                "[DNS] %s — sparse results (%d), trying Bright Data SERP",
                domain,
                free_count,
            )
            serp_signals = await _from_bright_data_serp(domain, seen)
            signals.extend(serp_signals)

        logger.info("[DNS] %s → %d total unique subdomains", domain, len(signals))
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
                logger.error("[DNS] Domain error: %s", r)
        logger.info("[DNS] Total: %d subdomain signals collected", len(out))
        return out
