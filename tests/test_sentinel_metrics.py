# -*- coding: utf-8 -*-
"""Offline unit tests for SentinelMetrics (Phase 5)."""
import time
import threading
import pytest

from src.services.sentinel.metrics import SentinelMetrics


class TestRecordSpiderFetch:
    def test_accumulates_fetched_count(self):
        m = SentinelMetrics()
        m.record_spider_fetch("spider_a", 10)
        m.record_spider_fetch("spider_a", 5)
        s = m.summary()
        assert s["spiders"]["spider_a"]["total_fetched"] == 15

    def test_counts_errors(self):
        m = SentinelMetrics()
        m.record_spider_fetch("spider_b", 0, error=True)
        m.record_spider_fetch("spider_b", 0, error=True)
        s = m.summary()
        assert s["spiders"]["spider_b"]["errors"] == 2

    def test_consecutive_empty_increments_and_resets(self):
        m = SentinelMetrics()
        m.record_spider_fetch("spider_c", 0)
        m.record_spider_fetch("spider_c", 0)
        assert m.summary()["spiders"]["spider_c"]["consecutive_empty"] == 2
        # Non-empty run resets counter
        m.record_spider_fetch("spider_c", 3)
        assert m.summary()["spiders"]["spider_c"]["consecutive_empty"] == 0

    def test_error_does_not_increment_empty(self):
        m = SentinelMetrics()
        m.record_spider_fetch("spider_d", 0, error=True)
        s = m.summary()
        assert s["spiders"]["spider_d"]["consecutive_empty"] == 0

    def test_success_rate_all_errors(self):
        m = SentinelMetrics()
        m.record_spider_fetch("s", 5, error=False)
        m.record_spider_fetch("s", 0, error=True)
        s = m.summary()
        # 1 error out of 2 total attempts → 0.5
        assert s["spiders"]["s"]["success_rate"] == 0.5

    def test_success_rate_no_data(self):
        m = SentinelMetrics()
        m.record_spider_fetch("s", 10)
        # 0 errors → 1.0
        assert m.summary()["spiders"]["s"]["success_rate"] == 1.0

    def test_multiple_spiders_tracked_independently(self):
        m = SentinelMetrics()
        m.record_spider_fetch("a", 10)
        m.record_spider_fetch("b", 20, error=True)
        s = m.summary()
        assert s["spiders"]["a"]["total_fetched"] == 10
        assert s["spiders"]["b"]["errors"] == 1


class TestRecordClassification:
    def test_accumulates_total_and_batches(self):
        m = SentinelMetrics()
        m.record_classification(10, 1.0)
        m.record_classification(5, 0.5)
        s = m.summary()["classification"]
        assert s["total_classified"] == 15
        assert s["batches"] == 2

    def test_avg_latency(self):
        m = SentinelMetrics()
        m.record_classification(1, 2.0)
        m.record_classification(1, 4.0)
        s = m.summary()["classification"]
        assert s["avg_latency_s"] == 3.0

    def test_max_latency(self):
        m = SentinelMetrics()
        m.record_classification(1, 1.5)
        m.record_classification(1, 3.7)
        m.record_classification(1, 2.0)
        s = m.summary()["classification"]
        assert s["max_latency_s"] == 3.7

    def test_zero_batches_avg_is_zero(self):
        m = SentinelMetrics()
        s = m.summary()["classification"]
        assert s["avg_latency_s"] == 0.0
        assert s["max_latency_s"] == 0.0


class TestRecordPurge:
    def test_accumulates_deleted_and_archived(self):
        m = SentinelMetrics()
        m.record_purge(deleted=5, archived=2)
        m.record_purge(deleted=3, archived=1)
        s = m.summary()["ttl_purge"]
        assert s["total_deleted"] == 8
        assert s["total_archived"] == 3


class TestRecordCycle:
    def test_counts_cycles_and_errors(self):
        m = SentinelMetrics()
        m.record_cycle(error=False)
        m.record_cycle(error=True)
        m.record_cycle(error=False)
        s = m.summary()
        assert s["cycles_total"] == 3
        assert s["cycles_errors"] == 1


class TestSummary:
    def test_uptime_increases(self):
        m = SentinelMetrics()
        s1 = m.summary()["uptime_seconds"]
        time.sleep(0.05)
        s2 = m.summary()["uptime_seconds"]
        assert s2 >= s1

    def test_db_size_returns_zero_for_missing_path(self):
        m = SentinelMetrics()
        s = m.summary(db_path="/nonexistent/path.db")
        assert s["db_size_bytes"] == 0

    def test_db_size_returns_zero_when_no_path(self):
        m = SentinelMetrics()
        assert m.summary()["db_size_bytes"] == 0

    def test_thread_safety(self):
        m = SentinelMetrics()
        errors: list = []

        def worker():
            try:
                for _ in range(50):
                    m.record_spider_fetch("s", 1)
                    m.record_classification(1, 0.1)
                    m.record_purge(1, 0)
                    m.record_cycle()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        s = m.summary()
        assert s["spiders"]["s"]["total_fetched"] == 250
        assert s["cycles_total"] == 250
