# -*- coding: utf-8 -*-
"""
===================================
自选股业务服务层
===================================

职责：
1. 提供自选股增删改查业务逻辑
2. 支持与 Sentinel 监控列表同步
"""

import logging
from typing import List, Optional

from src.repositories.watchlist_repo import WatchlistRepo

logger = logging.getLogger(__name__)


class WatchlistService:
    """自选股业务服务，封装 WatchlistRepo 并提供 Sentinel 同步。"""

    def __init__(self, repo: Optional[WatchlistRepo] = None):
        self.repo = repo or WatchlistRepo()

    def list_all(self) -> List[dict]:
        """返回全部自选股列表。"""
        return self.repo.list_all()

    def add(self, code: str, name: str = "", notes: str = "") -> dict:
        """添加或更新自选股，返回最新记录。"""
        item = self.repo.upsert(code=code, name=name, notes=notes)
        self.sync_to_sentinel()
        return item

    def remove(self, code: str) -> bool:
        """删除自选股，返回是否实际删除。"""
        removed = self.repo.remove(code)
        if removed:
            self.sync_to_sentinel()
        return removed

    def is_watched(self, code: str) -> bool:
        """判断是否在自选股列表中。"""
        return self.repo.is_watched(code)

    def sync_to_sentinel(self) -> int:
        """
        将当前自选股列表同步到 Sentinel 监控。

        直接调用 sentinel store 以避免 HTTP 循环调用。
        Sentinel 可能未启用，因此用 try/except 保护。

        Returns:
            同步的股票数量；Sentinel 未启用时返回 0。
        """
        try:
            from src.services.sentinel.store import NewsStore  # type: ignore
            from src.services.sentinel.config import SentinelConfig  # type: ignore

            config = SentinelConfig.from_env()
            store = NewsStore(config.db_path)
            items = self.repo.list_all()
            stocks = [{"code": item["code"], "name": item["name"]} for item in items]
            store.upsert_watched_stocks(stocks)
            return len(stocks)
        except ImportError:
            logger.debug("Sentinel 模块不可用，跳过同步")
            return 0
        except Exception as exc:
            logger.warning("同步自选股到 Sentinel 失败: %s", exc)
            return 0
