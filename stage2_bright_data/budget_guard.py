"""
stage2_bright_data/budget_guard.py
------------------------------------
Bright Data budget tracker and gate.

Every request routed through Bright Data MUST call BudgetGuard first.
If the estimated cumulative spend exceeds BRIGHT_DATA_BUDGET_USD,
the request is denied and the caller falls back to a free alternative.

This is a singleton (one shared state per process). In a distributed
Celery setup, back this with Redis INCRBYFLOAT for true cross-worker
budget enforcement.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class BDRequestType(str, Enum):
    WEB_UNLOCKER = "web_unlocker"
    SERP = "serp"
    SCRAPING_BROWSER = "scraping_browser"


# Cost per request type (USD)
_COSTS: dict[BDRequestType, float] = {
    BDRequestType.WEB_UNLOCKER:     settings.BRIGHT_DATA_COST_WEB_UNLOCKER,
    BDRequestType.SERP:             settings.BRIGHT_DATA_COST_SERP,
    BDRequestType.SCRAPING_BROWSER: settings.BRIGHT_DATA_COST_BROWSER_SESSION,
}


@dataclass
class BudgetSnapshot:
    total_spent: float
    total_requests: int
    budget_remaining: float
    breakdown: dict[str, dict]  # {request_type: {count, spent}}


class BudgetGuard:
    """
    Thread-safe singleton budget enforcer for Bright Data API spend.

    Usage
    -----
        guard = BudgetGuard.get_instance()

        if guard.can_spend(BDRequestType.WEB_UNLOCKER):
            guard.record_spend(BDRequestType.WEB_UNLOCKER)
            # ... make Bright Data request ...
        else:
            # ... fall back to free alternative ...
    """

    _instance: "BudgetGuard | None" = None
    _lock: Lock = Lock()

    def __init__(self, budget_usd: float) -> None:
        self._budget = budget_usd
        self._spent: float = 0.0
        self._request_counts: dict[BDRequestType, int] = {t: 0 for t in BDRequestType}
        self._spent_by_type: dict[BDRequestType, float] = {t: 0.0 for t in BDRequestType}
        self._guard_lock = Lock()
        self._start_time = time.time()

    @classmethod
    def get_instance(cls) -> "BudgetGuard":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(budget_usd=settings.BRIGHT_DATA_BUDGET_USD)
                logger.info(
                    "[BUDGET] Bright Data guard initialised — $%.2f budget",
                    settings.BRIGHT_DATA_BUDGET_USD,
                )
            return cls._instance

    def can_spend(
        self,
        request_type: BDRequestType,
        safety_margin: float = 5.0,
    ) -> bool:
        """
        Returns True if we have budget remaining for this request type.
        `safety_margin` is a USD buffer kept in reserve.
        """
        if not settings.has_bright_data:
            return False

        cost = _COSTS[request_type]
        with self._guard_lock:
            remaining = self._budget - self._spent - safety_margin
            can = remaining >= cost

        if not can:
            logger.warning(
                "[BUDGET] ⛔ Blocked %s request — spent $%.4f / $%.2f (remaining: $%.4f)",
                request_type.value,
                self._spent,
                self._budget,
                self._budget - self._spent,
            )
        return can

    def record_spend(self, request_type: BDRequestType) -> float:
        """Record a Bright Data request spend. Returns cost recorded."""
        cost = _COSTS[request_type]
        with self._guard_lock:
            self._spent += cost
            self._request_counts[request_type] += 1
            self._spent_by_type[request_type] += cost

        logger.debug(
            "[BUDGET] %s request: $%.4f (total: $%.4f / $%.2f)",
            request_type.value,
            cost,
            self._spent,
            self._budget,
        )
        return cost

    def snapshot(self) -> BudgetSnapshot:
        with self._guard_lock:
            breakdown = {
                t.value: {
                    "count": self._request_counts[t],
                    "spent_usd": round(self._spent_by_type[t], 4),
                    "cost_per_req": _COSTS[t],
                }
                for t in BDRequestType
            }
            return BudgetSnapshot(
                total_spent=round(self._spent, 4),
                total_requests=sum(self._request_counts.values()),
                budget_remaining=round(self._budget - self._spent, 4),
                breakdown=breakdown,
            )

    def log_summary(self) -> None:
        snap = self.snapshot()
        elapsed = (time.time() - self._start_time) / 3600
        logger.info(
            "[BUDGET] Summary — Spent: $%.4f | Remaining: $%.4f | "
            "Requests: %d | Elapsed: %.1fh",
            snap.total_spent,
            snap.budget_remaining,
            snap.total_requests,
            elapsed,
        )
        for rtype, stats in snap.breakdown.items():
            if stats["count"] > 0:
                logger.info(
                    "[BUDGET]   %-20s: %d req × $%.4f = $%.4f",
                    rtype,
                    stats["count"],
                    stats["cost_per_req"],
                    stats["spent_usd"],
                )
