# -*- coding: utf-8 -*-
"""Google News RSS spiders (English & Chinese).

Google News provides native RSS at news.google.com/rss/headlines/section/topic/BUSINESS
for topic-based feeds — no API key, no rate limits.
"""
from .native_rss import NativeRSSSpider


class GoogleNewsENSpider(NativeRSSSpider):
    name = "google_news_en"
    category = "finance"
    interval_minutes = 15
    max_items_per_run = 50
    language = "en"
    # Topic-based business/finance feed; no query parameter needed
    feed_url = (
        "https://news.google.com/rss/headlines/section/topic/BUSINESS"
        "?hl=en-US&gl=US&ceid=US:en"
    )


class GoogleNewsCNSpider(NativeRSSSpider):
    name = "google_news_cn"
    category = "finance"
    interval_minutes = 15
    max_items_per_run = 50
    language = "zh"
    feed_url = (
        "https://news.google.com/rss/headlines/section/topic/BUSINESS"
        "?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
