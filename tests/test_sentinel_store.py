# -*- coding: utf-8 -*-
"""Tests for NewsStore (SQLite + FTS5) — offline, no network."""
import os
import tempfile
from datetime import datetime, timezone

import pytest

from src.services.sentinel.dedup import url_hash
from src.services.sentinel.models import RawArticle
from src.services.sentinel.store import NewsStore


def _make_article(url: str, title: str = "Test", spider: str = "test_spider") -> RawArticle:
    return RawArticle(
        url=url,
        title=title,
        content="content body",
        source_name="TestSource",
        source_url="https://test.com",
        spider_name=spider,
        published_at=datetime.now(timezone.utc),
        language="zh",
    )


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "sentinel_test.db")
    return NewsStore(db_path)


class TestNewsStoreInit:
    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        assert not os.path.exists(db_path)
        NewsStore(db_path)
        assert os.path.exists(db_path)

    def test_count_empty_on_init(self, store):
        assert store.count() == 0


class TestNewsStoreUpsert:
    def test_insert_new_returns_true(self, store):
        article = _make_article("https://example.com/news/1")
        assert store.upsert(article) is True

    def test_duplicate_url_returns_false(self, store):
        article = _make_article("https://example.com/news/2")
        assert store.upsert(article) is True
        assert store.upsert(article) is False   # second insert is ignored

    def test_count_increases_after_insert(self, store):
        store.upsert(_make_article("https://example.com/1"))
        store.upsert(_make_article("https://example.com/2"))
        assert store.count() == 2

    def test_different_urls_both_inserted(self, store):
        store.upsert(_make_article("https://a.com/1", "Article A"))
        store.upsert(_make_article("https://b.com/2", "Article B"))
        assert store.count() == 2

    def test_upsert_without_published_at(self, store):
        article = _make_article("https://example.com/no-date")
        article.published_at = None
        assert store.upsert(article) is True


class TestNewsStoreExists:
    def test_exists_after_insert(self, store):
        from src.services.sentinel.dedup import url_hash
        article = _make_article("https://example.com/exist")
        store.upsert(article)
        assert store.exists_by_url_hash(url_hash("https://example.com/exist"))

    def test_not_exists_before_insert(self, store):
        from src.services.sentinel.dedup import url_hash
        assert not store.exists_by_url_hash(url_hash("https://never-inserted.com"))


class TestNewsStoreCountBySpider:
    def test_counts_per_spider(self, store):
        store.upsert(_make_article("https://a.com/1", spider="spider_a"))
        store.upsert(_make_article("https://a.com/2", spider="spider_a"))
        store.upsert(_make_article("https://b.com/1", spider="spider_b"))
        counts = store.count_by_spider()
        assert counts["spider_a"] == 2
        assert counts["spider_b"] == 1


class TestNewsStoreGetRecent:
    def test_returns_inserted_items(self, store):
        store.upsert(_make_article("https://example.com/recent"))
        rows = store.get_recent(hours=1)
        assert len(rows) >= 1


class TestNewsStoreGetNewsForStock:
    def test_returns_classified_news_by_affected_stock(self, store):
        article = _make_article("https://example.com/mu", title="Micron earnings lift chip outlook")
        store.upsert(article)
        store.update_classification(
            url_hash(article.url),
            {
                "priority": 4,
                "sentiment": "positive",
                "affected_stocks": '["MU"]',
                "llm_reasoning": "Micron demand improves",
            },
        )

        rows = store.get_news_for_stock("MU", limit=5)

        assert len(rows) == 1
        assert rows[0].sentiment == "positive"

    def test_returns_classified_news_by_watched_name_fts(self, store):
        store.upsert_watched_stocks([{"code": "NVDA", "name": "NVIDIA"}])
        article = _make_article("https://example.com/nvda", title="NVIDIA raises AI chip guidance")
        store.upsert(article)
        store.update_classification(
            url_hash(article.url),
            {
                "priority": 3,
                "sentiment": "positive",
                "affected_stocks": "[]",
                "llm_reasoning": "Strong data center demand",
            },
        )

        rows = store.get_news_for_stock("NVDA", limit=5)

        assert [row.title for row in rows] == ["NVIDIA raises AI chip guidance"]

    def test_returns_per_stock_target_news_without_llm_affected_stock(self, store):
        article = _make_article("https://example.com/targeted", title="Analyst raises price target")
        article.target_code = "MU"
        article.target_name = "Micron Technology"
        store.upsert(article)
        store.update_classification(
            url_hash(article.url),
            {
                "priority": 3,
                "sentiment": "positive",
                "affected_stocks": "[]",
                "llm_reasoning": "Targeted watcher fetched this stock news",
            },
        )

        rows = store.get_news_for_stock("MU", limit=5)

        assert len(rows) == 1
        assert rows[0].target_code == "MU"

    def test_short_ticker_does_not_match_inside_words(self, store):
        article = _make_article(
            "https://example.com/systemic",
            title="Nvidia posts record profit amid AI chip boom",
        )
        article.target_code = "NVDA"
        article.content = "This is a systemic semiconductor market update."
        store.upsert(article)
        store.update_classification(
            url_hash(article.url),
            {
                "priority": 5,
                "sentiment": "positive",
                "affected_stocks": '["NVDA"]',
                "llm_reasoning": "Major AI chip demand update",
            },
        )

        assert store.get_news_for_stock("MU", limit=5) == []


class TestNewsStoreLogRun:
    def test_log_spider_run_does_not_raise(self, store):
        store.log_spider_run(
            spider_name="test",
            started_at="2026-05-14T10:00:00Z",
            finished_at="2026-05-14T10:00:05Z",
            items_fetched=10,
            items_new=8,
            items_deduped=2,
            status="ok",
        )


class TestNewsStoreNearDuplicate:
    def test_near_duplicate_detection(self, store):
        article = _make_article("https://example.com/dup1", title="PBoC cuts rates by 25bp monetary easing policy")
        store.upsert(article)
        # Retrieve the stored (signed) simhash and check it matches itself
        row = store._connect().execute(
            "SELECT simhash FROM news_items WHERE url='https://example.com/dup1'"
        ).fetchone()
        assert row is not None
        assert store.near_duplicate_exists(row[0], threshold=3)
