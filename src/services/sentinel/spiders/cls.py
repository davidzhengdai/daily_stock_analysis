# -*- coding: utf-8 -*-
"""财联社 (CLS) spider via RSSHub.

CLS has no public RSS.  RSSHub route /cls/telegraph synthesises one from
the 电报 (telegraph) fast-news stream.

Requires a running local RSSHub instance (SENTINEL_RSSHUB_BASE_URL).
Falls back gracefully when RSSHub is unavailable.
"""
from .rsshub import RSSHubSpider


class CLSRSSHubSpider(RSSHubSpider):
    name = "cls_telegraph"
    category = "breaking"
    interval_minutes = 15
    max_items_per_run = 50
    language = "zh"
    route = "/cls/telegraph"
    # Display name used in source_name field
    _source_name = "财联社"
