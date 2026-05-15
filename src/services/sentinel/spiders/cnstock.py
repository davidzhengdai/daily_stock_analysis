# -*- coding: utf-8 -*-
from .rsshub import RSSHubSpider


class CNStockRSSHubSpider(RSSHubSpider):
    """上海证券报 via RSSHub (/cnstock/news)."""

    name = "cnstock"
    route = "/cnstock/news"
    source_name = "上海证券报"
    source_url = "https://www.cnstock.com"
    category = "finance"
    language = "zh"
    interval_minutes = 30
    max_items_per_run = 30
