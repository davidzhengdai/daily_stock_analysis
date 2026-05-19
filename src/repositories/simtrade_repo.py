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
    SimulatedAutoTradeRun,
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
        def _create_account(session) -> SimulatedAccount:
            row = SimulatedAccount()
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedAccount).order_by(SimulatedAccount.id).limit(2)
            ).scalars().all()
            if rows:
                row = rows[0]
                if len(rows) > 1:
                    logger.warning(
                        "检测到多个模拟交易账户，默认使用最早账户 id=%s",
                        row.id,
                    )
                return row.to_dict()

        def _ensure_account(session) -> Dict[str, Any]:
            row = session.execute(
                select(SimulatedAccount).order_by(SimulatedAccount.id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = _create_account(session)
            return row.to_dict()

        return self.db._run_write_transaction(
            "simtrade_get_or_create_account",
            _ensure_account,
        )

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

    def list_trade_history(self, account_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Return round-trip trade history with account and AI signal context."""
        account = self.get_account(account_id) or {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedOrder, SimulatedAISignal)
                .outerjoin(
                    SimulatedAISignal,
                    SimulatedOrder.ai_signal_id == SimulatedAISignal.id,
                )
                .where(SimulatedOrder.account_id == account_id)
                .order_by(SimulatedOrder.created_at, SimulatedOrder.id)
            ).all()

        raw_items: List[Dict[str, Any]] = []
        cost_basis: Dict[str, Dict[str, float]] = {}
        for order, signal in rows:
            item = order.to_dict()
            code = item["code"]
            filled_qty = item.get("fill_qty") or 0
            fill_price = item.get("fill_price")
            realized_pnl = item.get("realized_pnl")
            basis = cost_basis.setdefault(code, {"qty": 0.0, "avg_cost": 0.0})

            if item.get("status") == "filled" and filled_qty > 0 and fill_price is not None:
                if item["side"] == "buy":
                    old_qty = basis["qty"]
                    new_qty = old_qty + filled_qty
                    basis["avg_cost"] = (
                        ((basis["avg_cost"] * old_qty) + (fill_price * filled_qty)) / new_qty
                    ) if new_qty > 0 else 0.0
                    basis["qty"] = new_qty
                elif item["side"] == "sell":
                    avg_cost_before_sell = basis["avg_cost"]
                    if realized_pnl is None and basis["avg_cost"] > 0:
                        realized_pnl = round((fill_price - basis["avg_cost"]) * filled_qty, 2)
                        item["realized_pnl"] = realized_pnl
                    if (
                        item.get("source") == "auto"
                        and not item.get("ai_signal_id")
                        and not item.get("rejection_reason")
                        and avg_cost_before_sell > 0
                    ):
                        if fill_price < avg_cost_before_sell:
                            item["rejection_reason"] = "自动风控卖出（历史记录未保存具体原因；按成本回放推断为止损）"
                        else:
                            item["rejection_reason"] = "自动风控卖出（历史记录未保存具体原因；可能为止盈或收盘清仓）"
                    basis["qty"] = max(0.0, basis["qty"] - filled_qty)
                    if basis["qty"] == 0:
                        basis["avg_cost"] = 0.0

            item["account_name"] = account.get("name")
            item["ai_reasoning"] = signal.reasoning if signal else None
            item["ai_confidence"] = signal.confidence if signal else None
            raw_items.append(item)
        return self._build_trade_round_trips(raw_items, limit)

    def _build_trade_round_trips(self, orders: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        lots_by_code: Dict[str, List[Dict[str, Any]]] = {}
        trades: List[Dict[str, Any]] = []

        for order in orders:
            if order.get("status") != "filled":
                continue
            qty = int(order.get("fill_qty") or 0)
            price = order.get("fill_price")
            if qty <= 0 or price is None:
                continue

            code = order["code"]
            lots = lots_by_code.setdefault(code, [])
            if order["side"] == "buy":
                merge_target = lots[-1] if lots else None
                if (
                    merge_target
                    and merge_target["source"] == order["source"]
                    and merge_target["buy_price"] == price
                    and not merge_target.get("sell_price")
                ):
                    total_qty = merge_target["qty"] + qty
                    merge_target["buy_price"] = (
                        (merge_target["buy_price"] * merge_target["qty"]) + (price * qty)
                    ) / total_qty
                    merge_target["qty"] = total_qty
                    merge_target["order_ids"].append(order["id"])
                    if not merge_target.get("ai_reasoning"):
                        merge_target["ai_reasoning"] = order.get("ai_reasoning")
                else:
                    lots.append({
                        "id": f"open-{order['id']}",
                        "account_id": order["account_id"],
                        "account_name": order.get("account_name"),
                        "code": code,
                        "name": order.get("name"),
                        "market": order["market"],
                        "currency": order["currency"],
                        "status": "open",
                        "source": order["source"],
                        "qty": qty,
                        "buy_price": price,
                        "sell_price": None,
                        "realized_pnl": None,
                        "opened_at": order.get("filled_at") or order.get("created_at"),
                        "closed_at": None,
                        "ai_reasoning": order.get("ai_reasoning"),
                        "sell_reason": None,
                        "order_ids": [order["id"]],
                    })
                continue

            remaining = qty
            weighted_buy_value = 0.0
            matched_qty = 0
            matched_lots: List[Dict[str, Any]] = []
            while remaining > 0 and lots:
                lot = lots[0]
                use_qty = min(remaining, lot["qty"])
                weighted_buy_value += lot["buy_price"] * use_qty
                matched_qty += use_qty
                remaining -= use_qty
                lot["qty"] -= use_qty
                matched_lots.append(lot)
                if lot["qty"] == 0:
                    lots.pop(0)

            if matched_qty <= 0:
                continue

            avg_buy_price = weighted_buy_value / matched_qty
            sell_reason = order.get("ai_reasoning") or order.get("rejection_reason")
            buy_reason = next((lot.get("ai_reasoning") for lot in matched_lots if lot.get("ai_reasoning")), None)
            trade = {
                "id": f"trade-{order['id']}",
                "account_id": order["account_id"],
                "account_name": order.get("account_name"),
                "code": code,
                "name": order.get("name"),
                "market": order["market"],
                "currency": order["currency"],
                "status": "closed",
                "source": "auto" if order["source"] == "auto" or any(lot["source"] == "auto" for lot in matched_lots) else "manual",
                "qty": matched_qty,
                "buy_price": round(avg_buy_price, 4),
                "sell_price": price,
                "realized_pnl": order.get("realized_pnl") if order.get("realized_pnl") is not None else round((price - avg_buy_price) * matched_qty, 2),
                "opened_at": matched_lots[0].get("opened_at"),
                "closed_at": order.get("filled_at") or order.get("created_at"),
                "ai_reasoning": buy_reason,
                "sell_reason": sell_reason,
                "order_ids": [oid for lot in matched_lots for oid in lot["order_ids"]] + [order["id"]],
            }

            previous = trades[-1] if trades else None
            if (
                previous
                and previous["status"] == "closed"
                and previous["code"] == trade["code"]
                and previous["source"] == trade["source"]
                and previous["buy_price"] == trade["buy_price"]
                and previous["sell_price"] == trade["sell_price"]
                and previous.get("sell_reason") == trade.get("sell_reason")
            ):
                total_qty = previous["qty"] + trade["qty"]
                previous["realized_pnl"] = round((previous.get("realized_pnl") or 0) + (trade.get("realized_pnl") or 0), 2)
                previous["qty"] = total_qty
                previous["closed_at"] = trade["closed_at"]
                previous["order_ids"].extend(trade["order_ids"])
            else:
                trades.append(trade)

        for lots in lots_by_code.values():
            trades.extend(lots)

        return list(reversed(trades))[:limit]

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

    # =========================================================
    # Auto-trade run history
    # =========================================================

    def create_auto_trade_run(self, account_id: int, **kwargs) -> Dict[str, Any]:
        """持久化一次自动交易周期的执行结果。"""
        import json as _json

        def _write(session) -> Dict[str, Any]:
            stop_loss = kwargs.pop('stop_loss_triggered', [])
            errors = kwargs.pop('errors', [])
            row = SimulatedAutoTradeRun(
                account_id=account_id,
                stop_loss_triggered=_json.dumps(stop_loss) if stop_loss else None,
                errors=_json.dumps(errors) if errors else None,
                **kwargs,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row.to_dict()

        return self.db._run_write_transaction('simtrade_create_run', _write)

    def list_auto_trade_runs(self, account_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """按时间倒序返回历史运行记录。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(SimulatedAutoTradeRun)
                .where(SimulatedAutoTradeRun.account_id == account_id)
                .order_by(desc(SimulatedAutoTradeRun.started_at))
                .limit(limit)
            ).scalars().all()
            return [r.to_dict() for r in rows]
