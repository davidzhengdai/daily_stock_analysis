# -*- coding: utf-8 -*-
"""Regression tests for Sentinel news API fallbacks."""

from unittest.mock import MagicMock, patch

from api.v1.endpoints import sentinel


def _news_row(priority=None, title="Unclassified market news"):
    return {
        "id": 1,
        "title": title,
        "content": "body",
        "source_name": "TestSource",
        "url": "https://example.com/news",
        "priority": priority,
        "sentiment": None,
        "category": None,
        "market_scope": None,
        "affected_sectors": "[]",
        "affected_stocks": "[]",
        "impact_horizon": None,
        "llm_reasoning": None,
        "is_actionable": 0,
        "published_at": None,
        "fetched_at": "2026-05-26T10:00:00",
    }


def test_sentinel_news_falls_back_to_recent_items_when_http_classified_list_empty():
    store = MagicMock()
    store.get_recent.return_value = [_news_row()]

    with patch("api.v1.endpoints.sentinel._http_json", return_value={"items": []}), \
         patch("api.v1.endpoints.sentinel._get_store_and_config", return_value=(None, store)):
        rows = sentinel.get_news(hours=48, priority_min=3, limit=50)

    assert len(rows) == 1
    assert rows[0].title == "Unclassified market news"
    store.get_recent.assert_called_once_with(hours=48, priority_min=3, limit=50)


def test_sentinel_news_falls_back_to_recent_items_when_local_classified_list_empty():
    store = MagicMock()
    store.get_recent_classified.return_value = []
    store.get_recent.return_value = [_news_row()]

    with patch("api.v1.endpoints.sentinel._http_json", return_value=None), \
         patch("api.v1.endpoints.sentinel._get_store_and_config", return_value=(None, store)):
        rows = sentinel.get_news(hours=48, priority_min=3, limit=50)

    assert len(rows) == 1
    store.get_recent_classified.assert_called_once_with(hours=48, priority_min=3, limit=50)
    store.get_recent.assert_called_once_with(hours=48, priority_min=3, limit=50)
