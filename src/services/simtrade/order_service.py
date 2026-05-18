# -*- coding: utf-8 -*-
"""
===================================
模拟交易委托与持仓管理服务
===================================

职责：
1. 手动 / 自动下单（买入 / 卖出）
2. 委托撤单
3. 模拟成交（限价单即时撮合、市价单立即成交）
4. 持仓更新（均价、浮动盈亏、止损/止盈触发）
5. 每日资产快照
"""

import logging
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from src.repositories.simtrade_repo import SimTradeRepo

logger = logging.getLogger(__name__)

# -------------------------------------------------------
# 佣金计算
# -------------------------------------------------------

def _calc_commission(market: str, side: str, qty: int, price: float) -> float:
    """模拟佣金：A 股双边 0.025% + 卖出印花税 0.1%；美股 max($1, $0.005×股数)。"""
    if market == 'CN':
        commission = max(5.0, qty * price * 0.00025)
        stamp_duty = (qty * price * 0.001) if side == 'sell' else 0.0
        return round(commission + stamp_duty, 2)
    else:
        return round(max(1.0, qty * 0.005), 2)


def _fill_price(current_price: float, side: str, order_type: str, limit_price: Optional[float]) -> Optional[float]:
    """
    确定成交价：
    - 市价单：当前价 ± 0.1% 滑点
    - 限价单：若限价在当前价 ±3% 内则即时成交；否则挂单等待
    """
    if order_type == 'market':
        slippage = 0.001 if side == 'buy' else -0.001
        return round(current_price * (1 + slippage), 3)
    # limit order
    if limit_price is None:
        return None
    spread = abs(limit_price - current_price) / current_price if current_price > 0 else 1
    if spread <= 0.03:
        return limit_price
    return None  # 挂单等待


