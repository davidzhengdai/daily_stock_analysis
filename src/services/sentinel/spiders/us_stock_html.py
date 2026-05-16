# -*- coding: utf-8 -*-
"""US stock HTML news spiders — StockAnalysis.com and Finviz per-ticker news.

Both spiders are store-aware: they read watched_stocks and filter for US tickers
(1-5 uppercase letters, optional dot-suffix like BRK.B).

StockAnalysisNewsSpider   → https://stockanalysis.com/stocks/{ticker}/news/
FinvizNewsSpider          → https://finviz.com/quote.ashx?t={ticker}&ty=l

These pages are server-side rendered and do not require JavaScript.
"""
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import urljoin

import requests

from ..models import RawArticle
from ..store import NewsStore
from .base import SpiderBase

logger = logging.getLogger(__name__)

_US_TICKER_RE = re.compile(r'^[A-Z]{1,5}(\.[-A-Z]+)?$')

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _is_us_ticker(code: str) -> bool:
    return bool(_US_TICKER_RE.match(code.strip()))


def _extract_text(snippet: str) -> str:
    text = re.sub(r"<[^>]+>", " ", snippet)
    return re.sub(r"\s+", " ", text).strip()


# ── StockAnalysis.com ─────────────────────────────────────────────────────────

class _SAParser(HTMLParser):
    """Parses stockanalysis.com/stocks/{ticker}/news/ to extract news links."""

    def __init__(self) -> None:
        super().__init__()
        self.items: List[dict] = []
        self._in_article = False
        self._cur: dict = {}
        self._depth = 0
        self._article_depth = 0

    def handle_starttag(self, tag: str, attrs):
        attr_map = dict(attrs)
        self._depth += 1

        # Article blocks: <div class="...news-item..."> or <article ...>
        if tag in ("article",) or (
            tag == "div" and "news" in attr_map.get("class", "")
        ):
            self._in_article = True
            self._article_depth = self._depth
            self._cur = {}
            return

        if self._in_article and tag == "a":
            href = attr_map.get("href", "")
            # Capture first link that looks like a news article link
            if href and "href" not in self._cur:
                if href.startswith("http") or href.startswith("/"):
                    self._cur["href"] = href

        if self._in_article and tag == "time":
            dt_val = attr_map.get("datetime", "")
            if dt_val and "published_at" not in self._cur:
                self._cur["published_at"] = dt_val

    def handle_endtag(self, tag: str):
        if self._in_article and self._depth == self._article_depth:
            if self._cur.get("href") and self._cur.get("title"):
                self.items.append(self._cur)
            self._in_article = False
            self._cur = {}
        self._depth -= 1

    def handle_data(self, data: str):
        if not self._in_article:
            return
        text = data.strip()
        if text and len(text) > 10 and "title" not in self._cur:
            self._cur["title"] = text


def _parse_stockanalysis(html: str, ticker: str) -> List[dict]:
    """Extract news items from stockanalysis.com page HTML.

    Falls back to regex link extraction if the DOM parser yields nothing.
    """
    parser = _SAParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    if parser.items:
        return parser.items

    # Regex fallback: grab <a href> links from the news section
    base = f"https://stockanalysis.com/stocks/{ticker.lower()}/news/"
    items = []
    seen: set = set()
    link_re = re.compile(
        r'<a\s+[^>]*href=["\']([^"\']*news[^"\']*)["\'][^>]*>\s*([^<]{15,200}?)\s*</a>',
        re.IGNORECASE | re.DOTALL,
    )
    time_re = re.compile(r'<time[^>]*datetime=["\']([^"\']+)["\']')
    for m in link_re.finditer(html):
        href, raw_title = m.group(1), m.group(2)
        title = _extract_text(raw_title)
        if not title or len(title) < 10:
            continue
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        # Look for nearby <time datetime=...>
        ctx = html[max(0, m.start() - 300): m.end() + 300]
        tm = time_re.search(ctx)
        pub = tm.group(1) if tm else None
        items.append({"href": url, "title": title, "published_at": pub})
        if len(items) >= 20:
            break
    return items


class StockAnalysisNewsSpider(SpiderBase):
    """Per-ticker news from stockanalysis.com for watched US stocks."""

    name = "stockanalysis_us_news"
    category = "finance"
    interval_minutes = 60
    max_items_per_run = 100
    language = "en"
    source_name = "StockAnalysis"
    source_url = "https://stockanalysis.com"

    def __init__(self, store: NewsStore, max_stocks: int = 20) -> None:
        super().__init__()
        self._store = store
        self._max_stocks = max_stocks

    def fetch(self) -> List[RawArticle]:
        stocks = self._store.get_watched_stocks()
        us_stocks = [s for s in stocks if _is_us_ticker(s.get("code", ""))]
        if not us_stocks:
            self._record_result(0)
            return []

        articles: List[RawArticle] = []
        for stock in us_stocks[: self._max_stocks]:
            code = stock["code"]
            name = stock.get("name", "")
            try:
                articles.extend(self._fetch_for_ticker(code, name))
            except Exception:
                logger.exception("[%s] error fetching %s", self.name, code)

        self._record_result(len(articles))
        return articles

    def fetch_single(self, code: str, name: str) -> List[RawArticle]:
        """Immediately fetch news for one US ticker."""
        if not _is_us_ticker(code):
            return []
        try:
            return self._fetch_for_ticker(code, name)
        except Exception:
            logger.exception("[%s] error in fetch_single for %s", self.name, code)
            return []

    def _fetch_for_ticker(self, code: str, name: str) -> List[RawArticle]:
        ticker = code.lower().replace(".", "-")
        url = f"https://stockanalysis.com/stocks/{ticker}/news/"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            if resp.status_code == 404:
                logger.debug("[%s] %s not found (404)", self.name, code)
                return []
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[%s] request error for %s: %s", self.name, code, exc)
            return []

        raw_items = _parse_stockanalysis(resp.text, code)
        articles: List[RawArticle] = []
        for item in raw_items[: self.max_items_per_run]:
            href = item.get("href", "")
            title = item.get("title", "").strip()
            if not href or not title:
                continue
            if not href.startswith("http"):
                href = urljoin(url, href)
            pub_dt = _parse_iso_dt(item.get("published_at"))
            articles.append(RawArticle(
                url=href,
                title=title,
                content="",
                source_name=self.source_name,
                source_url=self.source_url,
                spider_name=self.name,
                language=self.language,
                published_at=pub_dt,
            ))
        return articles


