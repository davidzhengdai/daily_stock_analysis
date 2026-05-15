# -*- coding: utf-8 -*-
"""EastMoney (东方财富) rolling news spider.

Uses the kuaixun (快讯) rolling news endpoint — not the search API used by
EastMoneyNewsProvider in search_service.py.  This endpoint returns the latest
flash news without requiring a search query.
"""
from datetime import datetime, timezone
from typing import Any, List

from ..models import RawArticle
from .json_api import JSONAPISpider


class EastMoneySpider(JSONAPISpider):
    name = "eastmoney"
    category = "finance"
    interval_minutes = 15
    max_items_per_run = 50
    language = "zh"
    timeout_seconds = 10

    api_url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_100.html"

    def _default_headers(self):
        return {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.eastmoney.com/",
            "Accept": "application/json",
        }

    def _parse(self, data: Any) -> List[RawArticle]:
        items = []
        data_block = data.get("data") or {}
        if isinstance(data_block, dict):
            items = data_block.get("list") or data_block.get("articleList") or []
        elif isinstance(data, dict):
            items = data.get("list") or data.get("articleList") or []

        articles: List[RawArticle] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("Art_Title") or item.get("title") or "").strip()
            if not title:
                continue
            url = str(item.get("Art_Url") or item.get("url") or "").strip()
            source = str(item.get("Art_Source") or item.get("source") or "东方财富").strip()
            content = str(item.get("Art_Summary") or item.get("Art_Abstract") or item.get("summary") or "")[:2000]

            pub: datetime | None = None
            raw_time = item.get("Art_ShowTime") or item.get("pubDate") or item.get("time") or ""
            if raw_time:
                try:
                    pub = datetime.fromisoformat(str(raw_time)[:19]).replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            articles.append(RawArticle(
                url=url,
                title=title,
                content=content,
                published_at=pub,
                source_name=source,
                source_url="https://www.eastmoney.com",
                spider_name=self.name,
                language=self.language,
            ))
            if len(articles) >= self.max_items_per_run:
                break

        return articles
