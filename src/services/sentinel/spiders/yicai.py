# -*- coding: utf-8 -*-
from .rsshub import RSSHubSpider


class YicaiRSSHubSpider(RSSHubSpider):
    """第一财经快讯 via RSSHub (/yicai/brief)."""

    name = "yicai"
    route = "/yicai/brief"
    source_name = "第一财经"
    source_url = "https://www.yicai.com"
    category = "finance"
    language = "zh"
    interval_minutes = 30
    max_items_per_run = 30
