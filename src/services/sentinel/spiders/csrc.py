# -*- coding: utf-8 -*-
"""证监会 (CSRC) news list spider — HTML list page."""
import re
from typing import List

from .html_list import HTMLListSpider, _extract_text


class CSRCSpider(HTMLListSpider):
    """China Securities Regulatory Commission press releases."""

    name = "csrc"
    list_url = "http://www.csrc.gov.cn/csrc/c101924/zfxxgk_zdgk.shtml"
    source_name = "中国证监会"
    source_url = "http://www.csrc.gov.cn"
    category = "regulatory"
    language = "zh"
    interval_minutes = 60
    max_items_per_run = 20

    def _parse_items(self, html: str) -> List[dict]:
        # CSRC uses a standard <ul class="list_009"> or <ul class="news_list"> pattern
        # with <li><a href="...">title</a><span>date</span></li>
        items = []
        seen: set = set()

        # Match list items containing an article link + date
        li_re = re.compile(
            r'<li[^>]*>\s*<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
            r'[^<]*(?:<[^>]+>)*[^<]*(\d{4}-\d{2}-\d{2})?',
            re.IGNORECASE | re.DOTALL,
        )
        for m in li_re.finditer(html):
            href = m.group(1).strip()
            title = _extract_text(m.group(2))
            date = m.group(3)
            if len(title) < 5 or href in seen:
                continue
            seen.add(href)
            items.append({"url": href, "title": title, "published_at": date})

        # Fallback to generic extractor if pattern misses
        if not items:
            items = self._grep_list_items(html, self.source_url)

        return items
