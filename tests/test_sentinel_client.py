# -*- coding: utf-8 -*-
"""Offline tests for SentinelCacheClient (Phase 4)."""
import json
import pytest
from unittest.mock import MagicMock, patch

from src.services.sentinel.client import SentinelCacheClient
from src.services.sentinel.config import SentinelConfig


def _make_config(enabled=True, db_path="data/sentinel.db"):
    cfg = SentinelConfig()
    cfg.enabled = enabled
    cfg.db_path = db_path
    return cfg


def _make_row(**kwargs):
    defaults = {
        "id": 1,
        "url_hash": "abc123",
        "url": "https://example.com/article",
        "title": "Test Article",
        "content": "Content here",
        "source_name": "新华社",
        "priority": 4,
        "sentiment": "positive",
        "category": "breaking",
        "market_scope": "CN",
        "affected_sectors": '["银行"]',
        "affected_stocks": '["600519"]',
        "impact_horizon": "short_term",
        "llm_reasoning": "Market impact",
        "is_actionable": 1,
        "published_at": "2026-01-01T00:00:00+00:00",
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    row = MagicMock()
    row.__getitem__ = lambda self, k: defaults[k]
    row.keys = lambda: defaults.keys()
    # Make dict(row) work
    row.__iter__ = lambda self: iter(defaults)
    # Support dict() conversion
    row._data = defaults
    return row


def _make_dict_row(**kwargs):
    """A row that properly supports dict() conversion."""
    defaults = {
        "id": 1, "url_hash": "abc123", "url": "https://example.com/a",
        "title": "Title", "content": "Content",
        "source_name": "Source", "priority": 4, "sentiment": "positive",
        "category": "finance", "market_scope": "CN",
        "affected_sectors": '["银行"]', "affected_stocks": '["600519"]',
        "impact_horizon": "short_term", "llm_reasoning": "ok",
        "is_actionable": 1, "published_at": None,
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults  # Return plain dict for simplicity


class TestIsAvailable:
    def test_returns_false_when_disabled(self):
        cfg = _make_config(enabled=False)
        store = MagicMock()
        client = SentinelCacheClient(config=cfg, store=store)
        assert client.is_available() is False
        store.count.assert_not_called()

    def test_returns_false_when_too_few_items(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.count.return_value = 5
        client = SentinelCacheClient(config=cfg, store=store)
        assert client.is_available(min_items=10) is False

    def test_returns_true_when_enough_items(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.count.return_value = 50
        client = SentinelCacheClient(config=cfg, store=store)
        assert client.is_available(min_items=10) is True

    def test_returns_false_on_store_exception(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.count.side_effect = RuntimeError("db error")
        client = SentinelCacheClient(config=cfg, store=store)
        assert client.is_available() is False


class TestSearchForStock:
    def test_returns_search_response(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()

        # Build a proper sqlite3.Row-like object using a real dict
        class FakeRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
            def keys(self):
                return self._d.keys()

        row = FakeRow({
            "id": 1, "url_hash": "abc", "url": "https://ex.com",
            "title": "Rate cut", "content": "Details...",
            "source_name": "新华社", "priority": 4,
            "sentiment": "positive", "category": "macro",
            "market_scope": "CN", "affected_sectors": "[]",
            "affected_stocks": '["600519"]', "impact_horizon": "short_term",
            "llm_reasoning": "ok", "is_actionable": 1,
            "published_at": None, "fetched_at": "2026-01-01T00:00:00+00:00",
        })
        store.search_fts.return_value = [row]

        client = SentinelCacheClient(config=cfg, store=store)
        response = client.search_for_stock("600519", "贵州茅台")

        assert response.provider == "sentinel_cache"
        assert response.success is True
        assert len(response.results) == 1
        assert response.results[0].title == "Rate cut"

    def test_falls_back_when_fts_empty(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.search_fts.return_value = []

        class FakeRow:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]

        candidate = FakeRow({
            "url": "https://ex.com", "title": "News",
            "content": "Content", "source_name": "Source",
            "affected_stocks": '["600519"]',
            "published_at": None, "fetched_at": "2026-01-01T00:00:00+00:00",
        })
        store.get_recent.return_value = [candidate]

        client = SentinelCacheClient(config=cfg, store=store)
        response = client.search_for_stock("600519", "茅台")

        assert len(response.results) == 1

    def test_returns_empty_response_on_error(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.search_fts.side_effect = RuntimeError("db error")

        client = SentinelCacheClient(config=cfg, store=store)
        response = client.search_for_stock("600519", "茅台")

        assert response.success is False
        assert response.results == []
        assert response.provider == "sentinel_cache"


class TestGetRecentNews:
    def test_returns_list_of_dicts(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()

        class FakeRow(dict):
            pass

        row = FakeRow({
            "id": 1, "url_hash": "abc", "url": "https://ex.com",
            "title": "News", "content": "Content",
            "source_name": "XH", "priority": 4, "sentiment": "positive",
            "category": "breaking", "market_scope": "CN",
            "affected_sectors": '["银行","地产"]', "affected_stocks": '["600519"]',
            "impact_horizon": "short_term", "llm_reasoning": "ok",
            "is_actionable": 1, "published_at": None,
            "fetched_at": "2026-01-01T00:00:00+00:00",
        })
        store.get_recent_classified.return_value = [row]

        client = SentinelCacheClient(config=cfg, store=store)
        news = client.get_recent_news(hours=24, priority_min=3, limit=50)

        assert len(news) == 1
        assert news[0]["title"] == "News"
        # JSON fields should be parsed
        assert news[0]["affected_sectors"] == ["银行", "地产"]
        assert news[0]["affected_stocks"] == ["600519"]
        assert news[0]["is_actionable"] is True

    def test_returns_empty_list_on_error(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.get_recent_classified.side_effect = RuntimeError("db error")

        client = SentinelCacheClient(config=cfg, store=store)
        result = client.get_recent_news()
        assert result == []


class TestGetLatestAnalysis:
    def test_returns_none_when_no_analyses(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.get_latest_cycle_analysis.return_value = None

        client = SentinelCacheClient(config=cfg, store=store)
        assert client.get_latest_analysis() is None

    def test_parses_json_fields(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()

        analysis_row = {
            "id": 1, "cycle_at": "2026-01-01T00:00:00+00:00",
            "news_count": 10,
            "themes": '[{"theme":"降息","confidence":0.8,"sectors":["银行"]}]',
            "sector_opps": "[]",
            "stock_leads": '[{"code":"600519","name":"茅台","reason":"ok","confidence":0.85}]',
            "risk_alerts": '["汇率风险"]',
            "triggered_stocks": '["600519"]',
            "market_mood": "乐观",
            "model_used": "",
        }
        store.get_latest_cycle_analysis.return_value = analysis_row

        client = SentinelCacheClient(config=cfg, store=store)
        result = client.get_latest_analysis()

        assert result is not None
        assert result["market_mood"] == "乐观"
        assert isinstance(result["themes"], list)
        assert result["themes"][0]["theme"] == "降息"
        assert result["triggered_stocks"] == ["600519"]

    def test_returns_none_on_error(self):
        cfg = _make_config(enabled=True)
        store = MagicMock()
        store.get_latest_cycle_analysis.side_effect = RuntimeError("db error")

        client = SentinelCacheClient(config=cfg, store=store)
        assert client.get_latest_analysis() is None
