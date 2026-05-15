# -*- coding: utf-8 -*-
"""JSONAPISpider — base class for spiders that call undocumented JSON endpoints.

Chinese financial sites (东方财富, 雪球 …) have stable mobile/H5 JSON APIs that
are more reliable than HTML scraping and don't require RSS.
"""
import logging
from abc import abstractmethod
from typing import Any, Dict, List

import requests

from ..models import RawArticle
from .base import SpiderBase

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; DSA-Sentinel/1.0)"


class JSONAPISpider(SpiderBase):
    """Base class for JSON-API backed spiders."""

    api_url: str = ""
    name: str = "json_api"

    def _default_headers(self) -> Dict[str, str]:
        return {"User-Agent": _UA, "Accept": "application/json"}

    def _default_params(self) -> Dict[str, Any]:
        return {}

    def _get_json(self) -> Any:
        try:
            resp = requests.get(
                self.api_url,
                params=self._default_params(),
                headers=self._default_headers(),
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("[%s] API call failed: %s", self.name, exc)
            self._record_error()
            return None

    @abstractmethod
    def _parse(self, data: Any) -> List[RawArticle]:
        """Convert raw JSON response to RawArticle list."""
        ...

    def fetch(self) -> List[RawArticle]:
        data = self._get_json()
        if data is None:
            return []
        articles = self._parse(data)
        self._record_result(len(articles))
        logger.info("[%s] fetched %d items", self.name, len(articles))
        return articles
