"""
tests/test_integration_free_apis.py
-------------------------------------
LIVE integration tests using only free public APIs (no credentials needed).

Marked with @pytest.mark.integration so they are skipped in normal CI.
Run manually with:
    pytest tests/test_integration_free_apis.py -v -m integration

What these test (real network calls, no mocking):
  1. crt.sh         — fetch certs for a real domain (google.com)
  2. HackerTarget   — subdomain enum for a real domain
  3. BufferOver.run — passive DNS for a real domain
  4. CDX API        — Wayback snapshot index for a real domain
  5. GitHub public  — fetch public org repos (no token)
  6. Full Stage 3   — pipe live crt.sh data through all 4 parsers
  7. Full Stage 4   — cluster real signals from multiple sources
  8. cluster_report — print formatted report on real data
"""
from __future__ import annotations

import asyncio
import json
import pytest
import httpx
from datetime import datetime, timezone

from models.signal import Signal, SignalSource, SignalType
from stage3_parsing import ParserOrchestrator
from stage4_correlation import CorrelationEngine, cluster_report

# Integration test marker — skipped unless you pass -m integration
pytestmark = pytest.mark.integration

# A predictable, stable test target
_TARGET = "stripe.com"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: fetch real data (no keys needed for these endpoints)
# ─────────────────────────────────────────────────────────────────────────────

async def _crtsh_certs(domain: str, limit: int = 5) -> list[dict]:
    """Query crt.sh for real cert records. Returns [] on timeout (crt.sh can be slow)."""
    url = f"https://crt.sh/?q=%.{domain}&output=json"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                return data[:limit]
    except (httpx.TimeoutException, httpx.ConnectError):
        return []  # caller will pytest.skip
    return []


async def _hackertarget_subdomains(domain: str) -> list[str]:
    """Query HackerTarget for subdomains (free, no key)."""
    url = f"https://api.hackertarget.com/hostsearch/?q={domain}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        if r.status_code == 200 and "error" not in r.text.lower():
            lines = r.text.strip().splitlines()
            return [line.split(",")[0] for line in lines if "," in line]
    return []


async def _wayback_snapshots(domain: str, limit: int = 5) -> list[dict]:
    """Query Wayback CDX API for snapshot index (free, no key). Returns [] on timeout."""
    url = (
        f"http://web.archive.org/cdx/search/cdx"
        f"?url={domain}/integrations*&output=json&limit={limit}&fl=original,timestamp,statuscode"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url)
            if r.status_code == 200 and r.text.strip():
                rows = r.json()
                if rows and rows[0] == ["original", "timestamp", "statuscode"]:
                    rows = rows[1:]  # skip header row
                return [{"url": row[0], "timestamp": row[1], "status": row[2]} for row in rows]
    except (httpx.TimeoutException, httpx.ConnectError):
        return []  # caller will pytest.skip
    return []


async def _github_public_repos(org: str, limit: int = 5) -> list[dict]:
    """Fetch GitHub public repos with NO token (60 req/hr limit)."""
    url = f"https://api.github.com/orgs/{org}/repos?per_page={limit}&sort=updated"
    async with httpx.AsyncClient(
        headers={"Accept": "application/vnd.github.v3+json"},
        timeout=15.0,
    ) as client:
        r = await client.get(url)
        if r.status_code == 200:
            return r.json()
    return []


def _make_ct_signal(cert: dict, domain: str) -> Signal:
    """Convert a raw crt.sh record into a Signal."""
    name_value: str = cert.get("name_value", "")
    all_domains = list({d.strip() for d in name_value.splitlines() if d.strip()})
    return Signal(
        source=SignalSource.CT_LOG,
        target_domain=domain,
        signal_type=SignalType.NEW_CERT,
        timestamp=datetime.now(timezone.utc),
        raw_data={
            "all_domains": all_domains,
            "cert_domain": all_domains[0] if all_domains else domain,
            "issuer_name": cert.get("issuer_name", ""),
            "not_before": cert.get("not_before", ""),
            "not_after": cert.get("not_after", ""),
            "serial_number": cert.get("serial_number", ""),
            "name_value": name_value,
        },
        confidence=0.65,
    )


def _make_dns_signal(fqdn: str, domain: str) -> Signal:
    return Signal(
        source=SignalSource.DNS,
        target_domain=domain,
        signal_type=SignalType.NEW_SUBDOMAIN,
        timestamp=datetime.now(timezone.utc),
        raw_data={"fqdn": fqdn, "source": "hackertarget"},
        confidence=0.55,
    )


