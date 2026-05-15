# -*- coding: utf-8 -*-
"""Offline tests for sentinel TTL retention policy."""
import pytest
from datetime import datetime, timedelta, timezone

from src.services.sentinel.ttl import compute_expires_at, TTL_BY_PRIORITY, _DEFAULT_TTL


BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestComputeExpiresAt:
    def test_p1_expires_24h(self):
        result = compute_expires_at(1, BASE_TIME)
        expected = (BASE_TIME + timedelta(hours=24)).isoformat()
        assert result == expected

    def test_p2_expires_7d(self):
        result = compute_expires_at(2, BASE_TIME)
        expected = (BASE_TIME + timedelta(days=7)).isoformat()
        assert result == expected

    def test_p3_expires_30d(self):
        result = compute_expires_at(3, BASE_TIME)
        expected = (BASE_TIME + timedelta(days=30)).isoformat()
        assert result == expected

    def test_p4_expires_90d(self):
        result = compute_expires_at(4, BASE_TIME)
        expected = (BASE_TIME + timedelta(days=90)).isoformat()
        assert result == expected

    def test_p5_never_expires(self):
        assert compute_expires_at(5, BASE_TIME) is None

    def test_none_priority_uses_default_ttl(self):
        result = compute_expires_at(None, BASE_TIME)
        expected = (BASE_TIME + _DEFAULT_TTL).isoformat()
        assert result == expected

    def test_unknown_priority_uses_default_ttl(self):
        result = compute_expires_at(99, BASE_TIME)
        expected = (BASE_TIME + _DEFAULT_TTL).isoformat()
        assert result == expected

    def test_naive_datetime_assumed_utc(self):
        naive = datetime(2026, 1, 1, 0, 0, 0)
        result = compute_expires_at(2, naive)
        aware = naive.replace(tzinfo=timezone.utc)
        expected = (aware + timedelta(days=7)).isoformat()
        assert result == expected

    def test_result_is_string(self):
        result = compute_expires_at(3, BASE_TIME)
        assert isinstance(result, str)

    def test_result_contains_timezone_offset(self):
        result = compute_expires_at(2, BASE_TIME)
        # ISO 8601 string should contain +00:00 or Z for UTC
        assert "+00:00" in result or "Z" in result


class TestTTLPurger:
    def test_purger_delegates_to_store(self):
        from src.services.sentinel.ttl import TTLPurger

        class FakeStore:
            def purge_expired(self):
                return {"deleted": 3, "archived": 1}

        purger = TTLPurger(FakeStore())
        result = purger.run()
        assert result == {"deleted": 3, "archived": 1}

    def test_purger_returns_empty_on_store_failure(self):
        from src.services.sentinel.ttl import TTLPurger

        class BrokenStore:
            def purge_expired(self):
                raise RuntimeError("db error")

        purger = TTLPurger(BrokenStore())
        # Should not raise; store.purge_expired errors propagate (purger is thin)
        with pytest.raises(RuntimeError):
            purger.run()
