# GTM Intelligence Platform — Claude Code Context

## Project Overview

A 6-stage pipeline that collects public web signals (SSL certs, DNS, Wayback,
GitHub), correlates them into deal clusters, and uses GPT-4o-mini to generate
actionable B2B sales intelligence (vendor attribution, deal close dates,
outreach windows).

```
Stage 1 → Signal Collection  (CT logs, DNS, Wayback, GitHub)
Stage 2 → Bright Data MCP    (SERP + web fetch via BD REST + MCP tools)
Stage 3 → Parsing            (VendorFingerprinter, NER, confidence scoring)
Stage 4 → Correlation        (pandas rolling window + NetworkX bipartite graph)
Stage 5 → AI Enrichment      (OpenAI gpt-4o-mini, $0.15/1M tokens)
Stage 6 → Delivery           (FastAPI, PostgreSQL, HubSpot, Slack)  ← next
```

## Bright Data MCP — Primary Web Data Tool

This project uses the **Bright Data MCP server** as the primary tool for ALL
web data operations. You have direct access to it through the MCP configuration.

### MCP Server

The Bright Data MCP server is pre-configured in `.claude/settings.json`:
```
https://mcp.brightdata.com/mcp?token=b789f207-6414-46a4-9fe7-91e7a4fad2d9
```

**Always prefer Bright Data MCP tools over built-in WebFetch/WebSearch.**

### Learn the Full Skill Set

Read and internalize ALL skills from the official Bright Data skills GitHub:
👉 **https://github.com/brightdata/skills**

Key skills to master for this project:

| Skill | GitHub path | Purpose |
|-------|-------------|---------|
| `agent-onboarding` | `skills/agent-onboarding/SKILL.md` | Setup, auth, path selection |
| `bright-data-mcp` | `skills/bright-data-mcp/SKILL.md` | 60+ MCP tools overview |
| `search` | `skills/search/SKILL.md` | `bdata search` + `bdata discover` |
| `scrape` | `skills/scrape/SKILL.md` | `bdata scrape` (markdown/HTML/screenshot) |
| `data-feeds` | `skills/data-feeds/SKILL.md` | Structured data (LinkedIn, Crunchbase, …) |
| `bright-data-best-practices` | `skills/bright-data-best-practices/SKILL.md` | REST API reference |
| `competitive-intel` | `skills/competitive-intel/SKILL.md` | Competitor analysis |

### Active Credentials

```
BRIGHT_DATA_API_KEY  = b789f207-6414-46a4-9fe7-91e7a4fad2d9
BRIGHT_DATA_SERP_ZONE = serp_api          ← only active zone
BRIGHT_DATA_WEB_UNLOCKER_ZONE = (none)    ← not provisioned; use SERP zone
MCP URL = https://mcp.brightdata.com/mcp?token=b789f207-6414-46a4-9fe7-91e7a4fad2d9
```

### API Quick Reference

```python
# SERP search (brd_json=1 for parsed results)
POST https://api.brightdata.com/request
{
  "zone": "serp_api",
  "url": "https://www.google.com/search?q=site:*.acme.com&brd_json=1",
  "format": "raw"
}

# Web content fetch (use SERP zone as general unlocker)
POST https://api.brightdata.com/request
{
  "zone": "serp_api",
  "url": "https://acme.com/integrations",
  "format": "raw",
  "data_format": "markdown"
}
```

### Tool Selection Guide

```
Need to search Google SERP            → BD MCP search tool  / serp_api zone
Need to scrape a web page             → BD MCP scrape tool  / serp_api zone (data_format=markdown)
Need structured data (LinkedIn, etc.) → BD MCP data-feeds tool
Need browser automation               → BD MCP browser tool
CT logs / crt.sh (free API)           → direct httpx (no BD needed)
GitHub REST API (free)                → direct httpx with GITHUB_TOKEN
```

## Python Architecture

### Key Files

```
config/settings.py              — all credentials + properties (has_bright_data, etc.)
stage2_bright_data/
  web_unlocker.py               — smart_fetch() uses serp_api zone
  serp_api.py                   — SERPClient → BrightDataSERPClient → DDG fallback
  mcp_orchestrator.py           — scripted OSINT playbook (no Gemini)
  budget_guard.py               — thread-safe $250 spend cap
models/
  signal.py                     — Signal dataclass
  deal_cluster.py               — DealCluster dataclass
  deal_intelligence.py          — DealIntelligenceObject dataclass
stage5_ai_enrichment/
  ai_enricher.py                — AIEnricher (gpt-4o-mini, JSON mode)
pipeline/runner.py              — PipelineRunner orchestrates all stages
```

### Virtual Environment

```bash
source .venv/bin/activate       # always activate before running
.venv/bin/python -m pytest -m "not integration" -q   # run tests
.venv/bin/python -m pytest -m integration -v -s       # live API tests
```

### Signal Types Available

`new_cert`, `new_subdomain`, `dns_change`, `page_change`, `new_integration_page`,
`github_spike`, `robots_change`, `wayback_new_page`, `wayback_content_change`,
`github_new_repo`, `trademark_filing`

## Bright Data Best Practices (inlined from GitHub skills)

### REST API — SERP Zone

```python
import httpx

async def bd_serp(query: str, api_key: str, zone: str = "serp_api") -> dict:
    """Google SERP search via Bright Data REST API."""
    encoded = query.replace(" ", "+")
    google_url = f"https://www.google.com/search?q={encoded}&brd_json=1&gl=us"
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.post(
            "https://api.brightdata.com/request",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"zone": zone, "url": google_url, "format": "raw"},
        )
        resp.raise_for_status()
        return resp.json()  # {"organic": [...], "paid": [...], ...}
```

### REST API — Content Fetch (markdown)

```python
async def bd_fetch_markdown(url: str, api_key: str, zone: str = "serp_api") -> str:
    """Fetch any page as clean markdown via Bright Data."""
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.post(
            "https://api.brightdata.com/request",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"zone": zone, "url": url, "format": "raw", "data_format": "markdown"},
        )
        resp.raise_for_status()
        return resp.text
```

### Error Codes

| HTTP | Meaning | Fix |
|------|---------|-----|
| 400  | Bad request body | Check `format`, `zone`, `url` fields present |
| 401  | Bad API key | Check `BRIGHT_DATA_API_KEY` |
| 404  | Zone not found | Check `BRIGHT_DATA_SERP_ZONE=serp_api` in `.env` |
| 407  | Proxy auth fail | Use bearer token REST API (not proxy) |

## OpenAI (Stage 5)

```
Model : gpt-4o-mini
Cost  : $0.15/1M input + $0.60/1M output
Budget: $15 → ~50,000 cluster enrichments
Key   : stored in .env (OPENAI_API_KEY)
```

Always use `response_format={"type": "json_object"}` and `temperature=0.2` for
structured extraction tasks.

## Testing

```bash
# All unit tests (no API calls)
.venv/bin/python -m pytest -m "not integration" -q

# Stage 5 live (costs ~$0.0002 per run)
.venv/bin/python -m pytest tests/test_stage5_ai_enrichment.py -m integration -v -s
```

Currently: **69 tests pass**.

## Next: Stage 6 — Delivery Layer

To be built:
- `models/db.py` — SQLAlchemy models (Signal, DealCluster, DealIntelligenceObject)
- `api/routes.py` — FastAPI endpoints: `POST /run`, `GET /clusters`, `GET /signals`
- `stage6_delivery/hubspot.py` — Push HIGH-tier clusters to HubSpot CRM
- `stage6_delivery/slack.py` — Slack webhook alerts for HIGH-tier deals