def _make_wayback_signal(row: dict, domain: str) -> Signal:
    return Signal(
        source=SignalSource.WAYBACK,
        target_domain=domain,
        signal_type=SignalType.NEW_INTEGRATION_PAGE,
        timestamp=datetime.now(timezone.utc),
        raw_data={
            "url": row["url"],
            "is_new_url": True,
            "wayback_url": f"https://web.archive.org/web/{row['timestamp']}/{row['url']}",
            "wayback_timestamp": row["timestamp"],
        },
        confidence=0.72,
    )


def _make_github_signal(repo: dict, domain: str) -> Signal:
    return Signal(
        source=SignalSource.GITHUB,
        target_domain=domain,
        signal_type=SignalType.NEW_REPO,
        timestamp=datetime.now(timezone.utc),
        raw_data={
            "repo": repo.get("full_name", ""),
            "description": repo.get("description") or "",
            "language": repo.get("language"),
            "topics": repo.get("topics", []),
            "stars": repo.get("stargazers_count", 0),
            "forks": repo.get("forks_count", 0),
            "url": repo.get("html_url", ""),
        },
        confidence=0.50,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Individual API tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_crtsh_returns_certs():
    """crt.sh returns real cert records for stripe.com."""
    certs = asyncio.get_event_loop().run_until_complete(_crtsh_certs(_TARGET, limit=5))
    if not certs:
        pytest.skip("crt.sh timed out or unavailable (slow server — try again)")
    assert "name_value" in certs[0], f"Unexpected cert format: {list(certs[0].keys())}"
    print(f"\n✅ crt.sh: {len(certs)} certs for {_TARGET}")
    print(f"   First cert domain: {certs[0].get('name_value', '')[:60]}")


@pytest.mark.integration
def test_hackertarget_returns_subdomains():
    """HackerTarget returns subdomains for stripe.com (free API)."""
    subdomains = asyncio.get_event_loop().run_until_complete(
        _hackertarget_subdomains(_TARGET)
    )
    # May return empty if rate-limited; just check it doesn't crash
    print(f"\n✅ HackerTarget: {len(subdomains)} subdomains for {_TARGET}")
    if subdomains:
        print(f"   Sample: {subdomains[:3]}")
        assert all("." in s for s in subdomains), "Returned values don't look like FQDNs"


@pytest.mark.integration
def test_wayback_cdx_returns_snapshots():
    """Wayback CDX API returns integration page snapshots for stripe.com."""
    rows = asyncio.get_event_loop().run_until_complete(
        _wayback_snapshots(_TARGET, limit=5)
    )
    print(f"\n✅ Wayback CDX: {len(rows)} snapshots for {_TARGET}/integrations*")
    if rows:
        print(f"   Sample URL: {rows[0]['url']}")
        assert "url" in rows[0]
        assert "timestamp" in rows[0]


@pytest.mark.integration
def test_github_public_repos_no_token():
    """GitHub public API returns repos for 'stripe' org without a token."""
    repos = asyncio.get_event_loop().run_until_complete(
        _github_public_repos("stripe", limit=5)
    )
    assert len(repos) >= 1, "GitHub returned no repos — possible rate limit"
    assert "full_name" in repos[0], f"Unexpected repo format: {list(repos[0].keys())}"
    print(f"\n✅ GitHub (no token): {len(repos)} repos for stripe org")
    print(f"   Sample: {repos[0].get('full_name')}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 integration: real API data → ParserOrchestrator
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stage3_parses_real_crtsh_data():
    """
    Fetch real certs from crt.sh → convert to Signals → run through Stage 3 parsers.
    Validates that real-world cert data flows through the parsing pipeline correctly.
    """
    certs = asyncio.get_event_loop().run_until_complete(_crtsh_certs(_TARGET, limit=5))
    if not certs:
        pytest.skip("crt.sh unavailable")

    signals = [_make_ct_signal(c, _TARGET) for c in certs]
    print(f"\n🔄 Stage 3: Parsing {len(signals)} real CT signals for {_TARGET}")

    orch = ParserOrchestrator()
    parsed = asyncio.get_event_loop().run_until_complete(orch.parse(signals))

    assert len(parsed) == len(signals), "Parser dropped signals"
    for p in parsed:
        assert p.confidence > 0, "Parsed signal has zero confidence"
        assert isinstance(p.entities.get("all_domains"), list)
        print(f"   ✅ {p.entities.get('cert_domain', '?')} | vendors={p.vendor_hints} | conf={p.confidence:.2f}")


@pytest.mark.integration
def test_stage3_parses_real_dns_data():
    """
    Fetch real subdomains from HackerTarget → run through DNS parser.
    """
    subdomains = asyncio.get_event_loop().run_until_complete(
        _hackertarget_subdomains(_TARGET)
    )
    if not subdomains:
        pytest.skip("HackerTarget returned no data (rate-limited?)")

    signals = [_make_dns_signal(fqdn, _TARGET) for fqdn in subdomains[:10]]
    print(f"\n🔄 Stage 3: Parsing {len(signals)} real DNS signals for {_TARGET}")

    orch = ParserOrchestrator()
    parsed = asyncio.get_event_loop().run_until_complete(orch.parse(signals))

    assert len(parsed) == len(signals)
    for p in parsed:
        assert "fqdn" in p.entities
        assert p.entities["valid"] is True or p.confidence <= 0.20
        print(f"   ✅ {p.entities.get('fqdn')} | depth={p.entities.get('depth')} | conf={p.confidence:.2f}")


@pytest.mark.integration
def test_stage3_parses_real_wayback_data():
    """
    Fetch real Wayback snapshots → run through Wayback parser.
    """
    rows = asyncio.get_event_loop().run_until_complete(
        _wayback_snapshots(_TARGET, limit=5)
    )
    if not rows:
        pytest.skip("Wayback CDX returned no data or timed out")

    signals = [_make_wayback_signal(r, _TARGET) for r in rows]
    print(f"\n🔄 Stage 3: Parsing {len(signals)} real Wayback signals for {_TARGET}")

    orch = ParserOrchestrator()
    parsed = asyncio.get_event_loop().run_until_complete(orch.parse(signals))

    assert len(parsed) == len(signals)
    for p in parsed:
        assert "path_type" in p.entities
        assert "url" in p.entities
        print(f"   ✅ {p.entities.get('url', '')[:60]} | type={p.entities.get('path_type')} | conf={p.confidence:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 integration: real multi-source signals → CorrelationEngine
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_stage4_correlates_real_multi_source_signals():
    """
    Pull real data from crt.sh + HackerTarget + Wayback CDX.
    Feed through Stage 3 → Stage 4.
    Verify clusters are produced, scored, and contain real vendor attributions.
    """
    # Fetch from three free APIs concurrently
    async def gather_all():
        return await asyncio.gather(
            _crtsh_certs(_TARGET, limit=10),
            _hackertarget_subdomains(_TARGET),
            _wayback_snapshots(_TARGET, limit=5),
            return_exceptions=True,
        )

    certs, subdomains, wayback_rows = asyncio.get_event_loop().run_until_complete(gather_all())

    # Fallback if any source fails
    certs         = certs        if isinstance(certs, list)         else []
    subdomains    = subdomains   if isinstance(subdomains, list)    else []
    wayback_rows  = wayback_rows if isinstance(wayback_rows, list)  else []

    if not certs and not subdomains and not wayback_rows:
        pytest.skip("All free APIs unavailable (network issue)")

    # Build raw signals
    raw_signals = []
    for c in certs:
        raw_signals.append(_make_ct_signal(c, _TARGET))
    for fqdn in subdomains[:10]:
        raw_signals.append(_make_dns_signal(fqdn, _TARGET))
    for row in wayback_rows:
        raw_signals.append(_make_wayback_signal(row, _TARGET))

    print(f"\n🔄 Stage 3+4 integration: {len(raw_signals)} real signals for {_TARGET}")
    print(f"   CT: {len(certs)} | DNS: {len(subdomains[:10])} | Wayback: {len(wayback_rows)}")

    # Stage 3 + 4 full run
    engine = CorrelationEngine(window_days=30)
    clusters = asyncio.get_event_loop().run_until_complete(
        engine.correlate_raw(raw_signals)
    )

    # Print full report
    report = cluster_report(clusters)
    print(f"\n{report}")

    # Assertions
    assert len(clusters) >= 1, "No clusters produced from real data"
    target_cluster = next(
        (c for c in clusters if c.target_domain == _TARGET), None
    )
    assert target_cluster is not None, f"No cluster for {_TARGET}"
    assert target_cluster.signal_count >= 1
    assert 0.0 < target_cluster.confidence <= 1.0
    assert target_cluster.tier is not None

    print(f"\n✅ Final cluster for {_TARGET}:")
    print(f"   Confidence : {target_cluster.confidence:.0%} ({target_cluster.tier.value.upper()})")
    print(f"   Signals    : {target_cluster.signal_count}")
    print(f"   Vendors    : {target_cluster.vendors}")
    print(f"   Summary    : {target_cluster.summary}")
