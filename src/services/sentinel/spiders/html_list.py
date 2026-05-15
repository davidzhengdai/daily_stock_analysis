# -*- coding: utf-8 -*-
"""HTMLListSpider — base class for HTML list-page scrapers.

Uses stdlib html.parser to avoid adding bs4/lxml as new dependencies.
Subclasses implement `_parse_items(html)` for site-specific extraction.
"""
import logging
import re
import time
from abc import abstractmethod
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests

from ..models import RawArticle
from .base import SpiderBase

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DSA-Sentinel/1.0; +https://github.com/ZhuLinsen/daily_stock_analysis)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _extract_text(html_snippet: str) -> str:
    """Strip all HTML tags and normalise whitespace."""
    text = re.sub(r"<[^>]+>", " ", html_snippet)
    return re.sub(r"\s+", " ", text).strip()


def _parse_cn_date(raw: str) -> Optional[datetime]:
    """Try to parse common Chinese date patterns → aware UTC datetime."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Search for YYYY-MM-DD embedded in a string
    m = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})", raw)
    if m:
        return _parse_cn_date(m.group(1))
    return None


class HTMLListSpider(SpiderBase):
    """Base class: GET list_url, call _parse_items(html), yield RawArticle objects."""

    list_url: str = ""
    encoding: str = "utf-8"
    request_timeout: int = 15

    def fetch(self) -> List[RawArticle]:
        if not self.list_url:
            logger.warning("[%s] list_url is empty — skipping", self.name)
            return []

        html = self._get_html(self.list_url)
        if not html:
            self._on_empty()
            return []

        items = self._parse_items(html)
        if not items:
            self._on_empty()
            return []

        self._on_success()
        articles: List[RawArticle] = []
        for item in items[: self.max_items_per_run]:
            try:
                article = self._item_to_article(item)
                if article:
                    articles.append(article)
            except Exception:
                logger.exception("[%s] error building article from %s", self.name, item.get("url", "?"))
        return articles

    @abstractmethod
    def _parse_items(self, html: str) -> List[dict]:
        """Return list of dicts with at minimum ``url`` and ``title`` keys.

        Optional keys: ``published_at`` (datetime or str), ``content``.
        """

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_html(self, url: str) -> str:
        try:
            resp = requests.get(
                url,
                headers=_DEFAULT_HEADERS,
                timeout=self.request_timeout,
            )
            resp.raise_for_status()
            resp.encoding = self.encoding
            return resp.text
        except Exception as exc:
            logger.warning("[%s] HTTP error fetching %s: %s", self.name, url, exc)
            return ""

    def _item_to_article(self, item: dict) -> Optional[RawArticle]:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        if not url or not title:
            return None

        # Normalise URL (may be relative)
        if not urlparse(url).scheme:
            url = urljoin(self.list_url, url)

        pub_raw = item.get("published_at")
        pub_dt: Optional[datetime] = None
        if isinstance(pub_raw, datetime):
            pub_dt = pub_raw
        elif isinstance(pub_raw, str) and pub_raw:
            pub_dt = _parse_cn_date(pub_raw)

        return RawArticle(
            url=url,
            title=title,
            content=item.get("content", ""),
            source_name=self.source_name if hasattr(self, "source_name") else self.name,
            source_url=self.source_url if hasattr(self, "source_url") else self.list_url,
            spider_name=self.name,
            language=self.language,
            published_at=pub_dt,
        )

    @staticmethod
    def _grep_list_items(html: str, base_url: str = "") -> List[dict]:
        """Generic extractor: find <a href> tags that look like article links.

        Works on simple list pages where each news item is an ``<a>`` tag with
        Chinese/English text and an href pointing to an article path.
        """
        # Look for list items: <li ...>...<a href="...">title</a>...<date>...</li>
        # or simpler: just <a href> with non-empty text inside content areas
        items = []
        seen_urls: set = set()

        # Pattern: capture href + inner text, optionally a nearby date
        link_re = re.compile(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        date_re = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}")

        for m in link_re.finditer(html):
            href, raw_title = m.group(1), m.group(2)
            title = _extract_text(raw_title)
            if len(title) < 5 or len(title) > 200:
                continue
            # Skip navigation links (no path, anchors, JS)
            if href.startswith("#") or href.startswith("javascript"):
                continue
            url = urljoin(base_url, href) if base_url else href
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Try to find a nearby date in surrounding context (±200 chars)
            start = max(0, m.start() - 200)
            end = min(len(html), m.end() + 200)
            ctx = html[start:end]
            date_match = date_re.search(ctx)
            pub = date_match.group(0) if date_match else None

            items.append({"url": url, "title": title, "published_at": pub})

        return items
