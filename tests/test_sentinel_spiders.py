# -*- coding: utf-8 -*-
"""Tests for sentinel spider parsing — offline, uses mock RSS XML (no network)."""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.services.sentinel.spiders.native_rss import (
    NativeRSSSpider,
    _parse_rss_datetime,
    _extract_items,
)
from src.services.sentinel.spiders.eastmoney import EastMoneySpider
from src.services.sentinel.spiders.google_news import GoogleNewsENSpider, GoogleNewsCNSpider
from src.services.sentinel.spiders.cls import CLSRSSHubSpider
from src.services.sentinel.spiders.yahoo_finance import YahooFinanceRSSSpider
from src.services.sentinel.config import SentinelConfig


# ── RSS XML fixtures ─────────────────────────────────────────────────────────

_RSS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<rss version=\"2.0\"><channel><title>Test Finance Feed</title>"
    "<item>"
    "<title>PBoC cuts rates by 25bp</title>"
    "<link>https://example.com/news/1</link>"
    "<pubDate>Wed, 14 May 2026 08:00:00 +0000</pubDate>"
    "<description>Central bank cuts benchmark rate by 25bp, market sentiment improves.</description>"
    "<source>Xinhua Finance</source>"
    "</item>"
    "<item>"
    "<title>A-share indices rally across the board</title>"
    "<link>https://example.com/news/2</link>"
    "<pubDate>Wed, 14 May 2026 09:00:00 +0000</pubDate>"
    "<description>CSI 300 up over 1%, STAR Market leads gains.</description>"
    "</item>"
    "</channel></rss>"
).encode("utf-8")

_ATOM_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<title>Atom Finance Feed</title>"
    "<entry>"
    "<title>Fed Holds Rates Steady</title>"
    '<link href="https://example.com/atom/1"/>'
    "<published>2026-05-14T10:00:00Z</published>"
    "<summary>The Federal Reserve held rates steady at its May meeting.</summary>"
    "</entry>"
    "</feed>"
).encode("utf-8")

_EMPTY_RSS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<rss version=\"2.0\"><channel><title>Empty</title></channel></rss>"
).encode("utf-8")


# ── NativeRSSSpider parsing ──────────────────────────────────────────────────

class TestNativeRSSSpiderParsing:
    def _mock_spider(self, xml_bytes: bytes) -> NativeRSSSpider:
        spider = GoogleNewsENSpider()
        mock_resp = MagicMock()
        mock_resp.content = xml_bytes
        mock_resp.raise_for_status = MagicMock()
        with patch("src.services.sentinel.spiders.native_rss.requests.get", return_value=mock_resp):
            articles = spider.fetch()
        return articles

    def test_parses_rss_items(self):
        articles = self._mock_spider(_RSS_XML)
        assert len(articles) == 2

    def test_extracts_title(self):
        articles = self._mock_spider(_RSS_XML)
        assert articles[0].title == "PBoC cuts rates by 25bp"

    def test_extracts_url(self):
        articles = self._mock_spider(_RSS_XML)
        assert articles[0].url == "https://example.com/news/1"

    def test_extracts_content(self):
        articles = self._mock_spider(_RSS_XML)
        assert "25bp" in articles[0].content

    def test_extracts_published_at(self):
        articles = self._mock_spider(_RSS_XML)
        assert articles[0].published_at is not None
        assert isinstance(articles[0].published_at, datetime)

    def test_spider_name_set(self):
        articles = self._mock_spider(_RSS_XML)
        assert articles[0].spider_name == "google_news_en"

    def test_language_set(self):
        articles = self._mock_spider(_RSS_XML)
        assert articles[0].language == "en"

    def test_parses_atom_feed(self):
        articles = self._mock_spider(_ATOM_XML)
        assert len(articles) == 1
        assert articles[0].title == "Fed Holds Rates Steady"

    def test_atom_link_href(self):
        articles = self._mock_spider(_ATOM_XML)
        assert articles[0].url == "https://example.com/atom/1"

    def test_empty_feed_returns_empty_list(self):
        articles = self._mock_spider(_EMPTY_RSS_XML)
        assert articles == []

    def test_network_error_returns_empty(self):
        spider = GoogleNewsENSpider()
        with patch(
            "src.services.sentinel.spiders.native_rss.requests.get",
            side_effect=Exception("connection refused"),
        ):
            articles = spider.fetch()
        assert articles == []

    def test_degraded_after_consecutive_empty(self):
        spider = GoogleNewsENSpider()
        mock_resp = MagicMock()
        mock_resp.content = _EMPTY_RSS_XML
        mock_resp.raise_for_status = MagicMock()
        with patch("src.services.sentinel.spiders.native_rss.requests.get", return_value=mock_resp):
            for _ in range(3):
                spider.fetch()
        assert not spider.is_healthy()


