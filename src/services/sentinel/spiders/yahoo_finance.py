# -*- coding: utf-8 -*-
"""Yahoo Finance RSS spider.

Yahoo Finance provides native RSS at finance.yahoo.com/rss/topstories
for general market/finance top stories — no API key required.
"""
from .native_rss import NativeRSSSpider


class YahooFinanceRSSSpider(NativeRSSSpider):
    name = "yahoo_finance_rss"
    category = "finance"
    interval_minutes = 30
    max_items_per_run = 30
    language = "en"
    feed_url = "https://finance.yahoo.com/rss/topstories"