# ── Finviz ────────────────────────────────────────────────────────────────────

_FINVIZ_NEWS_RE = re.compile(
    r'<tr[^>]*class=["\'][^"\']*news[^"\']*["\'][^>]*>(.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)
_FINVIZ_LINK_RE = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]{10,200})</a>',
    re.IGNORECASE,
)
_FINVIZ_DATE_RE = re.compile(r'(\w+\s+\d+(?:,\s*\d{4})?|\d{2}/\d{2}/\d{2,4}|\w+\s+\d+:\d+\w+)')


def _parse_finviz(html: str) -> List[dict]:
    items: List[dict] = []
    seen: set = set()

    # The news table rows in finviz contain class="news_date-cell" and similar
    # We extract all external links from the news section of the page
    news_section_re = re.compile(
        r'id=["\']news-table["\'][^>]*>(.*?)</table>',
        re.IGNORECASE | re.DOTALL,
    )
    m = news_section_re.search(html)
    section = m.group(1) if m else html  # fall back to full page

    # Each row: date cell + link cell
    row_re = re.compile(r'<tr[^>]*>(.*?)</tr>', re.IGNORECASE | re.DOTALL)
    date_cell_re = re.compile(
        r'<td[^>]*>\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
        r'[^<]{3,25}|\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}[AP]M)\s*</td>',
        re.IGNORECASE,
    )
    link_cell_re = re.compile(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]{10,200})</a>',
        re.IGNORECASE,
    )

    last_date: Optional[str] = None
    for row_m in row_re.finditer(section):
        row_html = row_m.group(1)
        date_m = date_cell_re.search(row_html)
        if date_m:
            last_date = date_m.group(1).strip()
        link_m = link_cell_re.search(row_html)
        if not link_m:
            continue
        href, title = link_m.group(1).strip(), link_m.group(2).strip()
        title = _extract_text(title)
        if not href or not title or len(title) < 10:
            continue
        if href in seen:
            continue
        seen.add(href)
        items.append({"href": href, "title": title, "published_at": last_date})
        if len(items) >= 25:
            break
    return items


class FinvizNewsSpider(SpiderBase):
    """Per-ticker news table from finviz.com for watched US stocks."""

    name = "finviz_us_news"
    category = "finance"
    interval_minutes = 60
    max_items_per_run = 100
    language = "en"
    source_name = "Finviz"
    source_url = "https://finviz.com"

    def __init__(self, store: NewsStore, max_stocks: int = 20) -> None:
        super().__init__()
        self._store = store
        self._max_stocks = max_stocks

    def fetch(self) -> List[RawArticle]:
        stocks = self._store.get_watched_stocks()
        us_stocks = [s for s in stocks if _is_us_ticker(s.get("code", ""))]
        if not us_stocks:
            self._record_result(0)
            return []

        articles: List[RawArticle] = []
        for stock in us_stocks[: self._max_stocks]:
            code = stock["code"]
            name = stock.get("name", "")
            try:
                articles.extend(self._fetch_for_ticker(code, name))
            except Exception:
                logger.exception("[%s] error fetching %s", self.name, code)

        self._record_result(len(articles))
        return articles

    def fetch_single(self, code: str, name: str) -> List[RawArticle]:
        if not _is_us_ticker(code):
            return []
        try:
            return self._fetch_for_ticker(code, name)
        except Exception:
            logger.exception("[%s] error in fetch_single for %s", self.name, code)
            return []

    def _fetch_for_ticker(self, code: str, name: str) -> List[RawArticle]:
        url = f"https://finviz.com/quote.ashx?t={code.upper()}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            if resp.status_code in (403, 429):
                logger.debug("[%s] %s blocked (%d)", self.name, code, resp.status_code)
                return []
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[%s] request error for %s: %s", self.name, code, exc)
            return []

        raw_items = _parse_finviz(resp.text)
        articles: List[RawArticle] = []
        for item in raw_items[: self.max_items_per_run]:
            href = item.get("href", "")
            title = item.get("title", "").strip()
            if not href or not title:
                continue
            pub_dt = _parse_loose_dt(item.get("published_at"))
            articles.append(RawArticle(
                url=href,
                title=title,
                content="",
                source_name=self.source_name,
                source_url=self.source_url,
                spider_name=self.name,
                language=self.language,
                published_at=pub_dt,
            ))
        return articles


# ── date helpers ──────────────────────────────────────────────────────────────

def _parse_iso_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        s = raw.rstrip("Z")
        if "+" in s:
            s = s[: s.index("+")]
        dt = datetime.fromisoformat(s[:19])
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_loose_dt(raw: Optional[str]) -> Optional[datetime]:
    """Parse Finviz date strings like 'May-15-24 08:00AM' or 'May 15'."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in (
        "%b-%d-%y %I:%M%p",
        "%b-%d-%y",
        "%m/%d/%Y %I:%M%p",
        "%m/%d/%Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%b %d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