# ── _parse_rss_datetime ───────────────────────────────────────────────────────

class TestParseRSSDatetime:
    def test_rfc2822_format(self):
        dt = _parse_rss_datetime("Wed, 14 May 2026 08:00:00 +0000")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 14

    def test_iso8601_format(self):
        dt = _parse_rss_datetime("2026-05-14T08:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_none_for_empty_string(self):
        assert _parse_rss_datetime("") is None

    def test_none_for_garbage(self):
        assert _parse_rss_datetime("not a date at all") is None


# ── EastMoneySpider parsing ──────────────────────────────────────────────────

class TestEastMoneySpider:
    def _fetch_with_data(self, payload: dict):
        spider = EastMoneySpider()
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status = MagicMock()
        with patch("src.services.sentinel.spiders.json_api.requests.get", return_value=mock_resp):
            return spider.fetch()

    def test_parses_standard_response(self):
        payload = {
            "errno": 0,
            "data": {"list": [
                {
                    "Art_Title": "A-share limit-up review",
                    "Art_Url": "https://finance.eastmoney.com/news/1.html",
                    "Art_ShowTime": "2026-05-14 09:30:00",
                    "Art_Source": "EastMoney",
                    "Art_Summary": "Daily limit-up board recap",
                }
            ]},
        }
        articles = self._fetch_with_data(payload)
        assert len(articles) == 1
        assert articles[0].title == "A-share limit-up review"
        assert articles[0].source_name == "EastMoney"

    def test_skips_items_without_title(self):
        payload = {"data": {"list": [{"Art_Url": "https://x.com", "Art_Source": "X"}]}}
        articles = self._fetch_with_data(payload)
        assert articles == []

    def test_api_error_returns_empty(self):
        spider = EastMoneySpider()
        with patch(
            "src.services.sentinel.spiders.json_api.requests.get",
            side_effect=Exception("timeout"),
        ):
            assert spider.fetch() == []


# ── SpiderBase behaviours ─────────────────────────────────────────────────────

class TestSpiderBase:
    def test_enabled_when_all(self):
        config = SentinelConfig(enabled_spiders="all")
        spider = GoogleNewsENSpider()
        assert spider.is_enabled(config)

    def test_enabled_by_name(self):
        config = SentinelConfig(enabled_spiders="google_news_en,eastmoney")
        assert GoogleNewsENSpider().is_enabled(config)
        assert EastMoneySpider().is_enabled(config)

    def test_disabled_when_not_in_list(self):
        config = SentinelConfig(enabled_spiders="eastmoney")
        assert not GoogleNewsENSpider().is_enabled(config)

    def test_healthy_initially(self):
        assert GoogleNewsENSpider().is_healthy()


# ── RSSHubSpider ──────────────────────────────────────────────────────────────

class TestRSSHubSpider:
    def test_configure_sets_base_url(self):
        spider = CLSRSSHubSpider()
        config = SentinelConfig(rsshub_base_url="http://myrsshub:1200", rsshub_timeout=20)
        spider.configure(config)
        assert spider._rsshub_base_url == "http://myrsshub:1200"
        assert spider.timeout_seconds == 20

    def test_feed_url_uses_route(self):
        spider = CLSRSSHubSpider()
        spider._rsshub_base_url = "http://localhost:1200"
        assert spider._build_feed_url() == "http://localhost:1200/cls/telegraph"

    def test_skip_when_no_base_url(self):
        spider = CLSRSSHubSpider()
        spider._rsshub_base_url = ""
        assert spider.fetch() == []