class OrderService:
    """委托与持仓管理服务。"""

    def __init__(self, repo: Optional[SimTradeRepo] = None, fx_rate: Optional[float] = None):
        self.repo = repo or SimTradeRepo()
        self._fx_rate = fx_rate or float(os.getenv('SIMTRADE_USD_CNY_RATE', '7.25'))
        self._fetcher_manager = None

    # -------------------------------------------------------
    # 下单
    # -------------------------------------------------------

    def place_order(
        self,
        code: str,
        market: str,
        side: str,
        order_type: str,
        qty: int,
        limit_price: Optional[float] = None,
        name: str = "",
        source: str = 'manual',
        ai_signal_id: Optional[int] = None,
        current_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        提交一笔模拟委托。

        - market: CN / US
        - side: buy / sell
        - order_type: limit / market
        - qty: 股数（A 股最小 100 股）
        """
        market = market.upper()
        side = side.lower()
        order_type = order_type.lower()
        currency = 'CNY' if market == 'CN' else 'USD'

        if market not in ('CN', 'US'):
            raise ValueError("market 须为 CN 或 US")
        if side not in ('buy', 'sell'):
            raise ValueError("side 须为 buy 或 sell")
        if order_type not in ('limit', 'market'):
            raise ValueError("order_type 须为 limit 或 market")
        if qty <= 0:
            raise ValueError("qty 必须大于 0")
        if market == 'CN' and qty % 100 != 0:
            raise ValueError("A 股买卖单位为 100 股（1 手）")
        if order_type == 'limit' and (limit_price is None or limit_price <= 0):
            raise ValueError("限价单须提供有效的 limit_price")

        acct = self.repo.get_or_create_account()
        account_id = acct['id']

        # 自动交易开启时禁止手动下单
        if source == 'manual' and acct['auto_trade_enabled']:
            raise ValueError("自动交易已开启，手动下单已禁用；请先关闭自动交易")

        # 获取当前价格（如未提供）
        if current_price is None or current_price <= 0:
            current_price = self._get_latest_price(code)
        if current_price is None or current_price <= 0:
            raise ValueError(f"无法获取 {code} 的当前价格，请稍后重试")

        # 计算成交价
        fp = _fill_price(current_price, side, order_type, limit_price)
        commission = 0.0
        status = 'pending'

        # 卖出：校验持仓
        if side == 'sell':
            pos = self.repo.get_position(account_id, code)
            if pos is None or pos['qty'] < qty:
                raise ValueError(
                    f"持仓不足：当前持有 {pos['qty'] if pos else 0} 股，申请卖出 {qty} 股"
                )

        # 买入：校验可用资金
        if side == 'buy' and fp is not None:
            cost = fp * qty
            commission = _calc_commission(market, side, qty, fp)
            total_needed = cost + commission
            available = acct['cash_cny'] if currency == 'CNY' else acct['cash_usd']
            if total_needed > available:
                raise ValueError(
                    f"可用资金不足：需要 {total_needed:.2f} {currency}，"
                    f"账户余额 {available:.2f} {currency}"
                )

        # 创建委托记录
        order = self.repo.create_order(
            account_id=account_id,
            code=code.upper(),
            name=name,
            market=market,
            currency=currency,
            side=side,
            order_type=order_type,
            qty=qty,
            limit_price=limit_price,
            fill_price=fp,
            fill_qty=qty if fp is not None else 0,
            commission=commission if fp is not None else 0.0,
            status='pending',
            source=source,
            ai_signal_id=ai_signal_id,
        )

        # 若可以即时成交则立刻执行
        if fp is not None:
            order = self._execute_fill(account_id, order['id'], fp, qty, commission, currency, side, acct)

        return order

    def _execute_fill(
        self,
        account_id: int,
        order_id: int,
        fill_price: float,
        fill_qty: int,
        commission: float,
        currency: str,
        side: str,
        acct: Dict[str, Any],
    ) -> Dict[str, Any]:
        """完成一笔成交：更新委托状态、调整现金、更新持仓。"""
        # 更新委托
        order = self.repo.update_order(
            order_id,
            status='filled',
            fill_price=fill_price,
            fill_qty=fill_qty,
            commission=commission,
            filled_at=datetime.now(),
        )

        # 调整现金
        cash_field = 'cash_cny' if currency == 'CNY' else 'cash_usd'
        current_cash = acct[cash_field]
        trade_value = fill_price * fill_qty
        if side == 'buy':
            new_cash = current_cash - trade_value - commission
        else:
            new_cash = current_cash + trade_value - commission
        self.repo.update_account(account_id, **{cash_field: max(0.0, new_cash)})

        # 更新持仓
        self._update_position(account_id, order, fill_price, fill_qty)
        return order

    def _update_position(
        self,
        account_id: int,
        order: Dict[str, Any],
        fill_price: float,
        fill_qty: int,
    ) -> None:
        code = order['code']
        side = order['side']
        market = order['market']
        currency = order['currency']
        name = order.get('name', '')

        acct_info = self.repo.get_account(account_id)
        pos = self.repo.get_position(account_id, code)
        stop_loss_pct = acct_info['stop_loss_pct'] if acct_info else 5.0
        take_profit_pct = acct_info['take_profit_pct'] if acct_info else 15.0

        if side == 'buy':
            old_qty = pos['qty'] if pos else 0
            old_total_cost = pos['total_cost'] if pos else 0.0
            new_qty = old_qty + fill_qty
            new_total_cost = old_total_cost + fill_price * fill_qty
            new_avg_cost = new_total_cost / new_qty if new_qty > 0 else 0.0
            stop_loss_price = round(new_avg_cost * (1 - stop_loss_pct / 100), 3)
            take_profit_price = round(new_avg_cost * (1 + take_profit_pct / 100), 3)
            self.repo.upsert_position(
                account_id, code,
                name=name, market=market, currency=currency,
                qty=new_qty, avg_cost=round(new_avg_cost, 4),
                total_cost=round(new_total_cost, 2),
                last_price=fill_price,
                unrealized_pnl=round((fill_price - new_avg_cost) * new_qty, 2),
                unrealized_pnl_pct=round((fill_price / new_avg_cost - 1) * 100, 2) if new_avg_cost > 0 else 0.0,
                realized_pnl=pos['realized_pnl'] if pos else 0.0,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
            )
        elif side == 'sell':
            if pos is None:
                return
            old_qty = pos['qty']
            new_qty = old_qty - fill_qty
            realized = (fill_price - pos['avg_cost']) * fill_qty
            new_realized = pos['realized_pnl'] + realized
            if new_qty <= 0:
                self.repo.upsert_position(
                    account_id, code,
                    name=name, market=market, currency=currency,
                    qty=0, avg_cost=0.0, total_cost=0.0,
                    last_price=fill_price, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
                    realized_pnl=round(new_realized, 2),
                    stop_loss_price=None, take_profit_price=None,
                )
            else:
                new_total_cost = pos['avg_cost'] * new_qty
                unrealized = (fill_price - pos['avg_cost']) * new_qty
                self.repo.upsert_position(
                    account_id, code,
                    name=name, market=market, currency=currency,
                    qty=new_qty, avg_cost=pos['avg_cost'],
                    total_cost=round(new_total_cost, 2),
                    last_price=fill_price,
                    unrealized_pnl=round(unrealized, 2),
                    unrealized_pnl_pct=round((fill_price / pos['avg_cost'] - 1) * 100, 2) if pos['avg_cost'] > 0 else 0.0,
                    realized_pnl=round(new_realized, 2),
                    stop_loss_price=pos['stop_loss_price'],
                    take_profit_price=pos['take_profit_price'],
                )

    # -------------------------------------------------------
    # 撤单
    # -------------------------------------------------------

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        order = self.repo.get_order(order_id)
        if order is None:
            raise ValueError(f"委托 {order_id} 不存在")
        if order['status'] != 'pending':
            raise ValueError(f"委托 {order_id} 状态为 {order['status']}，无法撤单")
        return self.repo.update_order(order_id, status='cancelled')

    # -------------------------------------------------------
    # 挂单重新撮合（自动交易周期调用）
    # -------------------------------------------------------

    def try_fill_pending_orders(self, account_id: int) -> int:
        """对所有挂单尝试撮合成交，返回成交数量。"""
        pending = self.repo.list_pending_orders(account_id)
        filled_count = 0
        for order in pending:
            code = order['code']
            current_price = self._get_latest_price(code)
            if current_price is None or current_price <= 0:
                continue
            fp = _fill_price(current_price, order['side'], order['order_type'], order['limit_price'])
            if fp is None:
                continue
            qty = order['qty'] - (order['fill_qty'] or 0)
            if qty <= 0:
                continue
            commission = _calc_commission(order['market'], order['side'], qty, fp)
            acct = self.repo.get_account(account_id)
            self._execute_fill(account_id, order['id'], fp, qty, commission, order['currency'], order['side'], acct)
            filled_count += 1
        return filled_count

    # -------------------------------------------------------
    # 止损 / 止盈
    # -------------------------------------------------------

    def check_stop_loss_take_profit(self, account_id: int) -> List[str]:
        """检查所有持仓的止损/止盈触发，自动生成卖单。返回触发的股票代码列表。"""
        triggered = []
        positions = self.repo.list_positions(account_id)
        for pos in positions:
            if pos['qty'] <= 0:
                continue
            current_price = self._get_latest_price(pos['code'])
            if current_price is None or current_price <= 0:
                continue
            reason = None
            if pos['stop_loss_price'] and current_price <= pos['stop_loss_price']:
                reason = f"止损触发 (价格 {current_price:.3f} ≤ 止损线 {pos['stop_loss_price']:.3f})"
            elif pos['take_profit_price'] and current_price >= pos['take_profit_price']:
                reason = f"止盈触发 (价格 {current_price:.3f} ≥ 止盈线 {pos['take_profit_price']:.3f})"
            if reason:
                try:
                    acct = self.repo.get_account(account_id)
                    self.place_order(
                        code=pos['code'],
                        market=pos['market'],
                        side='sell',
                        order_type='market',
                        qty=pos['qty'],
                        name=pos.get('name', ''),
                        source='auto',
                        current_price=current_price,
                    )
                    logger.info("[SimTrade] %s %s", pos['code'], reason)
                    triggered.append(pos['code'])
                except Exception as exc:
                    logger.warning("[SimTrade] 止损/止盈下单失败 %s: %s", pos['code'], exc)
        return triggered

    def liquidate_all_positions(self, account_id: int) -> List[str]:
        """以市价卖出所有持仓，返回已提交清仓委托的股票代码列表。"""
        liquidated = []
        positions = self.repo.list_positions(account_id)
        for pos in positions:
            if pos['qty'] <= 0:
                continue
            current_price = self._get_latest_price(pos['code'])
            try:
                self.place_order(
                    code=pos['code'],
                    market=pos['market'],
                    side='sell',
                    order_type='market',
                    qty=pos['qty'],
                    name=pos.get('name', ''),
                    source='auto',
                    current_price=current_price,
                )
                liquidated.append(pos['code'])
                logger.info("[SimTrade] 空仓过夜清仓: %s × %d", pos['code'], pos['qty'])
            except Exception as exc:
                logger.warning("[SimTrade] 空仓过夜清仓失败 %s: %s", pos['code'], exc)
        return liquidated

    # -------------------------------------------------------
    # 持仓刷新
    # -------------------------------------------------------

    def refresh_position_prices(self, account_id: int) -> int:
        """刷新所有持仓的最新价格与浮动盈亏，返回刷新数量。"""
        positions = self.repo.list_positions(account_id)
        updated = 0
        for pos in positions:
            if pos['qty'] <= 0:
                continue
            current_price = self._get_latest_price(pos['code'])
            if current_price is None or current_price <= 0:
                continue
            avg_cost = pos['avg_cost']
            unrealized_pnl = (current_price - avg_cost) * pos['qty']
            unrealized_pnl_pct = (current_price / avg_cost - 1) * 100 if avg_cost > 0 else 0.0
            self.repo.upsert_position(
                account_id, pos['code'],
                last_price=current_price,
                unrealized_pnl=round(unrealized_pnl, 2),
                unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
            )
            updated += 1
        return updated

    # -------------------------------------------------------
    # 日快照
    # -------------------------------------------------------

    def take_daily_snapshot(self, account_id: int) -> Dict[str, Any]:
        """生成当日资产快照（幂等：同一天重复调用会覆盖）。"""
        today_str = date.today().isoformat()
        acct = self.repo.get_account(account_id)
        positions = self.repo.list_positions(account_id)
        fx_rate = self._fx_rate

        market_value_cny = sum(
            (p['last_price'] * p['qty']) if p['currency'] == 'CNY'
            else (p['last_price'] * p['qty'] * fx_rate)
            for p in positions
        )
        cash_equiv_cny = acct['cash_cny'] + acct['cash_usd'] * fx_rate
        total_equity = cash_equiv_cny + market_value_cny

        realized_pnl = sum(p['realized_pnl'] for p in positions)
        unrealized_pnl = sum(p['unrealized_pnl'] for p in positions)

        # 计算最大回撤
        peak_equity = max(self.repo.get_peak_equity(account_id), total_equity)
        max_drawdown_pct = (
            (peak_equity - total_equity) / peak_equity * 100
            if peak_equity > 0
            else 0.0
        )

        net_deposited = (
            acct['total_deposited_cny'] + acct['total_deposited_usd'] * fx_rate
            - acct['total_withdrawn_cny'] - acct['total_withdrawn_usd'] * fx_rate
        )
        total_return_pct = (
            (total_equity - net_deposited) / net_deposited * 100
            if net_deposited > 0
            else 0.0
        )

        return self.repo.upsert_snapshot(
            account_id, today_str,
            cash_cny=round(acct['cash_cny'], 2),
            cash_usd=round(acct['cash_usd'], 2),
            fx_rate_usd_cny=fx_rate,
            market_value_cny=round(market_value_cny, 2),
            total_equity_cny=round(total_equity, 2),
            realized_pnl=round(realized_pnl, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            total_return_pct=round(total_return_pct, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            peak_equity_cny=round(peak_equity, 2),
        )

    # -------------------------------------------------------
    # 内部：价格获取
    # -------------------------------------------------------

    def _get_latest_price(self, code: str) -> Optional[float]:
        """优先获取实时价，失败时回落到最近日线收盘价。"""
        try:
            if self._fetcher_manager is None:
                from data_provider.base import DataFetcherManager
                self._fetcher_manager = DataFetcherManager()
            quote = self._fetcher_manager.get_realtime_quote(code, log_final_failure=False)
            price = getattr(quote, 'price', None) if quote is not None else None
            if price is not None and float(price) > 0:
                return float(price)
        except Exception as exc:
            logger.debug("获取 %s 实时价格失败，回落到日线收盘价: %s", code, exc)

        try:
            from src.storage import DatabaseManager, StockDaily
            from sqlalchemy import select, desc
            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                row = session.execute(
                    select(StockDaily.close)
                    .where(StockDaily.code == code)
                    .order_by(desc(StockDaily.date))
                    .limit(1)
                ).scalar_one_or_none()
                return float(row) if row else None
        except Exception as exc:
            logger.debug("获取 %s 价格失败: %s", code, exc)
            return None

    # -------------------------------------------------------
    # 公开查询
    # -------------------------------------------------------

    def list_positions(self, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if account_id is None:
            acct = self.repo.get_or_create_account()
            account_id = acct['id']
        return self.repo.list_positions(account_id)

    def list_orders(
        self,
        account_id: Optional[int] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if account_id is None:
            acct = self.repo.get_or_create_account()
            account_id = acct['id']
        return self.repo.list_orders(account_id, status=status, source=source, limit=limit)
