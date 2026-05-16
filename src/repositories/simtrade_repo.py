# -*- coding: utf-8 -*-
"""
===================================
模拟交易数据访问层
===================================

职责：
1. 封装 SimulatedAccount / SimulatedFundingLedger / SimulatedOrder /
   SimulatedPosition / SimulatedSnapshot / SimulatedAISignal 表的 CRUD 操作
2. 不包含业务逻辑，只做数据读写
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.storage import (
    DatabaseManager,
    SimulatedAccount,
    SimulatedFundingLedger,
    SimulatedOrder,
    SimulatedPosition,
    SimulatedSnapshot,
    SimulatedAISignal,
)

logger = logging.getLogger(__name__)


class SimTradeRepo:
    """模拟交易数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # =========================================================
    # Account
    # =========================================================

    def get_or_create_account(self) -> Dict[str, Any]:
        """获取默认模拟账户；不存在则创建。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(SimulatedAccount).order_by(SimulatedAccount.id)
            ).scalar_one_or_none()
            if row is None:
                row = SimulatedAccount()
                session.add(row)
                session.commit()
                session.refresh(row)
            return row.to_dict()

    def get_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(SimulatedAccount, account_id)
            return row.to_dict() if row else None

    def update_account(self, account_id: int, **kwargs) -> Dict[str, Any]:
        """更新账户任意字段。"""
        with self.db.get_session() as session:
            row = session.get(SimulatedAccount, account_id)
            if row is None:
                raise ValueError(f"Account {account_id} not found")
            for k, v in kwargs.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def reset_account(self, account_id: int) -> Dict[str, Any]:
        """重置账户：清空持仓、委托、快照、AI 信号；保留资金流水。"""
        with self.db.get_session() as session:
            for model in [SimulatedPosition, SimulatedOrder, SimulatedSnapshot, SimulatedAISignal]:
                rows = session.execute(
                    select(model).where(model.account_id == account_id)
                ).scalars().all()
                for r in rows:
                    session.delete(r)
            account = session.get(SimulatedAccount, account_id)
            if account:
                account.cash_cny = 0.0
                account.cash_usd = 0.0
                account.total_deposited_cny = 0.0
                account.total_deposited_usd = 0.0
                account.total_withdrawn_cny = 0.0
                account.total_withdrawn_usd = 0.0
                account.auto_trade_enabled = False
                account.status = 'active'
            session.commit()
            session.refresh(account)
            return account.to_dict()

    # =========================================================
    # Funding
    # =========================================================

    def add_funding(
        self,
        account_id: int,
        direction: str,
        amount: float,
        currency: str,
        note: str = "",
    ) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = SimulatedFundingLedger(
                account_id=account_id,
                direction=direction,
                amount=amount,
                currency=currency,
                note=note,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def list_funding(self, account_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedFundingLedger)
                .where(SimulatedFundingLedger.account_id == account_id)
                .order_by(desc(SimulatedFundingLedger.created_at))
                .limit(limit)
            ).scalars().all()
            return [r.to_dict() for r in rows]

    # =========================================================
    # Orders
    # =========================================================

    def create_order(self, account_id: int, **kwargs) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = SimulatedOrder(account_id=account_id, **kwargs)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(SimulatedOrder, order_id)
            return row.to_dict() if row else None

    def update_order(self, order_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(SimulatedOrder, order_id)
            if row is None:
                return None
            for k, v in kwargs.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def list_orders(
        self,
        account_id: int,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            q = select(SimulatedOrder).where(SimulatedOrder.account_id == account_id)
            if status:
                q = q.where(SimulatedOrder.status == status)
            if source:
                q = q.where(SimulatedOrder.source == source)
            q = q.order_by(desc(SimulatedOrder.created_at)).limit(limit)
            rows = session.execute(q).scalars().all()
            return [r.to_dict() for r in rows]

    def list_pending_orders(self, account_id: int) -> List[Dict[str, Any]]:
        return self.list_orders(account_id, status='pending', limit=200)

    # =========================================================
    # Positions
    # =========================================================

    def get_position(self, account_id: int, code: str) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(
                select(SimulatedPosition)
                .where(
                    SimulatedPosition.account_id == account_id,
                    SimulatedPosition.code == code,
                )
            ).scalar_one_or_none()
            return row.to_dict() if row else None

    def list_positions(self, account_id: int) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedPosition)
                .where(
                    SimulatedPosition.account_id == account_id,
                    SimulatedPosition.qty > 0,
                )
                .order_by(desc(SimulatedPosition.total_cost))
            ).scalars().all()
            return [r.to_dict() for r in rows]

    def upsert_position(self, account_id: int, code: str, **kwargs) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(SimulatedPosition)
                .where(
                    SimulatedPosition.account_id == account_id,
                    SimulatedPosition.code == code,
                )
            ).scalar_one_or_none()
            if row is None:
                row = SimulatedPosition(account_id=account_id, code=code, **kwargs)
                session.add(row)
            else:
                for k, v in kwargs.items():
                    if hasattr(row, k):
                        setattr(row, k, v)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    # =========================================================
    # Snapshots
    # =========================================================

    def upsert_snapshot(self, account_id: int, date: str, **kwargs) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(SimulatedSnapshot)
                .where(
                    SimulatedSnapshot.account_id == account_id,
                    SimulatedSnapshot.date == date,
                )
            ).scalar_one_or_none()
            if row is None:
                row = SimulatedSnapshot(account_id=account_id, date=date, **kwargs)
                session.add(row)
            else:
                for k, v in kwargs.items():
                    if hasattr(row, k):
                        setattr(row, k, v)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def list_snapshots(self, account_id: int, limit: int = 90) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedSnapshot)
                .where(SimulatedSnapshot.account_id == account_id)
                .order_by(desc(SimulatedSnapshot.date))
                .limit(limit)
            ).scalars().all()
            return list(reversed([r.to_dict() for r in rows]))

    def get_peak_equity(self, account_id: int) -> float:
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedSnapshot.peak_equity_cny)
                .where(SimulatedSnapshot.account_id == account_id)
                .order_by(desc(SimulatedSnapshot.date))
                .limit(1)
            ).scalars().all()
            return rows[0] if rows else 0.0

    # =========================================================
    # AI Signals
    # =========================================================

    def create_signal(self, account_id: int, **kwargs) -> Dict[str, Any]:
        with self.db.get_session() as session:
            expires_at = datetime.now() + timedelta(hours=4)
            row = SimulatedAISignal(account_id=account_id, expires_at=expires_at, **kwargs)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def update_signal(self, signal_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(SimulatedAISignal, signal_id)
            if row is None:
                return None
            for k, v in kwargs.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            session.commit()
            session.refresh(row)
            return row.to_dict()

    def list_signals(
        self,
        account_id: int,
        limit: int = 30,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            q = select(SimulatedAISignal).where(SimulatedAISignal.account_id == account_id)
            if status:
                q = q.where(SimulatedAISignal.status == status)
            q = q.order_by(desc(SimulatedAISignal.created_at)).limit(limit)
            rows = session.execute(q).scalars().all()
            return [r.to_dict() for r in rows]

    def expire_old_signals(self, account_id: int) -> int:
        """将超时且未执行的 pending 信号标记为 expired。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedAISignal)
                .where(
                    SimulatedAISignal.account_id == account_id,
                    SimulatedAISignal.status == 'pending',
                    SimulatedAISignal.expires_at < datetime.now(),
                )
            ).scalars().all()
            for r in rows:
                r.status = 'expired'
            session.commit()
            return len(rows)
