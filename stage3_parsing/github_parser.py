"""
stage3_parsing/github_parser.py
---------------------------------
Stage 3 — GitHub Signal Parser.

Takes raw GITHUB Signals and extracts:
  • Commit velocity (commits/day over the detected window)
  • NER on commit messages to find org/company names
  • Repository topic/language classification
  • Contributor count delta (new contributor = possible customer onboarding signal)
  • Customer name matches from commit messages / README

NER Strategy:
  Uses a fast regex-based approach by default (no spaCy download needed).
  If spaCy with 'en_core_web_sm' is installed, it upgrades automatically
  to proper NER for higher recall on org names.

  Install spaCy model (optional — improves NER significantly):
    pip install spacy && python -m spacy download en_core_web_sm
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable

from models.signal import Signal, SignalSource, SignalType
from models.parsed_signal import ParsedSignal

logger = logging.getLogger(__name__)

# Try to load spaCy NLP — graceful degradation if not installed
_NLP = None

def _load_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP
    try:
        import spacy  # type: ignore
        _NLP = spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])
        logger.info("[GH-PARSER] spaCy NER loaded (en_core_web_sm)")
    except Exception:
        logger.info("[GH-PARSER] spaCy not available — using regex NER fallback")
        _NLP = False
    return _NLP


# Regex-based org name extractor (fallback)
# Matches patterns like "Acme Corp", "GlobalEx Inc", "SomeCo Ltd", etc.
_ORG_RE = re.compile(
    r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*"
    r"(?:\s+(?:Inc|Corp|LLC|Ltd|GmbH|AG|SAS|PLC|Co|Group|Technologies?|Solutions?|Systems?))?)\b"
)

# Repo topics → category classification
_TOPIC_CATEGORIES: dict[str, str] = {
    "crm": "crm", "salesforce": "crm", "hubspot": "crm",
    "payments": "payments", "stripe": "payments", "billing": "payments",
    "analytics": "analytics", "data": "data_platform",
    "infrastructure": "infra", "devops": "infra", "kubernetes": "infra",
    "api": "api", "sdk": "sdk", "integration": "integration",
    "machine-learning": "ml", "ai": "ml",
}


def _ner_orgs_spacy(text: str) -> list[str]:
    nlp = _load_nlp()
    if not nlp:
        return _ner_orgs_regex(text)
    doc = nlp(text[:5000])  # cap for performance
    return list({ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"})


def _ner_orgs_regex(text: str) -> list[str]:
    matches = _ORG_RE.findall(text)
    # Filter out common false positives
    _SKIP = {"The", "Inc", "Corp", "LLC", "Ltd", "GitHub", "Git", "Python", "API", "URL"}
    return list({m for m in matches if m not in _SKIP and len(m) > 2})


def _classify_repo(raw: dict) -> str:
    topics: list[str] = raw.get("topics", [])
    for topic in topics:
        cat = _TOPIC_CATEGORIES.get(topic.lower())
        if cat:
            return cat
    lang = (raw.get("language") or "").lower()
    if lang in ("python", "go", "rust", "java", "typescript"):
        return "backend_sdk"
    return "general"


def _parse_one(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    sig_type = signal.signal_type

    if sig_type == SignalType.NEW_REPO:
        return _parse_new_repo(signal)
    elif sig_type == SignalType.COMMIT_SPIKE:
        return _parse_commit_spike(signal)
    elif sig_type == SignalType.CUSTOMER_NAME_IN_COMMIT:
        return _parse_customer_mention(signal)
    else:
        # Generic fallback
        return ParsedSignal(
            signal=signal,
            entities={"raw": raw},
            confidence=signal.confidence,
        )


def _parse_new_repo(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    description = raw.get("description", "")
    topics = raw.get("topics", [])
    repo_name = raw.get("repo", "")
    language = raw.get("language")
    stars = raw.get("stars", 0)
    forks = raw.get("forks", 0)
    url = raw.get("url", "")

    category = _classify_repo(raw)

    # NER on description
    ner_orgs = _ner_orgs_spacy(description) if description else []

    # Confidence: new repo is generally low-confidence alone
    # Stars/forks boost it slightly (indicates real activity)
    base = 0.50
    if stars > 10:
        base += 0.08
    if forks > 5:
        base += 0.05
    if any(t in (description + " ".join(topics)).lower() for t in [
        "customer", "enterprise", "partner", "integration", "onboard"
    ]):
        base += 0.12

    return ParsedSignal(
        signal=signal,
        entities={
            "repo": repo_name,
            "description": description,
            "language": language,
            "topics": topics,
            "category": category,
            "stars": stars,
            "forks": forks,
            "url": url,
            "ner_orgs": ner_orgs,
        },
        vendor_hints=[],
        confidence=round(min(base, 0.85), 4),
    )


def _parse_commit_spike(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    repo = raw.get("repo", "")
    commits = raw.get("commits_in_window", 0)
    velocity = raw.get("commits_per_day", 0.0)
    threshold = raw.get("spike_threshold", 10)
    sample_sha = raw.get("sample_commit", "")

    # Magnitude of spike
    spike_ratio = velocity / max(threshold, 1)

    # Confidence scales with spike severity
    base = min(0.40 + 0.10 * spike_ratio, 0.88)

    return ParsedSignal(
        signal=signal,
        entities={
            "repo": repo,
            "commits_in_window": commits,
            "commits_per_day": velocity,
            "spike_ratio": round(spike_ratio, 2),
            "threshold": threshold,
            "sample_sha": sample_sha,
        },
        vendor_hints=[],
        confidence=round(base, 4),
    )


def _parse_customer_mention(signal: Signal) -> ParsedSignal:
    raw = signal.raw_data
    message = raw.get("commit_message", raw.get("readme_snippet", ""))
    matched = raw.get("matched_customers", [])
    repo = raw.get("repo", "")
    commit_sha = raw.get("commit_sha", "")
    source_file = raw.get("file", "commit")

    # NER on commit message to find additional org names
    ner_orgs = _ner_orgs_spacy(message)
    all_orgs = list(dict.fromkeys(matched + [o for o in ner_orgs if o not in matched]))

    base = 0.82 if matched else 0.55
    if source_file == "README":
        base = min(base + 0.05, 0.92)   # README mentions = public, high signal

    return ParsedSignal(
        signal=signal,
        entities={
            "repo": repo,
            "commit_sha": commit_sha,
            "source_file": source_file,
            "message_snippet": message[:300],
            "matched_customers": matched,
            "ner_orgs": all_orgs,
        },
        vendor_hints=[],
        confidence=round(base, 4),
    )


class GitHubParser:
    """
    Async batch parser for GITHUB signals.

    Initialising the class triggers the one-time spaCy model load
    (or graceful fallback to regex NER).

    Usage
    -----
        parser = GitHubParser()
        parsed = await parser.parse_all(github_signals)
    """

    def __init__(self) -> None:
        # Trigger lazy load at startup so first parse isn't slow
        asyncio.get_event_loop().run_in_executor(None, _load_nlp)

    async def parse(self, signal: Signal) -> ParsedSignal | None:
        if signal.source != SignalSource.GITHUB:
            return None
        return await asyncio.to_thread(_parse_one, signal)

    async def parse_all(self, signals: Iterable[Signal]) -> list[ParsedSignal]:
        gh_signals = [s for s in signals if s.source == SignalSource.GITHUB]
        if not gh_signals:
            return []
        results = await asyncio.gather(
            *[self.parse(s) for s in gh_signals], return_exceptions=True
        )
        out = [r for r in results if isinstance(r, ParsedSignal)]
        logger.info("[GH-PARSER] Parsed %d/%d GitHub signals", len(out), len(gh_signals))
        return out
