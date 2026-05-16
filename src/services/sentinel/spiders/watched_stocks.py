# -*- coding: utf-8 -*-
"""WatchedStocksNewsSpider — fetches Google News RSS for user-watched stocks.

Reads the watched_stocks table from the store on each run and issues one
Google News RSS search request per stock, aggregating up to max_items_per_run
articles total.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List

import requests

from ..models import RawArticle
from ..store import NewsStore
from .base import SpiderBase

logger = logging.getLogger(__name__)

_ATOM_NS = "http://www.w3.org/2005/Atom"


def _google_news_url(code: str, name: str) -> str:
    from urllib.parse import quote_plus
    q = quote_plus(f"{name} {code}".strip() if name else code)
    # 6-digit numeric → A-share (zh-CN), alphanumeric → US (en-US), else HK (zh-HK)
    clean = code.replace(".", "").replace("-", "")
    if clean.isdigit() and len(clean) == 6:
        return f"https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    elif clean.isdigit():
        return f"https://news.google.com/rss/search?q={q}&hl=zh-HK&gl=HK&ceid=HK:zh-Hant"
    else:
        return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _detect_language(code: str) -> str:
    clean = code.replace(".", "").replace("-", "")
    if clean.isdigit():
        return "zh"
    return "en"


class WatchedStocksNewsSpider(SpiderBase):
    name = "watched_stocks_news"
    category = "finance"
    interval_minutes = 30
    max_items_per_run = 200
    language = "zh"

    def __init__(self, store: NewsStore, max_stocks: int = 50) -> None:
        super().__init__()
        self._store = store
        self._max_stocks = max_stocks

    def fetch(self) -> List[RawArticle]:
        stocks = self._store.get_watched_stocks()
        if not stocks:
            self._record_result(0)
            return []

        articles: List[RawArticle] = []
        for stock in stocks[: self._max_stocks]:
            code = stock.get("code", "")
            name = stock.get("name", "")
            try:
                articles.extend(self._fetch_for_stock(code, name))
            except Exception:
                logger.exception("[%s] unexpected error fetching stock %s", self.name, code)

        self._record_result(len(articles))
        return articles

    def _fetch_for_stock(self, code: str, name: str) -> List[RawArticle]:
        url = _google_news_url(code, name)
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DSA-Sentinel/1.0)"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("[%s] request error for %s: %s", self.name, code, exc)
            return []
        return self._parse_feed(resp.content, code, name)

    def _parse_feed(self, content: bytes, code: str, name: str) -> List[RawArticle]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []

        items = root.findall(".//item") or root.findall(f".//{{{_ATOM_NS}}}entry")
        articles: List[RawArticle] = []

        for el in items:
            def _t(tag: str) -> str:
                n = el.find(tag)
                if n is None:
                    n = el.find(f"{{{_ATOM_NS}}}{tag}")
                return (n.text or "").strip() if n is not None else ""

            title = _t("title")
            link_el = el.find("link")
            url = _t("link")
            if link_el is not None and link_el.get("href"):
                url = link_el.get("href")
            description = _t("description") or _t("summary")
            pub_raw = _t("pubDate") or _t("published") or _t("updated")

            if not title or not url:
                continue

            pub_dt = None
            if pub_raw:
                try:
                    pub_dt = parsedate_to_datetime(pub_raw)
                except Exception:
                    try:
                        pub_dt = datetime.fromisoformat(pub_raw[:19]).replace(tzinfo=timezone.utc)
                    except Exception:
                        pass

            articles.append(RawArticle(
                url=url,
                title=title,
                content=description[:500],
                published_at=pub_dt,
                source_name="Google News",
                source_url="https://news.google.com",
                spider_name=self.name,
                language=_detect_language(code),
            ))
            if len(articles) >= self.max_items_per_run:
                break

        return articles
