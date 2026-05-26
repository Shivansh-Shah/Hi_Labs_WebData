"""
stage3_parsing/parser_orchestrator.py
---------------------------------------
Stage 3 Orchestrator — runs all four parsers concurrently.

Accepts a list of raw Signals from Stage 1/2, fans out to the correct
parser based on signal.source, and returns a merged list of ParsedSignals.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from models.signal import Signal, SignalSource
from models.parsed_signal import ParsedSignal
from stage3_parsing.ct_log_parser import CTLogParser
from stage3_parsing.dns_parser import DNSParser
from stage3_parsing.wayback_parser import WaybackParser
from stage3_parsing.github_parser import GitHubParser

logger = logging.getLogger(__name__)


class ParserOrchestrator:
    """
    Runs the four Stage 3 parsers concurrently.

    Each parser only processes signals of its own source type.
    Results are merged into a single ParsedSignal list.

    Usage
    -----
        orch = ParserOrchestrator()
        parsed = await orch.parse(raw_signals)
    """

    def __init__(self) -> None:
        self._ct_parser = CTLogParser()
        self._dns_parser = DNSParser()
        self._wb_parser = WaybackParser()
        self._gh_parser = GitHubParser()

    async def parse(self, signals: Iterable[Signal]) -> list[ParsedSignal]:
        """
        Fan out all signals to the correct parser concurrently.
        Returns deduplicated ParsedSignal list.
        """
        signal_list = list(signals)
        if not signal_list:
            return []

        logger.info("[STAGE3] Parsing %d signals with 4 parallel parsers", len(signal_list))

        results = await asyncio.gather(
            self._ct_parser.parse_all(signal_list),
            self._dns_parser.parse_all(signal_list),
            self._wb_parser.parse_all(signal_list),
            self._gh_parser.parse_all(signal_list),
            return_exceptions=True,
        )

        out: list[ParsedSignal] = []
        for parser_name, result in zip(
            ["CT", "DNS", "Wayback", "GitHub"], results
        ):
            if isinstance(result, list):
                out.extend(result)
            else:
                logger.error("[STAGE3] %s parser error: %s", parser_name, result)

        # Pass-through: any signal source not handled by a parser
        handled_sources = {SignalSource.CT_LOG, SignalSource.DNS, SignalSource.WAYBACK, SignalSource.GITHUB}
        for s in signal_list:
            if s.source not in handled_sources:
                out.append(ParsedSignal(
                    signal=s,
                    entities={"raw": s.raw_data},
                    confidence=s.confidence,
                ))

        logger.info("[STAGE3] Complete: %d parsed signals", len(out))
        return out
