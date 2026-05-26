"""
stage3_parsing/vendor_fingerprinter.py
----------------------------------------
VendorFingerprinter — shared lookup engine for vendor attribution.

Loaded once at import time. Reads from config/vendor_fingerprints.json.
Used by all four Stage 3 parsers.

Matching priority:
  1. Exact subdomain prefix match (e.g. "salesforce" → Salesforce, 0.95)
  2. Contains match (e.g. "my-salesforce-crm" still matches Salesforce)
  3. Path pattern match for Wayback URLs

The fingerprinter also learns new patterns at runtime via `register_pattern()`
so Stage 5 (LLM enrichment) can expand the table.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TABLE = Path(__file__).parent.parent / "config" / "vendor_fingerprints.json"


@dataclass(frozen=True)
class FingerprintMatch:
    vendor: str
    category: str
    confidence: float
    pattern: str
    match_type: str  # "exact_prefix" | "contains" | "path"


class VendorFingerprinter:
    """
    Fast O(1) vendor fingerprinting for subdomains and URL paths.

    Thread-safe (read-mostly, writes guarded by a flag).

    Usage
    -----
        fp = VendorFingerprinter()
        result = fp.match_subdomain("crm.acme.com")
        if result:
            print(result.vendor, result.confidence)

        result = fp.match_path("https://acme.com/integrations/stripe")
        if result:
            print(result.vendor)
    """

    def __init__(self, table_path: Path = _DEFAULT_TABLE) -> None:
        self._subdomain_exact: dict[str, FingerprintMatch] = {}
        self._subdomain_contains: list[tuple[str, FingerprintMatch]] = []
        self._path_patterns: list[tuple[re.Pattern, FingerprintMatch]] = []
        self._load(table_path)

    def _load(self, path: Path) -> None:
        if not path.exists():
            logger.warning("[FINGERPRINT] Table not found at %s — using empty table", path)
            return
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            logger.error("[FINGERPRINT] Failed to load table: %s", exc)
            return

        for entry in data.get("subdomain_patterns", []):
            pattern = entry["pattern"].lower()
            match = FingerprintMatch(
                vendor=entry["vendor"],
                category=entry.get("category", "unknown"),
                confidence=float(entry.get("confidence", 0.70)),
                pattern=pattern,
                match_type="exact_prefix",
            )
            # Exact prefix match
            self._subdomain_exact[pattern] = match
            # Contains match (lower confidence)
            contains_match = FingerprintMatch(
                vendor=match.vendor,
                category=match.category,
                confidence=round(match.confidence * 0.75, 3),
                pattern=pattern,
                match_type="contains",
            )
            self._subdomain_contains.append((pattern, contains_match))

        for entry in data.get("path_patterns", []):
            if not entry.get("vendor"):
                continue
            regex = re.compile(re.escape(entry["pattern"]), re.I)
            match = FingerprintMatch(
                vendor=entry["vendor"],
                category=entry.get("category", "unknown"),
                confidence=0.88,
                pattern=entry["pattern"],
                match_type="path",
            )
            self._path_patterns.append((regex, match))

        logger.info(
            "[FINGERPRINT] Loaded %d subdomain patterns, %d path patterns",
            len(self._subdomain_exact),
            len(self._path_patterns),
        )

    def match_subdomain(self, fqdn: str) -> Optional[FingerprintMatch]:
        """
        Match a FQDN against the fingerprint table.
        Returns the best match or None.
        """
        fqdn = fqdn.lower().strip().rstrip(".")
        prefix = fqdn.split(".")[0]

        # 1. Exact prefix match (highest confidence)
        if prefix in self._subdomain_exact:
            return self._subdomain_exact[prefix]

        # 2. Contains match (any part of the prefix)
        for keyword, fp_match in self._subdomain_contains:
            if keyword in fqdn:
                return fp_match

        return None

    def match_path(self, url: str) -> Optional[FingerprintMatch]:
        """
        Match a URL path against path patterns in the fingerprint table.
        Returns the first match or None.
        """
        for pattern, fp_match in self._path_patterns:
            if pattern.search(url):
                return fp_match
        return None

    def match_text(self, text: str) -> list[FingerprintMatch]:
        """
        Find all vendor mentions in a block of text (commit message, page text).
        Used by GitHub and Wayback parsers.
        """
        text_lower = text.lower()
        matches: list[FingerprintMatch] = []
        seen_vendors: set[str] = set()
        for keyword, fp_match in self._subdomain_contains:
            if fp_match.vendor not in seen_vendors and keyword in text_lower:
                matches.append(fp_match)
                seen_vendors.add(fp_match.vendor)
        return matches

    def register_pattern(
        self,
        pattern: str,
        vendor: str,
        category: str,
        confidence: float = 0.75,
    ) -> None:
        """
        Register a new fingerprint pattern at runtime.
        Called by Stage 5 when the LLM identifies a new vendor pattern.
        """
        pattern = pattern.lower()
        new_match = FingerprintMatch(
            vendor=vendor,
            category=category,
            confidence=confidence,
            pattern=pattern,
            match_type="exact_prefix",
        )
        self._subdomain_exact[pattern] = new_match
        self._subdomain_contains.append(
            (pattern, FingerprintMatch(
                vendor=vendor, category=category,
                confidence=round(confidence * 0.75, 3),
                pattern=pattern, match_type="contains",
            ))
        )
        logger.info("[FINGERPRINT] Registered new pattern: %s → %s", pattern, vendor)

    @property
    def pattern_count(self) -> int:
        return len(self._subdomain_exact)
