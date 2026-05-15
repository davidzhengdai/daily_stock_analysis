# -*- coding: utf-8 -*-
"""RSSHubSpider — routes through a locally-hosted RSSHub instance.

RSSHub (https://rsshub.app) synthesises RSS feeds for sites without native RSS.
Most Chinese financial sites (财联社, 证券时报, 新浪财经 …) are covered by
RSSHub routes.  Point SENTINEL_RSSHUB_BASE_URL at a running Docker instance:

    docker run -d -p 1200:1200 diygod/rsshub

Each concrete spider sets `route` (e.g. "/cls/telegraph") and optionally
`rsshub_timeout` (RSSHub itself must fetch from the upstream site, so it needs
more time than a direct RSS call).
"""
import logging
from typing import List, TYPE_CHECKING

from ..models import RawArticle
from .native_rss import NativeRSSSpider

if TYPE_CHECKING:
    from ..config import SentinelConfig

logger = logging.getLogger(__name__)


class RSSHubSpider(NativeRSSSpider):
    """Base class for spiders backed by a local RSSHub instance."""

    route: str = ""         # e.g. "/cls/telegraph"
    name: str = "rsshub"
    timeout_seconds: int = 20    # RSSHub itself fetches upstream → needs more time

    # Injected by SentinelService before first fetch call
    _rsshub_base_url: str = "http://localhost:1200"

    def configure(self, config: "SentinelConfig") -> None:
        """Called by SentinelService to inject runtime config."""
        self._rsshub_base_url = config.rsshub_base_url
        self.timeout_seconds = config.rsshub_timeout

    def _build_feed_url(self) -> str:
        if not self.route:
            logger.warning("[%s] no RSSHub route configured", self.name)
            return ""
        return f"{self._rsshub_base_url}{self.route}"

    def fetch(self) -> "List[RawArticle]":
        if not self._rsshub_base_url:
            logger.info("[%s] RSSHub disabled (no base URL), skipping", self.name)
            return []
        return super().fetch()
