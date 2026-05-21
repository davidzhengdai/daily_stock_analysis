# -*- coding: utf-8 -*-
"""
淘金列表数据访问层

封装 discovery_list 表的 CRUD 操作。
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select, update

from src.storage import DatabaseManager, DiscoveryItem

logger = logging.getLogger(__name__)

_MARKET_NORM: Dict[str, str] = {
    "A-share": "CN",
    "CN": "CN",
    "cn": "CN",
    "US": "US",
    "us": "US",
    "HK": "HK",
    "hk": "HK",
}


def _normalize_market(market: str) -> str:
    return _MARKET_NORM.get(market, market.upper())


class DiscoveryRepo:
    """淘金列表数据访问层，封装 discovery_list 表操作。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------

    def add(
        self,
        ticker: str,
        name: str,
        market: str,
        sector: str,
        source: str,
        source_run_id: str,
        score: float,
        confidence: int,
        thesis: str,
        themes_json: str,
        trade_horizon: str,
        ttl_days: int,
    ) -> Optional[dict]:
        """插入一条淘金列表记录（同一 ticker+source+source_run_id 时幂等忽略）。"""
        market_norm = _normalize_market(market)
        expires_at = datetime.utcnow() + timedelta(days=ttl_days)

        def _write(session):
            existing = session.execute(
                select(DiscoveryItem).where(
                    DiscoveryItem.ticker == ticker,
                    DiscoveryItem.source == source,
                    DiscoveryItem.source_run_id == source_run_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None
            item = DiscoveryItem(
                ticker=ticker,
                name=name,
                market=market_norm,
                sector=sector or "",
                source=source,
                source_run_id=source_run_id,
                score=score,
                confidence=confidence,
                thesis=thesis or "",
                themes=themes_json or "[]",
                trade_horizon=trade_horizon,
                added_at=datetime.utcnow(),
                expires_at=expires_at,
                status="active",
                allow_auto_trade=True,
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            return item.to_dict()

        try:
            return self.db._run_write_transaction("discovery_add", _write)
        except Exception as exc:
            logger.warning("[DiscoveryRepo] add(%s, %s) 失败: %s", ticker, source, exc)
            return None

    def expire_stale(self) -> int:
        """将 expires_at < utcnow() 的 active 记录标记为 expired，返回更新数。"""
        now = datetime.utcnow()

        def _write(session):
            result = session.execute(
                update(DiscoveryItem)
                .where(DiscoveryItem.status == "active", DiscoveryItem.expires_at < now)
                .values(status="expired")
            )
            session.commit()
            return result.rowcount

        try:
            return self.db._run_write_transaction("discovery_expire", _write) or 0
        except Exception as exc:
            logger.warning("[DiscoveryRepo] expire_stale 失败: %s", exc)
            return 0

    def reject(self, item_id: int) -> bool:
        """标记某条记录为 rejected。"""
        def _write(session):
            item = session.execute(
                select(DiscoveryItem).where(DiscoveryItem.id == item_id)
            ).scalar_one_or_none()
            if item is None:
                return False
            item.status = "rejected"
            item.rejected_at = datetime.utcnow()
            session.commit()
            return True

        try:
            return self.db._run_write_transaction("discovery_reject", _write) or False
        except Exception as exc:
            logger.warning("[DiscoveryRepo] reject(%d) 失败: %s", item_id, exc)
            return False

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------

    def list_active(self) -> List[dict]:
        """返回所有 status='active' 且未过期的记录，按 score 降序。"""
        now = datetime.utcnow()
        with self.db.get_session() as session:
            rows = session.execute(
                select(DiscoveryItem)
                .where(DiscoveryItem.status == "active", DiscoveryItem.expires_at > now)
                .order_by(desc(DiscoveryItem.score))
            ).scalars().all()
            return [r.to_dict() for r in rows]

    def get_auto_trade_stocks(self) -> List[dict]:
        """返回 AutoTradeService 兼容格式的列表，按 ticker 去重（保留最高 score）。"""
        now = datetime.utcnow()
        with self.db.get_session() as session:
            rows = session.execute(
                select(DiscoveryItem)
                .where(
                    DiscoveryItem.status == "active",
                    DiscoveryItem.expires_at > now,
                    DiscoveryItem.allow_auto_trade == True,
                )
                .order_by(desc(DiscoveryItem.score))
            ).scalars().all()

        seen: Dict[str, dict] = {}
        for r in rows:
            t = r.ticker
            if t not in seen or r.score > seen[t]["_score"]:
                trade_horizon = r.trade_horizon or "medium"
                seen[t] = {
                    "code": t,
                    "name": r.name,
                    "market": r.market,
                    "_from_discovery": True,
                    "_discovery_id": r.id,
                    "_trade_context": "discovery_short_term",
                    "_score": r.score,
                    "_source": r.source,
                    "_trade_horizon": trade_horizon,
                }
        return list(seen.values())

    def stats(self) -> Dict[str, Any]:
        """按来源和状态统计数量。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    DiscoveryItem.source,
                    DiscoveryItem.status,
                    func.count(DiscoveryItem.id).label("cnt"),
                )
                .group_by(DiscoveryItem.source, DiscoveryItem.status)
            ).all()
            result: Dict[str, Any] = {}
            for source, status, cnt in rows:
                result.setdefault(source, {})[status] = cnt
            return result
