# -*- coding: utf-8 -*-
"""央行 (PBoC) news list spider — HTML list page."""
import re
from typing import List

from .html_list import HTMLListSpider, _extract_text


class PBCSpider(HTMLListSpider):
    """People's Bank of China news and press releases."""

    name = "pbc"
    list_url = "http://www.pbc.gov.cn/rmyh/4128165/index.html"
    source_name = "中国人民银行"
    source_url = "http://www.pbc.gov.cn"
    category = "macro"
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
