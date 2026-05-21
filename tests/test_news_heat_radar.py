# -*- coding: utf-8 -*-
"""Regression tests for NewsHeatRadar search input handling."""

from types import SimpleNamespace

from src.search_service import SearchResponse, SearchResult
from src.services.news_heat_radar import NewsHeatRadar


def _response(query: str, results) -> SearchResponse:
    return SearchResponse(query=query, results=results, provider="Mock", success=True)


def _result(title: str, url: str) -> SearchResult:
    return SearchResult(
        title=title,
        snippet=f"{title} snippet",
        url=url,
        source="example.com",
        published_date="2026-05-21",
    )


class _FakeSearchService:
    is_available = True

    def __init__(self):
        self.queries = []

    def search(self, query: str, max_results: int = 5, days: int = 7):
        self.queries.append(query)
        return _response(
            query,
            [
                _result(f"{query} first", "https://example.com/shared"),
                _result(f"{query} second", f"https://example.com/{len(self.queries)}"),
            ],
        )


def test_build_heat_queries_respects_target_markets():
    assert NewsHeatRadar._build_heat_queries(["cn"])
    assert all("A股" in q for q in NewsHeatRadar._build_heat_queries(["cn"]))
    assert all("A股" not in q for q in NewsHeatRadar._build_heat_queries(["us"]))


def test_build_news_digest_uses_search_service_and_dedupes_urls():
    radar = NewsHeatRadar(config=SimpleNamespace())
    search = _FakeSearchService()

    digest = radar._build_news_digest(search, ["cn"])

    assert search.queries
    assert "A股" in search.queries[0]
    assert digest.count("https://example.com/shared") == 0
    assert digest.count(" first snippet") == 1
