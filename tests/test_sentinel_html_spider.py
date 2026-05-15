# -*- coding: utf-8 -*-
"""Offline unit tests for HTMLListSpider utilities (Phase 5)."""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.services.sentinel.spiders.html_list import (
    HTMLListSpider,
    _extract_text,
    _parse_cn_date,
)
from src.services.sentinel.models import RawArticle


# ── _extract_text ─────────────────────────────────────────────────────────────

class TestExtractText:
    def test_strips_tags(self):
        assert _extract_text("<b>Hello</b>") == "Hello"

    def test_normalises_whitespace(self):
        assert _extract_text("  foo   <br/>  bar  ") == "foo bar"

    def test_empty_string(self):
        assert _extract_text("") == ""

    def test_no_tags(self):
        assert _extract_text("plain text") == "plain text"

    def test_nested_tags(self):
        result = _extract_text("<div><span>A</span> <em>B</em></div>")
        assert result == "A B"


# ── _parse_cn_date ────────────────────────────────────────────────────────────

class TestParseCnDate:
    def test_iso_format(self):
        dt = _parse_cn_date("2026-01-15")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 1 and dt.day == 15
        assert dt.tzinfo == timezone.utc

    def test_slash_format(self):
        dt = _parse_cn_date("2026/03/20")
        assert dt is not None
        assert dt.month == 3

    def test_chinese_format(self):
        dt = _parse_cn_date("2026年04月01日")
        assert dt is not None
        assert dt.day == 1

    def test_dot_format(self):
        dt = _parse_cn_date("2026.06.30")
        assert dt is not None
        assert dt.month == 6

    def test_embedded_date(self):
        dt = _parse_cn_date("发布时间：2026-02-10 某机构")
        assert dt is not None
        assert dt.month == 2

    def test_invalid_returns_none(self):
        assert _parse_cn_date("not a date") is None

    def test_empty_string_returns_none(self):
        assert _parse_cn_date("") is None

    def test_whitespace_stripped(self):
        dt = _parse_cn_date("  2026-07-04  ")
        assert dt is not None
        assert dt.month == 7


# ── HTMLListSpider concrete subclass ─────────────────────────────────────────

class _ConcreteSpider(HTMLListSpider):
    """Minimal concrete implementation for testing."""
    name = "test_spider"
    source_name = "TestSource"
    source_url = "https://example.com"
    list_url = "https://example.com/news"
    language = "zh"

    def _parse_items(self, html: str):
        return [
            {"url": "https://example.com/a1", "title": "Article One", "published_at": "2026-01-01"},
            {"url": "/relative/path", "title": "Article Two", "published_at": None},
            {"url": "", "title": "Missing URL"},  # should be skipped
            {"url": "https://example.com/a3", "title": ""},  # should be skipped
        ]

    def is_enabled(self, config=None) -> bool:
        return True


class TestHTMLListSpiderFetch:
    def test_fetch_returns_articles(self):
        spider = _ConcreteSpider()
        with patch.object(spider, "_get_html", return_value="<html>dummy</html>"):
            articles = spider.fetch()
        # 2 valid articles (missing url and missing title are skipped)
        assert len(articles) == 2
        assert articles[0].title == "Article One"

    def test_relative_url_resolved(self):
        spider = _ConcreteSpider()
        with patch.object(spider, "_get_html", return_value="<html>dummy</html>"):
            articles = spider.fetch()
        # Second article has relative URL
        assert articles[1].url.startswith("https://example.com")

    def test_empty_html_returns_empty(self):
        spider = _ConcreteSpider()
        with patch.object(spider, "_get_html", return_value=""):
            articles = spider.fetch()
        assert articles == []

    def test_empty_items_list_returns_empty(self):
        class _EmptySpider(_ConcreteSpider):
            def _parse_items(self, html):
                return []

        spider = _EmptySpider()
        with patch.object(spider, "_get_html", return_value="<html>dummy</html>"):
            articles = spider.fetch()
        assert articles == []

    def test_max_items_limit(self):
        class _ManyItems(_ConcreteSpider):
            max_items_per_run = 2
            def _parse_items(self, html):
                return [{"url": f"https://ex.com/{i}", "title": f"Title {i}"} for i in range(10)]

        spider = _ManyItems()
        with patch.object(spider, "_get_html", return_value="<html>dummy</html>"):
            articles = spider.fetch()
        assert len(articles) <= 2

    def test_http_error_returns_empty(self):
        spider = _ConcreteSpider()
        with patch.object(spider, "_get_html", return_value=""):
            articles = spider.fetch()
        assert articles == []

    def test_published_at_parsed(self):
        spider = _ConcreteSpider()
        with patch.object(spider, "_get_html", return_value="<html>dummy</html>"):
            articles = spider.fetch()
        assert articles[0].published_at is not None
        assert isinstance(articles[0].published_at, datetime)

    def test_published_at_none_when_missing(self):
        spider = _ConcreteSpider()
        with patch.object(spider, "_get_html", return_value="<html>dummy</html>"):
            articles = spider.fetch()
        assert articles[1].published_at is None


# ── _grep_list_items ─────────────────────────────────────────────────────────

class TestGrepListItems:
    def test_extracts_links_with_titles(self):
        html = """
        <ul>
          <li><a href="/news/1">央行降息公告</a> 2026-01-01</li>
          <li><a href="/news/2">证监会新规出台</a> 2026-01-02</li>
        </ul>
        """
        items = HTMLListSpider._grep_list_items(html, base_url="https://gov.cn")
        assert len(items) == 2
        assert items[0]["title"] == "央行降息公告"
        assert items[0]["url"] == "https://gov.cn/news/1"

    def test_skips_short_titles(self):
        html = '<a href="/x">AB</a>'  # title length < 5
        items = HTMLListSpider._grep_list_items(html)
        assert items == []

    def test_skips_anchors(self):
        html = '<a href="#top">返回顶部导航菜单</a>'
        items = HTMLListSpider._grep_list_items(html)
        assert items == []

    def test_skips_javascript(self):
        html = '<a href="javascript:void(0)">点击这里查看更多内容</a>'
        items = HTMLListSpider._grep_list_items(html)
        assert items == []

    def test_deduplicates_urls(self):
        html = """
        <a href="/same">同一篇文章重复出现的标题</a>
        <a href="/same">同一篇文章重复出现的标题</a>
        """
        items = HTMLListSpider._grep_list_items(html, base_url="https://ex.com")
        urls = [i["url"] for i in items]
        assert len(urls) == len(set(urls))

    def test_extracts_nearby_date(self):
        html = """
        <li>2026-03-15 <a href="/article/123">财政部出台房地产新政策</a></li>
        """
        items = HTMLListSpider._grep_list_items(html)
        assert len(items) == 1
        assert items[0]["published_at"] is not None
        assert "2026" in items[0]["published_at"]

    def test_no_date_when_absent(self):
        html = '<a href="/a/b/c/d">没有日期的新闻标题文章</a>'
        items = HTMLListSpider._grep_list_items(html)
        if items:
            assert items[0]["published_at"] is None
