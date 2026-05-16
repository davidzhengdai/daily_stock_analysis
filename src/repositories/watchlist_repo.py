# -*- coding: utf-8 -*-
"""
===================================
自选股数据访问层
===================================

职责：
1. 封装 Watchlist 表的 CRUD 操作
2. 提供自选股查询、添加、删除、更新接口
"""

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import desc, select

from src.storage import DatabaseManager, Watchlist

logger = logging.getLogger(__name__)


class WatchlistRepo:
    """自选股数据访问层，封装 Watchlist 表操作。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def list_all(self) -> List[dict]:
        """返回全部自选股，按 added_at 降序。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(Watchlist).order_by(desc(Watchlist.added_at))
            ).scalars().all()
            return [row.to_dict() for row in rows]

    def get(self, code: str) -> Optional[dict]:
        """按股票代码查询，不存在返回 None。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(Watchlist).where(Watchlist.code == code)
            ).scalar_one_or_none()
            return row.to_dict() if row else None

    def upsert(self, code: str, name: str = "", notes: str = "") -> dict:
        """插入或更新自选股记录。若已存在则更新 name/notes，保留 added_at。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(Watchlist).where(Watchlist.code == code)
            ).scalar_one_or_none()
            if row is None:
                row = Watchlist(code=code, name=name, notes=notes)
                session.add(row)
            else:
                row.name = name
                row.notes = notes
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def remove(self, code: str) -> bool:
        """删除自选股，返回是否实际删除了记录。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(Watchlist).where(Watchlist.code == code)
            ).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def mark_analyzed(self, code: str) -> None:
        """更新 last_analyzed_at 为当前时间。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(Watchlist).where(Watchlist.code == code)
            ).scalar_one_or_none()
            if row is not None:
                row.last_analyzed_at = datetime.utcnow()
                session.commit()

    def is_watched(self, code: str) -> bool:
        """判断股票代码是否在自选股列表中。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(Watchlist.code).where(Watchlist.code == code)
            ).scalar_one_or_none()
            return row is not None
