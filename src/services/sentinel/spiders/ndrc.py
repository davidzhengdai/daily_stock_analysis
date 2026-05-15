# -*- coding: utf-8 -*-
"""发改委 (NDRC) news list spider — HTML list page."""
import re
from typing import List

from .html_list import HTMLListSpider, _extract_text


class NDRCSpider(HTMLListSpider):
    """National Development and Reform Commission press releases."""

    name = "ndrc"
    list_url = "https://www.ndrc.gov.cn/xwdt/xwfb/"
    source_name = "国家发展和改革委员会"
    source_url = "https://www.ndrc.gov.cn"
    category = "political"
    language = "zh"
    interval_minutes = 60
    max_items_per_run = 20

    def _parse_items(self, html: str) -> List[dict]:
        items = []
        seen: set = set()

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

        if not items:
            items = self._grep_list_items(html, self.source_url)

        return items
