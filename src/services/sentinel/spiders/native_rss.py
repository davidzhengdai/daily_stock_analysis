# -*- coding: utf-8 -*-
"""NativeRSSSpider — fetches RSS/Atom feeds that sites provide natively.

Uses stdlib xml.etree.ElementTree (no feedparser dependency) to stay consistent
with the existing search_service.py RSS parsing pattern.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List
from urllib.parse import urlparse

import requests

from ..models import RawArticle
from .base import SpiderBase

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; DSA-Sentinel/1.0)"

# Atom namespace
_ATOM_NS = "http://www.w3.org/2005/Atom"


def _parse_rss_datetime(text: str) -> datetime | None:
    """Parse RFC 2822 or ISO-8601 datetime strings."""
    if not text:
        return None
    text = text.strip()
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text[:19], fmt[:len(fmt)])
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_items(root: ET.Element) -> List[ET.Element]:
    """Support both RSS <item> and Atom <entry>."""
    items = root.findall(".//item")
    if items:
        return items
    return root.findall(f".//{{{_ATOM_NS}}}entry")


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _find(parent: ET.Element, *tags: str) -> ET.Element | None:
    for tag in tags:
        el = parent.find(tag)
        if el is not None:
            return el
        el = parent.find(f"{{{_ATOM_NS}}}{tag}")
        if el is not None:
            return el
    return None


class NativeRSSSpider(SpiderBase):
    """Base class for spiders that consume a native RSS/Atom feed URL."""

    feed_url: str = ""
    name: str = "native_rss"

    def _build_feed_url(self) -> str:
        return self.feed_url

    def fetch(self) -> List[RawArticle]:
        url = self._build_feed_url()
        if not url:
            return []

        try:
            resp = requests.get(
                url,
                timeout=self.timeout_seconds,
                headers={"User-Agent": _UA},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("[%s] fetch failed: %s", self.name, exc)
            self._record_error()
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("[%s] XML parse error: %s", self.name, exc)
            self._record_error()
            return []

        articles: List[RawArticle] = []
        source_domain = urlparse(url).netloc

        for item in _extract_items(root):
            title_el = _find(item, "title")
            link_el = _find(item, "link")
            pubdate_el = _find(item, "pubDate", "published", "updated")
            desc_el = _find(item, "description", "summary", "content")
            source_el = item.find("source")

            title = _text(title_el)
            if not title:
                continue

            # Atom <link> uses href attribute, RSS uses text content
            if link_el is not None:
                link = link_el.get("href") or _text(link_el)
            else:
                link = ""

            content = _text(desc_el)[:2000]
            pub = _parse_rss_datetime(_text(pubdate_el))
            source_name = _text(source_el) if source_el is not None else source_domain

            articles.append(RawArticle(
                url=link,
                title=title,
                content=content,
                published_at=pub,
                source_name=source_name or self.name,
                source_url=f"https://{source_domain}",
                spider_name=self.name,
                language=self.language,
            ))

            if len(articles) >= self.max_items_per_run:
                break

        self._record_result(len(articles))
        logger.info("[%s] fetched %d items from %s", self.name, len(articles), url)
        return articles
