# -*- coding: utf-8 -*-
"""SentinelMetrics — lightweight in-process metrics accumulator.

Tracks spider health, LLM classification latency, and DB size across
cycles. All state is in-memory and resets on process restart.
Provides a summary() dict consumed by the /status endpoint and logs.
"""
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SentinelMetrics:
    """Thread-safe in-memory metrics store for the sentinel service."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Per-spider counters
        self._spider_fetched: Dict[str, int] = defaultdict(int)
        self._spider_errors: Dict[str, int] = defaultdict(int)
        self._spider_empty_runs: Dict[str, int] = defaultdict(int)
        # LLM classification
        self._classify_total: int = 0
        self._classify_batches: int = 0
        self._classify_latency_sum: float = 0.0   # seconds
        self._classify_latency_max: float = 0.0
        # TTL purge
        self._purge_deleted: int = 0
        self._purge_archived: int = 0
        # Cycle counters
        self._cycle_count: int = 0
        self._cycle_errors: int = 0
        # Start time
        self._started_at: float = time.time()

    # ── record methods ────────────────────────────────────────────────────────

    def record_spider_fetch(self, spider_name: str, fetched: int, error: bool = False) -> None:
        with self._lock:
            self._spider_fetched[spider_name] += fetched
            if error:
                self._spider_errors[spider_name] += 1
            if fetched == 0 and not error:
                self._spider_empty_runs[spider_name] += 1
            else:
                self._spider_empty_runs[spider_name] = 0  # reset on non-empty run

    def record_classification(self, count: int, elapsed_seconds: float) -> None:
        with self._lock:
            self._classify_total += count
            self._classify_batches += 1
            self._classify_latency_sum += elapsed_seconds
            if elapsed_seconds > self._classify_latency_max:
                self._classify_latency_max = elapsed_seconds

    def record_purge(self, deleted: int, archived: int) -> None:
        with self._lock:
            self._purge_deleted += deleted
            self._purge_archived += archived

    def record_cycle(self, error: bool = False) -> None:
        with self._lock:
            self._cycle_count += 1
            if error:
                self._cycle_errors += 1

    # ── read methods ──────────────────────────────────────────────────────────

    def summary(self, db_path: Optional[str] = None) -> dict:
        with self._lock:
            uptime_s = int(time.time() - self._started_at)
            avg_latency = (
                self._classify_latency_sum / self._classify_batches
                if self._classify_batches > 0 else 0.0
            )
            spider_stats = {
                name: {
                    "total_fetched": self._spider_fetched[name],
                    "errors": self._spider_errors[name],
                    "consecutive_empty": self._spider_empty_runs[name],
                    "success_rate": self._spider_success_rate(name),
                }
                for name in set(list(self._spider_fetched) + list(self._spider_errors))
            }
            result = {
                "uptime_seconds": uptime_s,
                "cycles_total": self._cycle_count,
                "cycles_errors": self._cycle_errors,
                "spiders": spider_stats,
                "classification": {
                    "total_classified": self._classify_total,
                    "batches": self._classify_batches,
                    "avg_latency_s": round(avg_latency, 2),
                    "max_latency_s": round(self._classify_latency_max, 2),
                },
                "ttl_purge": {
                    "total_deleted": self._purge_deleted,
                    "total_archived": self._purge_archived,
                },
                "db_size_bytes": self._get_db_size(db_path),
            }
        return result

    def _spider_success_rate(self, name: str) -> float:
        """Success rate = fetches with items / total fetch attempts."""
        total = self._spider_fetched.get(name, 0) + self._spider_errors.get(name, 0)
        if total == 0:
            return 1.0
        errors = self._spider_errors.get(name, 0)
        return round(1.0 - errors / total, 3)

    @staticmethod
    def _get_db_size(db_path: Optional[str]) -> int:
        if not db_path:
            return 0
        try:
            return os.path.getsize(db_path)
        except OSError:
            return 0
