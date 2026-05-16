# -*- coding: utf-8 -*-
"""
===================================
模拟账户与资金管理服务
===================================

职责：
1. 获取/创建默认模拟账户
2. 入金 / 出金（CNY 或 USD）
3. 更新自动交易策略设置
4. 重置账户
5. 计算账户总权益（CNY 基准）
"""

import logging
from typing import Any, Dict, List, Optional

from src.repositories.simtrade_repo import SimTradeRepo

logger = logging.getLogger(__name__)

# 最低出金余额保护：出金后账户余额不得为负
_MIN_RESIDUAL = 0.0


class AccountService:
    """模拟账户与资金管理服务。"""

    def __init__(self, repo: Optional[SimTradeRepo] = None):
        self.repo = repo or SimTradeRepo()

    # -------------------------------------------------------
    # 账户获取
    # -------------------------------------------------------

    def get_account(self) -> Dict[str, Any]:
        """获取（或创建）默认模拟账户。"""
        return self.repo.get_or_create_account()

    def get_account_with_equity(self, fx_rate: float = 7.25) -> Dict[str, Any]:
        """返回账户信息并附加实时权益计算结果。"""
        acct = self.get_account()
        positions = self.repo.list_positions(acct['id'])

        market_value_cny = sum(
            (p['last_price'] * p['qty']) if p['currency'] == 'CNY'
            else (p['last_price'] * p['qty'] * fx_rate)
            for p in positions
        )
        unrealized_pnl = sum(p['unrealized_pnl'] for p in positions)
        realized_pnl = sum(p['realized_pnl'] for p in positions)
        cash_equiv_cny = acct['cash_cny'] + acct['cash_usd'] * fx_rate
        total_equity_cny = cash_equiv_cny + market_value_cny

        total_deposited_cny = (
            acct['total_deposited_cny'] + acct['total_deposited_usd'] * fx_rate
        )
        total_withdrawn_cny = (
            acct['total_withdrawn_cny'] + acct['total_withdrawn_usd'] * fx_rate
        )
        net_deposited = total_deposited_cny - total_withdrawn_cny
        total_return_pct = (
            (total_equity_cny - net_deposited) / net_deposited * 100
            if net_deposited > 0
            else 0.0
        )

        return {
            **acct,
            'equity_summary': {
                'cash_equiv_cny': round(cash_equiv_cny, 2),
                'market_value_cny': round(market_value_cny, 2),
                'total_equity_cny': round(total_equity_cny, 2),
                'unrealized_pnl': round(unrealized_pnl, 2),
                'realized_pnl': round(realized_pnl, 2),
                'total_return_pct': round(total_return_pct, 2),
                'fx_rate_usd_cny': fx_rate,
                'positions_count': len(positions),
            },
        }

    # -------------------------------------------------------
    # 资金操作
    # -------------------------------------------------------

    def deposit(
        self,
        amount: float,
        currency: str,
        note: str = "",
    ) -> Dict[str, Any]:
        """入金。currency 为 CNY 或 USD。"""
        if amount <= 0:
            raise ValueError("入金金额必须大于 0")
        currency = currency.upper()
        if currency not in ('CNY', 'USD'):
            raise ValueError("货币只支持 CNY 或 USD")

        acct = self.get_account()
        account_id = acct['id']

        cash_field = 'cash_cny' if currency == 'CNY' else 'cash_usd'
        deposited_field = 'total_deposited_cny' if currency == 'CNY' else 'total_deposited_usd'

        new_cash = acct[cash_field] + amount
        new_deposited = acct[deposited_field] + amount

        self.repo.update_account(account_id, **{
            cash_field: new_cash,
            deposited_field: new_deposited,
        })
        ledger = self.repo.add_funding(account_id, 'deposit', amount, currency, note)
        logger.info("入金 %.2f %s → 账户 %d", amount, currency, account_id)
        return ledger

    def withdraw(
        self,
        amount: float,
        currency: str,
        note: str = "",
    ) -> Dict[str, Any]:
        """出金。不得超过当前可用余额。"""
        if amount <= 0:
            raise ValueError("出金金额必须大于 0")
        currency = currency.upper()
        if currency not in ('CNY', 'USD'):
            raise ValueError("货币只支持 CNY 或 USD")

        acct = self.get_account()
        account_id = acct['id']
        cash_field = 'cash_cny' if currency == 'CNY' else 'cash_usd'
        withdrawn_field = 'total_withdrawn_cny' if currency == 'CNY' else 'total_withdrawn_usd'

        available = acct[cash_field]
        if amount > available - _MIN_RESIDUAL:
            raise ValueError(
                f"可用余额不足：当前 {currency} 余额 {available:.2f}，申请出金 {amount:.2f}"
            )

        new_cash = available - amount
        new_withdrawn = acct[withdrawn_field] + amount

        self.repo.update_account(account_id, **{
            cash_field: new_cash,
            withdrawn_field: new_withdrawn,
        })
        ledger = self.repo.add_funding(account_id, 'withdrawal', amount, currency, note)
        logger.info("出金 %.2f %s ← 账户 %d", amount, currency, account_id)
        return ledger

    def list_funding_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        acct = self.get_account()
        return self.repo.list_funding(acct['id'], limit=limit)

    # -------------------------------------------------------
    # 设置更新
    # -------------------------------------------------------

    def update_settings(
        self,
        auto_trade_mode: Optional[str] = None,
        max_position_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        min_signal_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """更新自动交易策略参数。"""
        acct = self.get_account()
        updates: Dict[str, Any] = {}

        if auto_trade_mode is not None:
            if auto_trade_mode not in ('conservative', 'balanced', 'aggressive'):
                raise ValueError("auto_trade_mode 须为 conservative / balanced / aggressive")
            updates['auto_trade_mode'] = auto_trade_mode

        if max_position_pct is not None:
            updates['max_position_pct'] = max(1.0, min(100.0, max_position_pct))
        if max_drawdown_pct is not None:
            updates['max_drawdown_pct'] = max(1.0, min(100.0, max_drawdown_pct))
        if stop_loss_pct is not None:
            updates['stop_loss_pct'] = max(0.5, min(50.0, stop_loss_pct))
        if take_profit_pct is not None:
            updates['take_profit_pct'] = max(1.0, min(200.0, take_profit_pct))
        if min_signal_confidence is not None:
            updates['min_signal_confidence'] = max(0.0, min(1.0, min_signal_confidence))

        if not updates:
            return acct
        return self.repo.update_account(acct['id'], **updates)

    def set_auto_trade_enabled(self, enabled: bool) -> Dict[str, Any]:
        """开启或关闭自动交易。开启前校验自选股非空。"""
        if enabled:
            from src.services.watchlist_service import WatchlistService
            watchlist = WatchlistService().list_all()
            if not watchlist:
                raise ValueError("自选股列表为空，请先添加自选股后再开启自动交易")
        acct = self.get_account()
        return self.repo.update_account(acct['id'], auto_trade_enabled=enabled)

    # -------------------------------------------------------
    # 重置
    # -------------------------------------------------------

    def reset_account(self) -> Dict[str, Any]:
        """清空持仓、委托、快照和 AI 信号；重置资金为 0；保留资金流水。"""
        acct = self.get_account()
        result = self.repo.reset_account(acct['id'])
        logger.info("模拟账户 %d 已重置", acct['id'])
        return result
