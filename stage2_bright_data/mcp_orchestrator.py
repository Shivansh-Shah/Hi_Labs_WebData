"""
stage2_bright_data/mcp_orchestrator.py
----------------------------------------
Bright Data MCP-backed OSINT investigator.

The investigation playbook runs as a deterministic async pipeline — no LLM
required for the Python layer.  When Claude Code has the Bright Data MCP server
configured (see .claude/settings.json) it gets 60+ live tools directly; this
module handles the *programmatic* investigation that runs inside the pipeline.

Architecture:
  • Claude Code agent  → BD MCP server (https://mcp.brightdata.com/mcp?token=…)
                          60+ tools: search, scrape, structured data, browser
  • Python pipeline   → BD REST API via SERP zone (serp_api)
                          SERP searches + content fetching without an LLM loop

BD MCP skills reference: https://github.com/brightdata/skills
  - search  → bdata search / bdata discover
  - scrape  → bdata scrape (markdown / HTML / screenshot)
  - data-feeds → bdata pipelines (Amazon, LinkedIn, Crunchbase, …)
  - bright-data-mcp → 60+ MCP tools for agent use
  - agent-onboarding → setup + auth guide

Cost: ~$0.005 per SERP call. With $250 budget ≈ 50,000 SERP queries.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from config.settings import get_settings
from models.signal import Signal, SignalSource, SignalType
from stage2_bright_data.serp_api import SERPClient
from stage2_bright_data.web_unlocker import smart_fetch
from stage2_bright_data.scraping_browser import fetch_robots_txt
from stage2_bright_data.budget_guard import BudgetGuard

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Vendor pattern table (subdomain prefix → vendor name) ────────────────────

_VENDOR_PATTERNS: dict[str, str] = {
    # CRM
    "salesforce": "Salesforce", "crm": "Salesforce/HubSpot", "hubspot": "HubSpot",
    "marketo": "Marketo", "pardot": "Salesforce Pardot",
    # Payments
    "stripe": "Stripe", "braintree": "Braintree", "adyen": "Adyen",
    "recurly": "Recurly",
    # Identity
    "okta": "Okta", "auth0": "Auth0", "onelogin": "OneLogin",
    # Analytics
    "analytics": "Amplitude/Mixpanel", "amplitude": "Amplitude",
    "mixpanel": "Mixpanel", "segment": "Segment", "rudderstack": "RudderStack",
    # Observability
    "datadog": "Datadog", "newrelic": "New Relic", "splunk": "Splunk",
    "pagerduty": "PagerDuty",
    # Support
    "zendesk": "Zendesk", "intercom": "Intercom", "freshdesk": "Freshdesk",
    # HR
    "workday": "Workday", "bamboohr": "BambooHR", "greenhouse": "Greenhouse",
    # Data
    "snowflake": "Snowflake", "databricks": "Databricks", "looker": "Looker",
    # Comms
    "slack": "Slack", "zoom": "Zoom",
}

# Subdomains that clearly indicate a vendor deployment
_VENDOR_SUBDOMAIN_RE = re.compile(
    r'^(' + '|'.join(re.escape(k) for k in _VENDOR_PATTERNS) + r')(\.|$)', re.I
)

# Integration page path patterns
_INTEGRATION_PATH_RE = re.compile(
    r'/(integrations?|partners?|marketplace|apps?|connect|ecosystem)',
    re.I,
)


# ── Helper: emit a Signal from raw evidence ───────────────────────────────────


def _make_signal(
    target_domain: str,
    signal_type: SignalType,
    source: SignalSource,
    description: str,
    confidence: float,
    vendor_hint: str | None = None,
    evidence_url: str | None = None,
) -> Signal:
    return Signal(
        source=source,
        target_domain=target_domain,
        signal_type=signal_type,
        timestamp=datetime.now(timezone.utc),
        raw_data={"description": description, "evidence_url": evidence_url or ""},
        confidence=min(1.0, max(0.0, confidence)),
        vendor_hint=vendor_hint,
        metadata={
            "emitted_by": "bd_mcp_orchestrator",
            "description": description,
        },
    )


def _vendor_from_subdomain(subdomain: str) -> str | None:
    """Return vendor name if the subdomain prefix matches a known pattern."""
    m = _VENDOR_SUBDOMAIN_RE.match(subdomain)
    if m:
        return _VENDOR_PATTERNS.get(m.group(1).lower())
    return None


# ── Step 1: CT log certificate check (crt.sh — free) ─────────────────────────


async def _check_ct_logs(domain: str) -> list[Signal]:
    """
    Query crt.sh for recent certificates issued for *.domain.
    New vendor-specific subdomains in SAN entries = new_cert signal.
    """
    signals: list[Signal] = []
    url = f"https://crt.sh/?q=%.{domain}&output=json"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return signals
            entries: list[dict] = resp.json()
    except Exception as exc:
        logger.warning("[BD-ORCH] crt.sh failed for %s: %s", domain, exc)
        return signals

    seen_names: set[str] = set()
    for entry in entries[:200]:  # Limit to recent 200
        name_value = entry.get("name_value", "")
        for name in name_value.split("\n"):
            name = name.strip().lstrip("*.")
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            # Check if subdomain has a vendor-specific prefix
            sub = name.replace(f".{domain}", "").replace(domain, "")
            if not sub or sub == name:
                continue

            vendor = _vendor_from_subdomain(sub)
            if vendor:
                signals.append(_make_signal(
                    target_domain=domain,
                    signal_type=SignalType.NEW_CERT,
                    source=SignalSource.CT_LOG,
                    description=f"New cert issued for {name} — indicates {vendor} deployment",
                    confidence=0.78,
                    vendor_hint=vendor,
                    evidence_url=url,
                ))
                logger.info("[BD-ORCH] CT cert signal: %s → %s", name, vendor)

    logger.info("[BD-ORCH] CT log: %d cert signals for %s", len(signals), domain)
    return signals


# ── Step 2: Subdomain SERP enumeration (BD SERP zone) ────────────────────────


async def _enumerate_subdomains(domain: str) -> list[Signal]:
    """
    Use BD SERP to run site:*.{domain} and extract vendor-specific subdomains.
    """
    signals: list[Signal] = []
    client = SERPClient()

    subdomains = await client.search_subdomains(domain)
    for fqdn in subdomains:
        sub_prefix = fqdn.replace(f".{domain}", "")
        vendor = _vendor_from_subdomain(sub_prefix)
        if vendor:
            signals.append(_make_signal(
                target_domain=domain,
                signal_type=SignalType.NEW_SUBDOMAIN,
                source=SignalSource.DNS,
                description=f"Vendor subdomain found: {fqdn} → {vendor}",
                confidence=0.70,
                vendor_hint=vendor,
                evidence_url=f"https://www.google.com/search?q=site:*.{domain}",
            ))
            logger.info("[BD-ORCH] Subdomain signal: %s → %s", fqdn, vendor)

    logger.info("[BD-ORCH] Subdomain SERP: %d vendor signals for %s", len(signals), domain)
    return signals


# ── Step 3: robots.txt hidden path analysis ───────────────────────────────────


async def _check_robots(domain: str) -> list[Signal]:
    """
    Fetch robots.txt and look for staged/beta/product-launch paths.
    """
    signals: list[Signal] = []
    _LAUNCH_PATTERNS = re.compile(
        r'/(launch|beta|preview|staging|upcoming|new-product|v2|release)',
        re.I,
    )

    try:
        data = await fetch_robots_txt(domain)
    except Exception as exc:
        logger.debug("[BD-ORCH] robots.txt failed for %s: %s", domain, exc)
        return signals

    for path in data.get("disallowed", []):
        if _LAUNCH_PATTERNS.search(path):
            signals.append(_make_signal(
                target_domain=domain,
                signal_type=SignalType.ROBOTS_CHANGE,
                source=SignalSource.ROBOTS_TXT,
                description=f"Staged/beta path in robots.txt: {path}",
                confidence=0.65,
                evidence_url=f"https://{domain}/robots.txt",
            ))
            logger.info("[BD-ORCH] robots.txt launch path: %s", path)

    # Check interesting_paths surfaced by the robots parser
    for path in data.get("interesting_paths", []):
        if _INTEGRATION_PATH_RE.search(path):
            signals.append(_make_signal(
                target_domain=domain,
                signal_type=SignalType.NEW_INTEGRATION_PAGE,
                source=SignalSource.ROBOTS_TXT,
                description=f"Integration/partner path found in robots.txt: {path}",
                confidence=0.60,
                evidence_url=f"https://{domain}{path}",
            ))

    return signals


# ── Step 4: Integration page content check ───────────────────────────────────


async def _check_integration_pages(domain: str) -> list[Signal]:
    """
    Fetch /integrations and /partners pages — new vendor mentions = signals.
    Uses BD SERP zone for anti-bot bypass; falls back to direct HTTP.
    """
    signals: list[Signal] = []
    paths = ["/integrations", "/partners", "/marketplace", "/apps"]

    for path in paths:
        url = f"https://{domain}{path}"
        result = await smart_fetch(url, timeout=20.0)
        if result is None:
            continue

        text = result.text if hasattr(result, "text") else str(result)
        if len(text) < 200:
            continue

        # Count vendor mentions in the page
        vendors_found: set[str] = set()
        text_lower = text.lower()
        for keyword, vendor in _VENDOR_PATTERNS.items():
            if keyword in text_lower:
                vendors_found.add(vendor)

        if vendors_found:
            signals.append(_make_signal(
                target_domain=domain,
                signal_type=SignalType.NEW_INTEGRATION_PAGE,
                source=SignalSource.WAYBACK,
                description=(
                    f"Integration page {url} mentions: "
                    f"{', '.join(sorted(vendors_found)[:5])}"
                ),
                confidence=0.72,
                vendor_hint=list(vendors_found)[0] if len(vendors_found) == 1 else None,
                evidence_url=url,
            ))
            logger.info(
                "[BD-ORCH] Integration page %s: %d vendors", url, len(vendors_found)
            )
        break  # Found an accessible integration page — stop checking others

    return signals


# ── Step 5: SERP search for vendor evidence ───────────────────────────────────


async def _serp_vendor_search(domain: str) -> list[Signal]:
    """
    Run targeted SERP queries to find vendor partnership announcements.
    """
    signals: list[Signal] = []
    client = SERPClient()

    queries = [
        f'site:{domain} "integrations" OR "partners"',
        f'"{domain}" "powered by" OR "built with" salesforce OR stripe OR okta',
    ]

    for query in queries:
        resp = await client.search(query, max_results=15)
        for result in resp.organic:
            url_lower = result.url.lower()
            title_lower = result.title.lower()

            # Look for vendor-specific URLs in results
            for keyword, vendor in _VENDOR_PATTERNS.items():
                if keyword in url_lower or keyword in title_lower:
                    signals.append(_make_signal(
                        target_domain=domain,
                        signal_type=SignalType.NEW_INTEGRATION_PAGE,
                        source=SignalSource.WAYBACK,
                        description=(
                            f"SERP result links {domain} to {vendor}: "
                            f"{result.title[:80]}"
                        ),
                        confidence=0.62,
                        vendor_hint=vendor,
                        evidence_url=result.url,
                    ))
                    break  # One signal per result

        await asyncio.sleep(0.5)  # Polite SERP delay

    return signals


# ── Main orchestrator ─────────────────────────────────────────────────────────


class MCPAgentOrchestrator:
    """
    Bright Data MCP-backed OSINT investigator.

    Runs a deterministic 5-step playbook using BD REST API for SERP + content:
      1. CT log certificate check (crt.sh — free)
      2. Subdomain enumeration via BD SERP (serp_api zone)
      3. robots.txt hidden path analysis
      4. Integration page content check (via BD SERP zone)
      5. Targeted SERP vendor evidence search

    For Claude Code agent tool use, the BD MCP server is configured in
    .claude/settings.json — 60+ live tools available directly to the agent.
    See: https://github.com/brightdata/skills

    Usage
    -----
        orchestrator = MCPAgentOrchestrator()
        signals = await orchestrator.investigate_domain("acme.com")
        signals = await orchestrator.detect_product_launch("competitor.com")
    """

    def __init__(self, concurrency: int = 3) -> None:
        if not settings.has_bright_data:
            raise ValueError(
                "BRIGHT_DATA_API_KEY not configured. "
                "Add it to your .env file. "
                "Get a token at https://brightdata.com/cp/setting"
            )
        self._concurrency = concurrency
        logger.info(
            "[BD-ORCH] MCPAgentOrchestrator ready — zone=%s mcp=%s",
            settings.bright_data_active_zone,
            "✓" if settings.has_bright_data_mcp else "✗",
        )

    async def investigate_domain(
        self, target_domain: str, context: str = ""
    ) -> list[Signal]:
        """
        Run full OSINT investigation for a target domain.
        Returns deduplicated list of Signal objects.
        """
        logger.info("[BD-ORCH] Investigating %s…", target_domain)
        start = datetime.now(timezone.utc)

        # Run steps 1–3 in parallel (independent + free/cheap)
        step1, step2, step3 = await asyncio.gather(
            _check_ct_logs(target_domain),
            _enumerate_subdomains(target_domain),
            _check_robots(target_domain),
            return_exceptions=True,
        )

        # Steps 4–5 after (may depend on SERP budget awareness)
        step4, step5 = await asyncio.gather(
            _check_integration_pages(target_domain),
            _serp_vendor_search(target_domain),
            return_exceptions=True,
        )

        # Collect all signals, skip any step that raised
        all_signals: list[Signal] = []
        for step_result in (step1, step2, step3, step4, step5):
            if isinstance(step_result, list):
                all_signals.extend(step_result)
            elif isinstance(step_result, Exception):
                logger.warning("[BD-ORCH] Step failed: %s", step_result)

        # Deduplicate by (signal_type, vendor_hint, evidence_url)
        seen: set[tuple] = set()
        deduped: list[Signal] = []
        for sig in all_signals:
            key = (
                sig.signal_type,
                sig.vendor_hint or "",
                sig.raw_data.get("evidence_url", "")[:80],
            )
            if key not in seen:
                seen.add(key)
                deduped.append(sig)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        BudgetGuard.get_instance().log_summary()
        logger.info(
            "[BD-ORCH] Investigation complete: %s → %d signals in %.1fs",
            target_domain, len(deduped), elapsed,
        )
        return deduped

    async def detect_product_launch(
        self,
        competitor_domain: str,
        product_keywords: list[str] | None = None,
    ) -> list[Signal]:
        """
        Detect staged competitor product launches.
        Focuses on robots.txt staged paths, new subdomains, beta/staging certs.
        """
        logger.info("[BD-ORCH] Product launch scan for %s…", competitor_domain)

        ct_signals, robots_signals = await asyncio.gather(
            _check_ct_logs(competitor_domain),
            _check_robots(competitor_domain),
        )

        # Extra SERP for explicit launch keywords
        all_signals: list[Signal] = []
        for s in (ct_signals, robots_signals):
            if isinstance(s, list):
                all_signals.extend(s)

        if product_keywords:
            client = SERPClient()
            for kw in product_keywords[:3]:
                resp = await client.search(
                    f'site:{competitor_domain} "{kw}"', max_results=10
                )
                for result in resp.organic:
                    all_signals.append(_make_signal(
                        target_domain=competitor_domain,
                        signal_type=SignalType.NEW_INTEGRATION_PAGE,
                        source=SignalSource.WAYBACK,
                        description=f"Product launch keyword '{kw}' found: {result.title[:80]}",
                        confidence=0.68,
                        evidence_url=result.url,
                    ))

        return all_signals

    async def bulk_investigate(
        self, domains: list[str], concurrency: int | None = None
    ) -> dict[str, list[Signal]]:
        """Investigate multiple domains with concurrency control."""
        semaphore = asyncio.Semaphore(concurrency or self._concurrency)

        async def investigate_one(d: str) -> tuple[str, list[Signal]]:
            async with semaphore:
                return d, await self.investigate_domain(d)

        results = await asyncio.gather(
            *[investigate_one(d) for d in domains],
            return_exceptions=True,
        )
        return {
            d: sigs
            for result in results
            if isinstance(result, tuple)
            for d, sigs in [result]
        }
