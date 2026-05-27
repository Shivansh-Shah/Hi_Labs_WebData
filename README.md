# GTM Intelligence Platform

> **Autonomous AI-powered competitor intelligence** that detects hidden enterprise deals and product launches by mining public technical signals — SSL certificates, DNS subdomains, Wayback Machine snapshots, and GitHub activity.

---

## Table of Contents

1. [How it Works](#how-it-works)
2. [Setup](#setup)
3. [Stage 1 — Signal Collection](#stage-1--signal-collection)
4. [Stage 2 — Bright Data Ingestion Layer](#stage-2--bright-data-ingestion-layer)
5. [Stage 3 — Parsing & Normalisation](#stage-3--parsing--normalisation)
6. [Stage 4 — Correlation Engine](#stage-4--correlation-engine)
7. [Running the Full Pipeline](#running-the-full-pipeline)
8. [CLI Reference](#cli-reference)
9. [Signal Schema](#signal-schema)
10. [Confidence Scoring](#confidence-scoring)
11. [Environment Variables](#environment-variables)
12. [File Structure](#file-structure)
13. [Roadmap](#roadmap)

---

## How it Works

```
Target Companies
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 1 — Signal Collection (4 collectors)          │
│  CT Logs │ DNS Subdomains │ Wayback │ GitHub         │
└────────────────────┬────────────────────────────────┘
                     │  raw Signal objects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 2 — Bright Data Ingestion Layer              │
│  Web Unlocker │ SERP API │ Scraping Browser │ Agent │
│  (free-first strategy — Bright Data only as fallback)│
└────────────────────┬────────────────────────────────┘
                     │  deduplicated Signal list
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 3 — Parsing & Normalisation (4 parsers)      │
│  CT Parser │ DNS Parser │ Wayback Parser │ GH Parser │
│  + VendorFingerprinter (subdomain → vendor lookup)  │
└────────────────────┬────────────────────────────────┘
                     │  ParsedSignal objects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 4 — Correlation Engine                       │
│  Layer 1: pandas 30-day rolling window              │
│  Layer 2: NetworkX bipartite graph clustering       │
│  ConfidenceScorer: diversity + pairings + penalties │
└────────────────────┬────────────────────────────────┘
                     │  DealCluster objects
                     ▼
              Deal Intelligence
         (⚪ LOW / 🟡 MEDIUM / 🔴 HIGH)
```

---

## Setup

```bash
# 1. Clone and create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env — minimum required: GEMINI_API_KEY + GITHUB_TOKEN

# 4. Verify everything is working
python -m pytest tests/ -v
# Expected: 47 passed
```

### Minimum viable config (free only)

```env
GEMINI_API_KEY=your_free_gemini_key     # https://aistudio.google.com/apikey
GITHUB_TOKEN=ghp_your_github_token     # https://github.com/settings/tokens
```

> Bright Data credentials are **optional** — the platform runs fully on free APIs and only invokes Bright Data as a fallback when direct requests are blocked.

---

## Stage 1 — Signal Collection

Four concurrent async collectors, all free:

### CT Log Collector
```python
from stage1_signal_collection import CTLogPollingCollector, CTLogStreamingCollector

# Batch: query crt.sh for all certs issued in the last 30 days (free)
collector = CTLogPollingCollector(target_domains=["acme.com"], since_days=30)
signals = await collector.collect_all()

# Live stream: subscribe to certstream WebSocket (real-time, free)
collector = CTLogStreamingCollector(
    target_domains={"acme.com", "globex.io"},
    on_signal=lambda s: print(s.signal_type, s.raw_data["cert_domain"]),
)
await collector.run()  # long-running
```

**What it detects:** New SSL certs for `crm.acme.com`, `*.acme.com`, `salesforce.acme.com` — each subdomain prefix is cross-referenced against the vendor fingerprint table.

---

### DNS Collector
```python
from stage1_signal_collection import DNSCollector

collector = DNSCollector(target_domains=["acme.com"])
signals = await collector.collect_all()
```

**6 sources run concurrently (all free):**

| Source | Method | Limit |
|--------|--------|-------|
| crt.sh SAN extraction | Mine cert SAN entries | Unlimited |
| HackerTarget | `/hostsearch/` API | 100 req/day |
| BufferOver.run | Passive DNS JSON | Unlimited |
| AlienVault OTX | Threat intel DB | Unlimited |
| RapidDNS | HTML scrape | Unlimited |
| SecurityTrails | REST API | Optional (paid) |

Bright Data SERP fires **only** if free sources return fewer than 5 subdomains.

---

### Wayback Collector
```python
from stage1_signal_collection import WaybackCollector

collector = WaybackCollector(
    target_domains=["acme.com"],
    diff_window_days=30,   # rolling window
    diff_content=True,     # also fetch + diff page content
)
signals = await collector.collect_all()
```

**Monitored paths:** `/integrations*`, `/partners*`, `/marketplace*`, `/changelog*`, `/pricing*`, `/customers*`, `/beta*`, `/launch*`, `/docs*`, `/status*`

Persists seen URL fingerprints to `.wayback_state/` between runs so only *net-new* URLs emit signals.

---

### GitHub Collector
```python
from stage1_signal_collection import GitHubOrgCollector

collector = GitHubOrgCollector(
    org_names=["salesforce", "hubspot"],
    customer_names=["Acme Corp", "Globex Industries"],  # names to watch for in commits
    lookback_days=30,
    spike_threshold=10,   # commits/day to trigger COMMIT_SPIKE
    max_repos=20,
)
signals = await collector.collect_all()
```

Uses direct async httpx (no PyGithub) with rate-limit header tracking. Pauses automatically when approaching the 5,000 req/hr limit.

---

## Stage 2 — Bright Data Ingestion Layer

### Budget Guard
Every Bright Data request is gated through `BudgetGuard` — a singleton tracker that hard-stops requests when the `BRIGHT_DATA_BUDGET_USD` cap is reached.

```python
from stage2_bright_data import BudgetGuard, BDRequestType

guard = BudgetGuard.get_instance()
print(guard.snapshot())
# BudgetSnapshot(total_spent=0.024, total_requests=6, budget_remaining=249.976, ...)
```

| Zone | Cost/request | With $250 budget |
|------|-------------|-----------------|
| Web Unlocker | ~$0.004 | ~62,000 requests |
| SERP API | ~$0.005 | ~50,000 requests |
| Scraping Browser | ~$0.10/session | ~2,500 sessions |

### Smart Fetch (free-first)
```python
from stage2_bright_data import smart_fetch

# Tries direct HTTP first (free), falls back to BD Web Unlocker only if blocked
result = await smart_fetch("https://crt.sh/?q=%.acme.com&output=json", json_response=True)
```

### SERP Client
```python
from stage2_bright_data import SERPClient

client = SERPClient()
# DuckDuckGo first (free), BD Google only if DDG returns < 3 results
resp = await client.search("site:*.acme.com integrations")
subdomains = await client.search_subdomains("acme.com")
```

### MCP Agent (Gemini-powered)
```python
from stage2_bright_data import MCPAgentOrchestrator

agent = MCPAgentOrchestrator()   # uses GEMINI_API_KEY (free tier)

# Autonomous investigation: Gemini plans and executes tool calls
signals = await agent.investigate_domain("acme.com")

# Module 2: detect staged product launches
signals = await agent.detect_product_launch(
    "competitor.com",
    product_keywords=["new-product", "v2", "platform"]
)
```

The agent has 5 tools: `web_fetch`, `serp_search`, `fetch_robots_txt`, `batch_fetch_urls`, `emit_signal`. It autonomously chains them following the investigation playbook (crt.sh → subdomain enumeration → robots.txt → integration pages).

---

## Stage 3 — Parsing & Normalisation

Takes raw `Signal` objects and extracts structured entities. Four parsers run concurrently.

```python
from stage3_parsing import ParserOrchestrator

orch = ParserOrchestrator()
parsed_signals = await orch.parse(raw_signals)
# Returns list[ParsedSignal]
```

### What each parser extracts

| Parser | Key extractions |
|--------|----------------|
| **CT Log** | SAN domain list, issuer (LE vs. commercial), lifespan, wildcard flag, vendor hints per SAN |
| **DNS** | FQDN validity, subdomain depth (deep = penalised), first-seen flag, vendor category |
| **Wayback** | Path type (integration/pricing/changelog…), URL vendor slugs, content word tokens, new-vs-changed |
| **GitHub** | Commit velocity, spike ratio, spaCy NER on commit messages, repo topic category |

### VendorFingerprinter
```python
from stage3_parsing import VendorFingerprinter

fp = VendorFingerprinter()

# Match by subdomain prefix
result = fp.match_subdomain("salesforce.acme.com")
# FingerprintMatch(vendor='Salesforce', category='crm', confidence=0.95)

# Match by URL path
result = fp.match_path("https://acme.com/integrations/stripe")
# FingerprintMatch(vendor='Stripe', category='payments', confidence=0.88)

# Find all vendor mentions in text
matches = fp.match_text("We integrated Okta for SSO and Stripe for billing.")
# [FingerprintMatch(Okta), FingerprintMatch(Stripe)]

# Register new patterns at runtime (used by Stage 5 LLM enrichment)
fp.register_pattern("gong", "Gong.io", "sales_intel", confidence=0.88)
```

### ParsedSignal schema
```python
@dataclass
class ParsedSignal:
    signal: Signal           # original raw signal
    entities: dict           # parser-specific extracted fields
    vendor_hints: list[str]  # all vendors attributed to this signal
    confidence: float        # refined confidence after parsing
    parsed_at: datetime
```

---

## Stage 4 — Correlation Engine

The core IP. Groups `ParsedSignal` objects into `DealCluster` objects using two-layer clustering.

```python
from stage4_correlation import CorrelationEngine, cluster_report

engine = CorrelationEngine(window_days=30)

# From raw signals (runs Stage 3 internally)
clusters = await engine.correlate_raw(raw_signals)

# From already-parsed signals (Stage 3 already done)
clusters = await engine.correlate_signals(parsed_signals)

# Print formatted report
print(cluster_report(clusters))
```

### Two-layer clustering

**Layer 1 — pandas rolling window:**
```
Group by target_domain → sort by timestamp
→ take all signals within the last 30 days of activity
→ emit candidate cluster if signal_count ≥ 2
```

**Layer 2 — NetworkX bipartite graph:**
```
Nodes: signal_ids + vendor names
Edges: signal_id → vendor  (weight = signal.confidence)
→ connected_components() reveals vendor-cohesive sub-clusters
→ signals sharing vendors are merged into one cluster
```

### Confidence scoring formula

```
score = weighted_avg(signal.confidence)
      + 0.15 × (unique_sources - 1)     ← source diversity
      + 0.10 × min(unique_vendors, 3)   ← vendor specificity
      + 0.05 × (unique_signal_types - 1)← type diversity
      + 0.10  if CT_LOG + DNS co-occur  ← strongest evidence pair
      + 0.08  if GitHub commit spike    ← development activity
      + 0.08  if Wayback integration pg ← public evidence
      - 0.10  if only 1 source         ← concentration penalty
      - 0.05  if window > 25 days      ← spread penalty
      capped at [0.0, 1.0]
```

### Confidence tiers

| Tier | Score | Meaning |
|------|-------|---------|
| 🔴 **HIGH** | ≥ 0.80 | 3+ signals, multiple sources, vendor-corroborated |
| 🟡 **MEDIUM** | ≥ 0.55 | 2 signals or single strong source |
| ⚪ **LOW** | < 0.55 | Single weak signal, watch but don't act |

### DealCluster schema
```python
@dataclass
class DealCluster:
    cluster_id: str
    target_domain: str        # "acme.com"
    signal_count: int         # total signals in cluster
    source_diversity: int     # number of distinct sources
    vendors: list[str]        # ["Salesforce", "Stripe"]
    window_start: datetime
    window_end: datetime
    confidence: float         # 0.0–1.0
    tier: ConfidenceTier      # HIGH / MEDIUM / LOW
    summary: str              # human-readable one-liner
```

### Sample cluster report output
```
════════════════════════════════════════════════════════════════════════
  GTM INTELLIGENCE PLATFORM — DEAL CLUSTER REPORT
  Generated: 2026-05-25 09:15 UTC
  Total clusters: 3
════════════════════════════════════════════════════════════════════════

  [01] 🔴 HIGH — acme.com
       Confidence  : 91%
       Signals     : 7 across 3 source(s)
       Vendors     : Salesforce, Okta, Stripe
       Window      : 2026-05-01 → 2026-05-22 (21d)
       Sources     : ct_log, dns, wayback
       Summary     : acme.com: 7 signals via SSL certs + subdomain
                     discovery + page changes — likely adopting
                     Salesforce, Okta, Stripe (91% confidence)

  [02] 🟡 MEDIUM — globex.io
       Confidence  : 62%
       ...
```

---

## Running the Full Pipeline

### Programmatic (recommended)

```python
import asyncio
from pipeline.runner import PipelineRunner
from stage4_correlation import cluster_report

async def main():
    runner = PipelineRunner(
        target_domains=["acme.com", "globex.io"],
        github_orgs=["salesforce", "hubspot"],
        customer_names=["Acme Corp", "Globex"],
        enable_agent=True,   # Gemini autonomous investigation
    )

    # Option A: Raw signals only (Stage 1 + 2)
    signals = await runner.run_once()
    print(f"Collected {len(signals)} signals")

    # Option B: Full pipeline (Stage 1 → 2 → 3 → 4)
    signals, clusters = await runner.run_full_pipeline(window_days=30)

    # Filter to actionable results
    high = [c for c in clusters if c.tier.value == "high"]
    for c in high:
        print(c.summary)
        print(f"  Vendors: {c.vendors}")
        print(f"  Cluster ID: {c.cluster_id}")

asyncio.run(main())
```

### CLI

```bash
# Batch — collect signals once and print cluster report
python main.py --domains acme.com globex.io

# With GitHub monitoring
python main.py --domains acme.com \
               --github-orgs salesforce \
               --customer-names "Acme Corp" "Globex Industries"

# Agent mode — Gemini autonomously investigates
python main.py --domains acme.com --agent

# Stream mode — long-running (certstream + 5-min batch)
python main.py --domains acme.com --stream

# Module 2 — product launch sniper
python main.py --domains competitor.com --launch-sniper

# Save signals to JSON
python main.py --domains acme.com --output signals.json
```

### Stage-by-stage (for debugging)

```python
import asyncio
from stage1_signal_collection import CTLogPollingCollector, DNSCollector
from stage3_parsing import ParserOrchestrator
from stage4_correlation import CorrelationEngine, cluster_report

async def debug_pipeline():
    # Stage 1
    ct_signals  = await CTLogPollingCollector(["acme.com"]).collect_all()
    dns_signals = await DNSCollector(["acme.com"]).collect_all()
    all_signals = ct_signals + dns_signals
    print(f"Stage 1: {len(all_signals)} raw signals")

    # Stage 3
    parser = ParserOrchestrator()
    parsed = await parser.parse(all_signals)
    print(f"Stage 3: {len(parsed)} parsed signals")
    for p in parsed[:3]:
        print(f"  {p.target_domain} | {p.signal_type.value} | vendors={p.vendor_hints}")

    # Stage 4
    engine = CorrelationEngine(window_days=30)
    clusters = await engine.correlate_signals(parsed)
    print(cluster_report(clusters))

asyncio.run(debug_pipeline())
```

---

## Signal Schema

```python
Signal(
    signal_id      = "uuid4",
    source         = SignalSource.CT_LOG | DNS | WAYBACK | GITHUB | ...,
    target_domain  = "acme.com",
    signal_type    = SignalType.NEW_CERT | NEW_SUBDOMAIN | ...,
    timestamp      = datetime(utc),      # when event occurred
    detected_at    = datetime(utc),      # when we detected it
    raw_data       = {...},              # full source payload
    vendor_hint    = "Salesforce",       # quick attribution
    confidence     = 0.0–1.0,           # pre-correlation score
    metadata       = {...},              # parser-specific extras
)
```

### Signal types

| Source | Signal Type | Confidence Range |
|--------|-------------|-----------------|
| CT Log | `new_cert` | 0.60–0.75 |
| CT Log | `new_wildcard_cert` | 0.72–0.85 |
| DNS | `new_subdomain` | 0.45–0.80 |
| Wayback | `new_integration_page` | 0.75–0.92 |
| Wayback | `page_content_change` | 0.55–0.65 |
| Wayback | `new_status_page` | 0.30 |
| GitHub | `commit_spike` | 0.40–0.88 |
| GitHub | `new_repo` | 0.50–0.85 |
| GitHub | `customer_name_in_commit` | 0.78–0.92 |

---

## Confidence Scoring

### Pre-correlation (per-signal)

Each Stage 1 collector sets a base confidence. Stage 3 parsers refine it:

- **CT**: wildcard cert → +0.15 | vendor-specific SAN → +0.10/vendor | Let's Encrypt short-lived → -0.05
- **DNS**: vendor prefix match → use fingerprint confidence | deep subdomain (depth≥3) → ×0.80 | re-seen FQDN → ×0.70
- **Wayback**: new URL → +0.10 | path-vendor match → +0.05 | status page → hard floor 0.30
- **GitHub**: spike ratio scales linearly | README mention → +0.05 vs. commit message

### Post-correlation (per-cluster)

See [Stage 4 — Confidence Scoring Formula](#confidence-scoring-formula) above.

---

## Environment Variables

| Variable | Required | Source | Description |
|----------|----------|--------|-------------|
| `GEMINI_API_KEY` | ✅ Yes | [aistudio.google.com](https://aistudio.google.com/apikey) | Free — 1,500 req/day |
| `GITHUB_TOKEN` | ✅ Yes | [github.com/settings/tokens](https://github.com/settings/tokens) | Free — 5,000 req/hr |
| `BRIGHT_DATA_USERNAME` | ⚡ Fallback | [brightdata.com](https://brightdata.com) | Web Unlocker zone username |
| `BRIGHT_DATA_PASSWORD` | ⚡ Fallback | Bright Data dashboard | Zone password |
| `SCRAPING_BROWSER_CDP_URL` | ⚡ Fallback | Bright Data → Scraping Browser | CDP WebSocket URL |
| `BRIGHT_DATA_BUDGET_USD` | Optional | — | Hard cap (default: `250.0`) |
| `SECURITY_TRAILS_API_KEY` | Optional | [securitytrails.com](https://securitytrails.com) | Passive DNS (6th DNS source) |
| `SLACK_WEBHOOK_URL` | Optional | Slack Apps | Alert webhook for HIGH clusters |
| `HUBSPOT_API_KEY` | Optional | HubSpot Private Apps | CRM enrichment (Stage 6) |
| `DATABASE_URL` | Stage 6 | PostgreSQL | Signal + cluster persistence |

---

## File Structure

```
web_data_labai/
├── main.py                                     # CLI entry point
├── requirements.txt
├── .env / .env.example
│
├── models/
│   ├── signal.py                               # Signal + SignalSource + SignalType enums
│   ├── parsed_signal.py                        # ParsedSignal (Stage 3 → 4 contract)
│   └── deal_cluster.py                         # DealCluster (Stage 4 output)
│
├── config/
│   ├── settings.py                             # Env-var config with property helpers
│   └── vendor_fingerprints.json               # 36 subdomain + 6 path patterns
│
├── stage1_signal_collection/
│   ├── ct_log_collector.py                    # certstream + crt.sh
│   ├── dns_collector.py                       # 6 free sources + BD SERP fallback
│   ├── wayback_collector.py                   # CDX API + content diffing
│   └── github_collector.py                   # Async GitHub REST + NER
│
├── stage2_bright_data/
│   ├── budget_guard.py                        # $250 budget tracker + gate
│   ├── web_unlocker.py                        # smart_fetch (free-first + BD fallback)
│   ├── serp_api.py                            # DuckDuckGo free + BD Google fallback
│   ├── scraping_browser.py                    # Playwright CDP + free httpx fallback
│   └── mcp_orchestrator.py                   # Gemini 2.0 Flash agent loop
│
├── stage3_parsing/
│   ├── vendor_fingerprinter.py               # O(1) lookup: prefix / contains / path / text
│   ├── ct_log_parser.py                      # SAN extraction, issuer, lifespan
│   ├── dns_parser.py                         # FQDN validation, depth, first-seen
│   ├── wayback_parser.py                     # Path classification, difflib
│   ├── github_parser.py                      # spaCy NER + regex fallback
│   └── parser_orchestrator.py               # asyncio.gather × 4 parsers
│
├── stage4_correlation/
│   ├── confidence_scorer.py                  # Additive scoring formula
│   ├── signal_clusterer.py                   # pandas + NetworkX two-layer clustering
│   └── correlation_engine.py                 # Top-level orchestrator + cluster_report()
│
├── pipeline/
│   └── runner.py                             # PipelineRunner + run_full_pipeline()
│
└── tests/
    ├── test_signal_model.py                  # 4 tests
    ├── test_pipeline_dedup.py                # 5 tests
    ├── test_stage3_parsers.py                # 19 tests
    └── test_stage4_correlation.py            # 19 tests
                                              # Total: 47 passing ✅
```

---

## Idea 2 — Launch Sniper (`idea 2/launch_sniper/`)

> **Detect unreleased competitor product launches** from WHOIS registrations, USPTO trademark filings, and robots.txt changes — fully independent module, no overlap with the GTM platform above.

### How it Works

```
Competitor Domains
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 1 — Signal Collection (3 collectors)          │
│  WHOIS (new domain registrations by same org)        │
│  Trademark (USPTO IBD API — 60-day lookback)         │
│  Robots.txt (new suspicious Disallow paths)          │
└────────────────────┬────────────────────────────────┘
                     │  Signal objects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 2 — Bright Data MCP                          │
│  search_engine + scrape_as_markdown tools           │
│  Budget guard: warn at 80, hard-stop at 100 calls   │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 3 — Signal Parsing                           │
│  WHOIS → suspected product from new domain name     │
│  Trademark → product category (9-keyword table)     │
│  Robots.txt → product hint from Disallow path       │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 4 — Correlation Engine                       │
│  Groups signals by competitor domain                │
│  Confidence: 1 source=0.5 / 2=0.7 / 3=0.9         │
└────────────────────┬────────────────────────────────┘
                     │  Clusters
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 5 — AI Enrichment (gpt-4o-mini)              │
│  Suspected product, estimated launch date,          │
│  confidence adjustment, counter-playbook            │
└────────────────────┬────────────────────────────────┘
                     │  LaunchIntelligenceObjects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 6 — Delivery                                 │
│  Markdown report saved to disk                      │
│  Email via Resend API → Gmail SMTP fallback         │
│  Supabase PostgreSQL persistence (deduped by run)   │
│  Slack webhook (optional)                           │
└─────────────────────────────────────────────────────┘
```

### Setup

```bash
cd "idea 2/launch_sniper"
pip install -r requirements.txt

# Edit .env — minimum required keys:
# BRIGHT_DATA_MCP_TOKEN, OPENAI_API_KEY
```

### Run

```bash
# Monitor any competitor domains
python main.py --domains salesforce.com,hubspot.com

# Save report to custom path
python main.py --domains notion.com,linear.app --output reports/today.md

# Suppress verbose logs
python main.py --domains acme.com --quiet

# Also send Slack alerts (requires SLACK_WEBHOOK_URL in .env)
python main.py --domains acme.com --slack
```

### Environment Variables (idea 2)

| Variable | Required | Description |
|----------|----------|-------------|
| `BRIGHT_DATA_MCP_TOKEN` | Yes | Bright Data MCP token — WHOIS searches + robots.txt fetching |
| `OPENAI_API_KEY` | Yes | gpt-4o-mini — Stage 5 enrichment (~$0.0002/cluster) |
| `RESEND_API_KEY` | Optional | Email delivery of the report |
| `GMAIL_USER` + `GMAIL_APP_PASSWORD` | Optional | Gmail SMTP fallback for email delivery |
| `REPORT_EMAIL` | Optional | Recipient address (defaults to `GMAIL_USER`) |
| `DATABASE_URL` | Optional | Supabase PostgreSQL — persists signals + results across runs |
| `SLACK_WEBHOOK_URL` | Optional | Slack alerts for high-confidence (≥80%) detections |

### File Structure

```
idea 2/launch_sniper/
├── main.py                              # CLI entry point (--domains, --output, --slack, --quiet)
├── requirements.txt
├── .env                                 # credentials (gitignored)
│
├── models/
│   ├── signal.py                        # Signal dataclass + SignalSource/SignalType enums
│   └── launch_intelligence.py          # LaunchIntelligenceObject + CounterPlaybook
│
├── collectors/
│   ├── whois_collector.py              # python-whois + BrightData MCP search
│   ├── trademark_collector.py          # USPTO IBD API with retry
│   └── robots_txt_collector.py        # BrightData MCP scrape + diff vs. cached state
│
├── stage2_brightdata/
│   └── mcp_client.py                   # SSE MCP client, budget guard, retry logic
│
├── stage3_parsing/
│   └── parser_orchestrator.py         # Concurrent signal enrichment
│
├── stage4_correlation/
│   └── correlation_engine.py          # Groups + scores clusters by domain
│
├── stage5_enrichment/
│   └── launch_enricher.py             # GPT-4o-mini JSON enrichment per cluster
│
├── stage6_delivery/
│   ├── report_generator.py            # Markdown report builder
│   ├── email_notifier.py              # Resend + Gmail SMTP fallback
│   ├── db_writer.py                   # Supabase PostgreSQL writer (deduped signals)
│   └── slack_notifier.py             # Slack webhook alerts
│
└── state/
    └── robots_cache.json              # Persists robots.txt between runs (gitignored)
```

---

## Roadmap

| Stage | Status | Description |
|-------|--------|-------------|
| Stage 1 — Signal Collection | ✅ Done | 4 collectors, certstream, crt.sh, 6 DNS sources, Wayback CDX, GitHub REST |
| Stage 2 — Bright Data Layer | ✅ Done | BudgetGuard, smart_fetch, DuckDuckGo, BD fallback, Gemini MCP agent |
| Stage 3 — Parsing & NER | ✅ Done | 4 parsers, VendorFingerprinter, spaCy NER with regex fallback |
| Stage 4 — Correlation Engine | ✅ Done | pandas rolling window + NetworkX bipartite clustering + confidence scorer |
| Stage 5 — AI Enrichment | 🔜 Next | Gemini synthesises clusters → `DealIntelligenceObject` with outreach window |
| Stage 6 — Delivery | 🔜 Next | PostgreSQL ORM, HubSpot CRM push, Slack HIGH-confidence webhooks |
