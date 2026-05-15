# -*- coding: utf-8 -*-
from .rsshub import RSSHubSpider


class STCNRSSHubSpider(RSSHubSpider):
    """证券时报 via RSSHub (/stcn/news)."""

    name = "stcn"
    route = "/stcn/news"
    source_name = "证券时报"
    source_url = "https://www.stcn.com"
    category = "finance"
    language = "zh"
    interval_minutes = 30
    max_items_per_run = 30
