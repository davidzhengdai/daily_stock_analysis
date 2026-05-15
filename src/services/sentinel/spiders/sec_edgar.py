# -*- coding: utf-8 -*-
from .native_rss import NativeRSSSpider


class SECEdgarSpider(NativeRSSSpider):
    """SEC EDGAR current filings (8-K) via official Atom feed."""

    name = "sec_edgar"
    feed_url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=8-K&dateb=&owner=include"
        "&count=20&search_text=&output=atom"
    )
    source_name = "SEC EDGAR"
    source_url = "https://www.sec.gov"
    category = "regulatory"
    language = "en"
    interval_minutes = 60
    max_items_per_run = 20
