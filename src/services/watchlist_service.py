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
import threading
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
        self.refresh_market_data_async(item["code"])
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

    def refresh_market_data_async(self, code: str) -> None:
        """Best-effort daily-bar refresh after watchlist changes."""
        normalized = (code or "").strip().upper()
        if not normalized:
            return

        thread = threading.Thread(
            target=self.refresh_market_data,
            args=(normalized,),
            name=f"watchlist-refresh-{normalized}",
            daemon=True,
        )
        thread.start()

    def refresh_market_data(self, code: str, days: int = 60) -> int:
        """Fetch and persist recent daily bars for a watched stock."""
        normalized = (code or "").strip().upper()
        if not normalized:
            return 0
        try:
            from data_provider.base import DataFetcherManager
            from src.storage import DatabaseManager

            df, source = DataFetcherManager().get_daily_data(normalized, days=days)
            if df is None or df.empty:
                logger.warning("自选股 %s 行情预热未返回数据", normalized)
                return 0
            saved = DatabaseManager.get_instance().save_daily_data(
                df,
                normalized,
                data_source=f"watchlist:{source}",
            )
            logger.info("自选股 %s 行情预热完成，source=%s, new_rows=%s", normalized, source, saved)
            return saved
        except Exception as exc:
            logger.warning("自选股 %s 行情预热失败: %s", normalized, exc)
            return 0
