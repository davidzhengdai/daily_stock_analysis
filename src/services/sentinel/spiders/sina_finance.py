# -*- coding: utf-8 -*-
from .rsshub import RSSHubSpider


class SinaFinanceRSSHubSpider(RSSHubSpider):
    """新浪财经滚动新闻 via RSSHub (/sina/finance/roll)."""

    name = "sina_finance"
    route = "/sina/finance/roll"
    source_name = "新浪财经"
    source_url = "https://finance.sina.com.cn"
    category = "finance"
    language = "zh"
    interval_minutes = 30
    max_items_per_run = 40
