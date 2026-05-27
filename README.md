# GTM Intelligence Platform

> **Autonomous AI-powered competitor intelligence** that detects hidden enterprise deals and product launches by mining public technical signals — SSL certificates, DNS subdomains, Wayback Machine snapshots, GitHub activity, WHOIS registrations, and USPTO trademark filings.

Live dashboard — dark glassmorphic UI, holographic stat cards, expandable intelligence tables, one-click email digests.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Quick Start](#quick-start)
3. [Stage 6 — Delivery Platform (Web UI + API)](#stage-6--delivery-platform)
4. [Stage 5 — AI Enrichment](#stage-5--ai-enrichment)
5. [Stage 1 — Signal Collection](#stage-1--signal-collection)
6. [Stage 2 — Bright Data Ingestion Layer](#stage-2--bright-data-ingestion-layer)
7. [Stage 3 — Parsing & Normalisation](#stage-3--parsing--normalisation)
8. [Stage 4 — Correlation Engine](#stage-4--correlation-engine)
9. [Idea 2 — Launch Sniper](#idea-2--launch-sniper)
10. [Running the Full Pipeline](#running-the-full-pipeline)
11. [CLI Reference](#cli-reference)
12. [Signal Schema](#signal-schema)
13. [Confidence Scoring](#confidence-scoring)
14. [Environment Variables](#environment-variables)
15. [File Structure](#file-structure)
16. [Roadmap](#roadmap)

---

## Architecture Overview

```
Target Competitor Domains
          │
          ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 1 — Signal Collection (4 collectors)          │
│  CT Logs │ DNS Subdomains │ Wayback │ GitHub         │
└────────────────────┬────────────────────────────────┘
                     │  raw Signal objects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 2 — Bright Data Ingestion Layer               │
│  Web Unlocker │ SERP API │ Scraping Browser │ Agent  │
│  (free-first strategy — Bright Data only as fallback)│
└────────────────────┬────────────────────────────────┘
                     │  deduplicated Signal list
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 3 — Parsing & Normalisation (4 parsers)       │
│  CT Parser │ DNS Parser │ Wayback Parser │ GH Parser  │
│  + VendorFingerprinter (subdomain → vendor lookup)   │
└────────────────────┬────────────────────────────────┘
                     │  ParsedSignal objects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 4 — Correlation Engine                        │
│  Layer 1: pandas 30-day rolling window               │
│  Layer 2: NetworkX bipartite graph clustering        │
│  ConfidenceScorer: diversity + pairings + penalties  │
└────────────────────┬────────────────────────────────┘
                     │  DealCluster objects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 5 — AI Enrichment (GPT-4o-mini)               │
│  Synthesises clusters → DealIntelligenceObjects      │
│  Suspected vendor, category, outreach window, tier   │
└────────────────────┬────────────────────────────────┘
                     │  DealIntelligenceObjects
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 6 — Delivery Platform                         │
│  FastAPI REST API  │  React + Vite + Tailwind v4     │
│  PostgreSQL ORM    │  Gmail SMTP digest emails       │
│  GTM Dashboard     │  Launch Sniper Dashboard        │
└─────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Clone and create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt
pip install "fastapi[standard]" sqlalchemy psycopg2-binary python-dotenv

# 3. Configure credentials
cp .env.example .env
# Edit .env — see Environment Variables section below

# 4. Start the FastAPI backend
uvicorn stage6.backend.main:app --reload --port 8000
# → http://localhost:8000/docs

# 5. Start the React frontend (separate terminal)
cd stage6/frontend
npm install
npm run dev
# → http://localhost:5173
```

### Minimum viable config (free only)

```env
GEMINI_API_KEY=your_free_gemini_key     # https://aistudio.google.com/apikey
GITHUB_TOKEN=ghp_your_github_token     # https://github.com/settings/tokens
OPENAI_API_KEY=sk-proj-...             # https://platform.openai.com
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

> Bright Data credentials are **optional** — the platform runs fully on free APIs and only invokes Bright Data as a fallback when direct requests are blocked.

---

## Stage 6 — Delivery Platform

The full-stack delivery layer: a dark glassmorphic React dashboard backed by a FastAPI REST API with PostgreSQL persistence.

### Backend (`stage6/backend/`)

```bash
uvicorn stage6.backend.main:app --reload --port 8000
```

#### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/run` | Trigger full pipeline (Stages 1–5) in background |
| `GET` | `/api/stats` | Live counts — signals, clusters, intel records, tier breakdown, AI spend |
| `GET` | `/api/intelligence` | All deal intelligence records (filterable by `?tier=high`) |
| `POST` | `/api/ingest` | Ingest JSON sample data from `stage6/sample_deals/` |
| `GET` | `/api/digest/preview` | Preview HTML email digest in browser |
| `POST` | `/api/digest/send` | Send deal intelligence digest via Gmail SMTP |
| `POST` | `/api/sniper/run` | Trigger Launch Sniper pipeline in background |
| `GET` | `/api/sniper/stats` | Aggregate sniper stats (scans, domains, launches, tiers) |
| `GET` | `/api/sniper/results` | All detected competitor launches (filterable by `?tier=` & `?domain=`) |
| `GET` | `/api/sniper/status/{run_id}` | Live status + output for a specific sniper run |
| `GET` | `/api/sniper/runs` | List all sniper runs (newest first) |
| `POST` | `/api/sniper/email` | Send Launch Sniper report via Gmail SMTP |
| `GET` | `/health` | Health check |

#### Database models

- **`SignalDB`** — raw signals persisted across pipeline runs
- **`DealClusterDB`** — correlated clusters (Stage 4 output)
- **`DealIntelligenceDB`** — AI-enriched intel records (Stage 5 output)

### Frontend (`stage6/frontend/`)

```bash
cd stage6/frontend && npm run dev   # http://localhost:5173
```

**Tech stack:** React 18 · Vite 5 · Tailwind CSS v4 · Framer Motion · hls.js · Axios

#### Landing Page

Full-screen dark hero with:
- HLS video background (Mux-hosted) with graceful fallback
- Liquid-glass glassmorphic navbar (Signals · Intelligence · Pipeline)
- Typewriter-animated headline + email capture CTA
- Scrolling sections: Ghost Pipeline Detector · Launch Sniper · CTA with testimonial

#### GTM Intelligence Dashboard

| Section | What it shows |
|---------|---------------|
| **Live Metrics** | 6 holographic stat cards — Signals, Clusters, Intel Records, HIGH/MEDIUM priority, AI Spend |
| **Deal Intelligence Feed** | Expandable table — Company, Vendor, Category, Confidence bar, Close date, Outreach window, Tier badge; expanded row shows AI reasoning + signal list |
| **Pipeline Controls** | 3 control cards — Run Pipeline (domains + GitHub orgs + window), Ingest Sample Data, Email Digest |

#### Launch Sniper Dashboard

| Section | What it shows |
|---------|---------------|
| **Live Metrics** | 6 holographic stat cards — Total Scans, Domains Scanned, Launches Detected, HIGH/MEDIUM Confidence, Active Scans (live spinner) |
| **Detected Launches** | Expandable table — Competitor + Avatar, Suspected Product, Confidence bar (green→cyan), Signal count, Tier badge, Scan date; expanded row shows signals list + counter-playbook |
| **Sniper Controls** | 3 control cards — Run Sniper (domain input), Filter Results (tier + domain keyword), Email Report (Gmail SMTP) |

#### Design system

| Token | Value |
|-------|-------|
| Background | `#0a0b10` |
| Surface | `#11131c` |
| Cyan accent | `#00dbe7` / `#74f5ff` |
| Green accent | `#4ae176` |
| Purple accent | `#6f00be` / `#ddb7ff` |
| Headline font | Hanken Grotesk 800 |
| UI font | Inter 300–600 |
| Label font | JetBrains Mono 700 (all-caps) |

---

## Stage 5 — AI Enrichment

GPT-4o-mini synthesises raw `DealCluster` objects into actionable `DealIntelligenceObject` records.

```python
from stage6.backend.pipeline import enrich_clusters   # or stage5 module directly

intel = await enrich_clusters(clusters)
# Returns list[DealIntelligenceObject]:
#   target_company, suspected_vendor, vendor_category,
#   confidence (float), tier (high/medium/low),
#   deal_closed_estimate, outreach_window,
#   signals (list[str]), reasoning (str),
#   new_vendor_patterns (list[{pattern, vendor}])
```

**Cost:** ~$0.0002 per cluster (GPT-4o-mini at current pricing).

---

## Stage 1 — Signal Collection

Four concurrent async collectors, all free:

### CT Log Collector
```python
from stage1_signal_collection import CTLogPollingCollector, CTLogStreamingCollector

# Batch: query crt.sh for all certs issued in the last 30 days
collector = CTLogPollingCollector(target_domains=["acme.com"], since_days=30)
signals = await collector.collect_all()

# Live stream: certstream WebSocket (real-time)
collector = CTLogStreamingCollector(
    target_domains={"acme.com", "globex.io"},
    on_signal=lambda s: print(s.signal_type, s.raw_data["cert_domain"]),
)
await collector.run()
```

**What it detects:** New SSL certs for `crm.acme.com`, `*.acme.com`, `salesforce.acme.com` — each SAN prefix is cross-referenced against the vendor fingerprint table.

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
    diff_window_days=30,
    diff_content=True,
)
signals = await collector.collect_all()
```

**Monitored paths:** `/integrations*`, `/partners*`, `/marketplace*`, `/changelog*`, `/pricing*`, `/customers*`, `/beta*`, `/launch*`, `/docs*`, `/status*`

---

### GitHub Collector
```python
from stage1_signal_collection import GitHubOrgCollector

collector = GitHubOrgCollector(
    org_names=["salesforce", "hubspot"],
    customer_names=["Acme Corp", "Globex Industries"],
    lookback_days=30,
    spike_threshold=10,
    max_repos=20,
)
signals = await collector.collect_all()
```

---

## Stage 2 — Bright Data Ingestion Layer

### Budget Guard

```python
from stage2_bright_data import BudgetGuard

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

result = await smart_fetch("https://crt.sh/?q=%.acme.com&output=json", json_response=True)
```

### MCP Agent (Gemini-powered)
```python
from stage2_bright_data import MCPAgentOrchestrator

agent = MCPAgentOrchestrator()
signals = await agent.investigate_domain("acme.com")
signals = await agent.detect_product_launch("competitor.com", product_keywords=["v2", "platform"])
```

---

## Stage 3 — Parsing & Normalisation

```python
from stage3_parsing import ParserOrchestrator

orch = ParserOrchestrator()
parsed_signals = await orch.parse(raw_signals)
```

| Parser | Key extractions |
|--------|----------------|
| **CT Log** | SAN list, issuer, lifespan, wildcard flag, vendor hints |
| **DNS** | FQDN validity, subdomain depth, first-seen flag, vendor category |
| **Wayback** | Path type, URL vendor slugs, content tokens, new-vs-changed |
| **GitHub** | Commit velocity, spike ratio, spaCy NER, repo topic |

---

## Stage 4 — Correlation Engine

```python
from stage4_correlation import CorrelationEngine, cluster_report

engine = CorrelationEngine(window_days=30)
clusters = await engine.correlate_raw(raw_signals)
print(cluster_report(clusters))
```

### Two-layer clustering

**Layer 1 — pandas rolling window:** Group by `target_domain` → 30-day window → emit if `signal_count ≥ 2`

**Layer 2 — NetworkX bipartite graph:** `signal_ids ↔ vendor_names` edges → `connected_components()` merges vendor-cohesive sub-clusters

### Confidence scoring formula

```
score = weighted_avg(signal.confidence)
      + 0.15 × (unique_sources - 1)
      + 0.10 × min(unique_vendors, 3)
      + 0.05 × (unique_signal_types - 1)
      + 0.10  if CT_LOG + DNS co-occur
      + 0.08  if GitHub commit spike
      + 0.08  if Wayback integration page
      - 0.10  if only 1 source
      - 0.05  if window > 25 days
      capped at [0.0, 1.0]
```

### Confidence tiers

| Tier | Score | Meaning |
|------|-------|---------|
| 🔴 **HIGH** | ≥ 0.80 | 3+ signals, multiple sources, vendor-corroborated |
| 🟡 **MEDIUM** | ≥ 0.55 | 2 signals or single strong source |
| ⚪ **LOW** | < 0.55 | Single weak signal — watch list |

---

## Idea 2 — Launch Sniper

> **Detect unreleased competitor product launches** from WHOIS registrations, USPTO trademark filings, and robots.txt changes — 6-stage pipeline, fully independent of the GTM platform above.

### How it Works

```
Competitor Domains
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 1 — Signal Collection                         │
│  WHOIS (new domain registrations by same org)        │
│  Trademark (USPTO IBD API — 60-day lookback)         │
│  Robots.txt (new suspicious Disallow paths)          │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 2 — Bright Data MCP                           │
│  search_engine + scrape_as_markdown tools            │
│  Budget guard: warn at 80, hard-stop at 100 calls    │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 3 — Signal Parsing                            │
│  WHOIS → suspected product from new domain name      │
│  Trademark → product category (9-keyword table)      │
│  Robots.txt → product hint from Disallow path        │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 4 — Correlation Engine                        │
│  Groups signals by competitor domain                 │
│  Confidence: 1 source=0.5 / 2=0.7 / 3=0.9          │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 5 — AI Enrichment (gpt-4o-mini)               │
│  Suspected product, estimated launch date,           │
│  confidence adjustment, counter-playbook             │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 6 — Delivery                                  │
│  Markdown report saved to disk                       │
│  Email via Resend API → Gmail SMTP fallback          │
│  Supabase PostgreSQL persistence                     │
│  Slack webhook (optional)                            │
│  REST API: /api/sniper/* served by Stage 6 backend   │
└─────────────────────────────────────────────────────┘
```

### CLI

```bash
cd "idea 2/launch_sniper"
pip install -r requirements.txt

python main.py --domains salesforce.com,hubspot.com
python main.py --domains notion.com,linear.app --output reports/today.md
python main.py --domains acme.com --quiet
python main.py --domains acme.com --slack
```

### Via Dashboard (recommended)

Start the Stage 6 backend and navigate to **Launch Sniper** in the sidebar:

1. Enter competitor domains in **Run Sniper** control card
2. Watch live metric cards update as the pipeline runs
3. Browse **Detected Launches** table — confidence bars, tier badges, expandable signals + counter-playbook
4. Filter by tier or domain keyword via **Filter Results**
5. Send the full report to any email via **Email Report**

---

## Running the Full Pipeline

### Programmatic

```python
import asyncio
from pipeline.runner import PipelineRunner

async def main():
    runner = PipelineRunner(
        target_domains=["acme.com", "globex.io"],
        github_orgs=["salesforce", "hubspot"],
        customer_names=["Acme Corp", "Globex"],
        enable_agent=True,
    )
    signals, clusters = await runner.run_full_pipeline(window_days=30)
    high = [c for c in clusters if c.tier.value == "high"]
    for c in high:
        print(c.summary)

asyncio.run(main())
```

### CLI

```bash
python main.py --domains acme.com globex.io
python main.py --domains acme.com --github-orgs salesforce --customer-names "Acme Corp"
python main.py --domains acme.com --agent
python main.py --domains acme.com --stream
python main.py --domains competitor.com --launch-sniper
python main.py --domains acme.com --output signals.json
```

---

## Signal Schema

```python
Signal(
    signal_id      = "uuid4",
    source         = SignalSource.CT_LOG | DNS | WAYBACK | GITHUB | ...,
    target_domain  = "acme.com",
    signal_type    = SignalType.NEW_CERT | NEW_SUBDOMAIN | ...,
    timestamp      = datetime(utc),
    detected_at    = datetime(utc),
    raw_data       = {...},
    vendor_hint    = "Salesforce",
    confidence     = 0.0–1.0,
    metadata       = {...},
)
```

| Source | Signal Type | Confidence Range |
|--------|-------------|-----------------|
| CT Log | `new_cert` | 0.60–0.75 |
| CT Log | `new_wildcard_cert` | 0.72–0.85 |
| DNS | `new_subdomain` | 0.45–0.80 |
| Wayback | `new_integration_page` | 0.75–0.92 |
| Wayback | `page_content_change` | 0.55–0.65 |
| GitHub | `commit_spike` | 0.40–0.88 |
| GitHub | `customer_name_in_commit` | 0.78–0.92 |

---

## Confidence Scoring

### Pre-correlation (per-signal)

- **CT**: wildcard cert → +0.15 | vendor-specific SAN → +0.10/vendor | Let's Encrypt short-lived → -0.05
- **DNS**: vendor prefix match → use fingerprint confidence | deep subdomain (depth≥3) → ×0.80
- **Wayback**: new URL → +0.10 | path-vendor match → +0.05 | status page → floor 0.30
- **GitHub**: spike ratio scales linearly | README mention +0.05 vs commit message

### Post-correlation (per-cluster)

See [Stage 4 Confidence Formula](#confidence-scoring-formula) above.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | ✅ Yes | GPT-4o-mini — Stage 5 enrichment + Launch Sniper enrichment |
| `DATABASE_URL` | ✅ Yes | PostgreSQL — `postgresql://user:pass@host:5432/dbname` |
| `GEMINI_API_KEY` | ✅ Yes | Gemini MCP agent (free — 1,500 req/day) |
| `GITHUB_TOKEN` | ✅ Yes | GitHub REST API (free — 5,000 req/hr) |
| `GMAIL_USER` | 📧 Email | Gmail address for SMTP digest + sniper reports |
| `GMAIL_APP_PASSWORD` | 📧 Email | Gmail App Password (16-char, spaces OK) — [generate here](https://myaccount.google.com/apppasswords) |
| `RESEND_API_KEY` | 📧 Email | Resend API (alternative to Gmail SMTP) |
| `BRIGHT_DATA_API_KEY` | ⚡ Fallback | Bright Data zone key — Web Unlocker + SERP |
| `BRIGHT_DATA_USERNAME` | ⚡ Fallback | Bright Data zone username |
| `BRIGHT_DATA_PASSWORD` | ⚡ Fallback | Bright Data zone password |
| `SCRAPING_BROWSER_CDP_URL` | ⚡ Fallback | Bright Data Scraping Browser CDP URL |
| `BRIGHT_DATA_BUDGET_USD` | Optional | Hard spend cap (default: `250.0`) |
| `SECURITY_TRAILS_API_KEY` | Optional | 6th DNS source (passive DNS) |
| `SLACK_WEBHOOK_URL` | Optional | Slack webhook for HIGH-confidence alerts |
| `REPORT_EMAIL` | Optional | Sniper report recipient (defaults to `GMAIL_USER`) |

> **Security:** `.env` is gitignored — never commit credentials.

---

## File Structure

```
Hi_Labs_WebData/
├── README.md
├── requirements.txt
├── .env                                        # gitignored
├── main.py                                     # GTM pipeline CLI entry point
│
├── models/
│   ├── signal.py
│   ├── parsed_signal.py
│   └── deal_cluster.py
│
├── config/
│   ├── settings.py
│   └── vendor_fingerprints.json
│
├── stage1_signal_collection/
│   ├── ct_log_collector.py
│   ├── dns_collector.py
│   ├── wayback_collector.py
│   └── github_collector.py
│
├── stage2_bright_data/
│   ├── budget_guard.py
│   ├── web_unlocker.py
│   ├── serp_api.py
│   ├── scraping_browser.py
│   └── mcp_orchestrator.py
│
├── stage3_parsing/
│   ├── vendor_fingerprinter.py
│   ├── ct_log_parser.py
│   ├── dns_parser.py
│   ├── wayback_parser.py
│   ├── github_parser.py
│   └── parser_orchestrator.py
│
├── stage4_correlation/
│   ├── confidence_scorer.py
│   ├── signal_clusterer.py
│   └── correlation_engine.py
│
├── pipeline/
│   └── runner.py
│
├── tests/
│   ├── test_signal_model.py          # 4 tests
│   ├── test_pipeline_dedup.py        # 5 tests
│   ├── test_stage3_parsers.py        # 19 tests
│   └── test_stage4_correlation.py   # 19 tests
│                                     # Total: 47 passing ✅
│
├── stage6/                           # ── Delivery Platform ─────────────────
│   ├── backend/
│   │   ├── main.py                   # FastAPI app (lifespan, CORS, routers)
│   │   ├── database.py               # SQLAlchemy engine + session + Base
│   │   ├── models.py                 # ORM models + Pydantic schemas
│   │   └── routes/
│   │       ├── deals.py              # /api/run, /api/stats, /api/intelligence, /api/ingest
│   │       ├── digest.py             # /api/digest/preview, /api/digest/send (Gmail SMTP)
│   │       └── sniper.py             # /api/sniper/* (run, stats, results, status, email)
│   │
│   ├── frontend/
│   │   ├── index.html                # Google Fonts, Material Symbols
│   │   ├── vite.config.js            # @tailwindcss/vite plugin, /api proxy
│   │   ├── package.json
│   │   └── src/
│   │       ├── main.jsx
│   │       ├── index.css             # Tailwind v4, liquid-glass, holographic cards
│   │       ├── App.jsx               # GTMDashboard + SniperDashboard + Sidebar
│   │       ├── LandingPage.jsx       # HLS hero + glass nav + scrolling sections
│   │       └── components/
│   │           └── ui/
│   │               └── holographic-card.jsx
│   │
│   ├── sample_deals/                 # JSON fixtures for /api/ingest
│   └── sniper_output/                # Markdown reports from sniper runs (gitignored)
│
└── idea 2/
    └── launch_sniper/
        ├── main.py                   # CLI entry (--domains, --output, --slack, --quiet)
        ├── requirements.txt
        ├── .env                      # gitignored
        │
        ├── models/
        │   ├── signal.py
        │   └── launch_intelligence.py
        │
        ├── collectors/
        │   ├── whois_collector.py
        │   ├── trademark_collector.py
        │   └── robots_txt_collector.py
        │
        ├── stage2_brightdata/
        │   └── mcp_client.py
        │
        ├── stage3_parsing/
        │   └── parser_orchestrator.py
        │
        ├── stage4_correlation/
        │   └── correlation_engine.py
        │
        ├── stage5_enrichment/
        │   └── launch_enricher.py
        │
        ├── stage6_delivery/
        │   ├── report_generator.py
        │   ├── email_notifier.py
        │   ├── db_writer.py
        │   └── slack_notifier.py
        │
        └── state/
            └── robots_cache.json     # gitignored
```

---

## Roadmap

| Stage | Status | Description |
|-------|--------|-------------|
| Stage 1 — Signal Collection | ✅ Done | 4 collectors: certstream, crt.sh, 6 DNS sources, Wayback CDX, GitHub REST |
| Stage 2 — Bright Data Layer | ✅ Done | BudgetGuard, smart_fetch, DuckDuckGo, BD fallback, Gemini MCP agent |
| Stage 3 — Parsing & NER | ✅ Done | 4 parsers, VendorFingerprinter, spaCy NER with regex fallback |
| Stage 4 — Correlation Engine | ✅ Done | pandas rolling window + NetworkX bipartite clustering + confidence scorer |
| Stage 5 — AI Enrichment | ✅ Done | GPT-4o-mini synthesises clusters → DealIntelligenceObject with outreach window |
| Stage 6 — Delivery Platform | ✅ Done | FastAPI REST API + React/Vite dark dashboard + Gmail digest + PostgreSQL ORM |
| Idea 2 — Launch Sniper | ✅ Done | 6-stage standalone pipeline + integrated into Stage 6 dashboard + email reports |
