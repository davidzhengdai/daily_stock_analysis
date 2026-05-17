# -*- coding: utf-8 -*-
"""
===================================
AI 自动交易调度服务
===================================

职责：
1. 后台线程周期触发自动交易循环
2. 每轮：为自选股生成信号 → 执行信号 → 止损/止盈检查 → 日快照
3. 最大回撤保护：超阈值自动暂停
4. 支持手动触发单次循环（用于 API 即时调用）
"""

import logging
import os
import threading
import time
import uuid
from datetime import datetime, date, time as dtime
from typing import Any, Dict, List, Optional

from src.repositories.simtrade_repo import SimTradeRepo
from src.services.simtrade.signal_service import SignalService
from src.services.simtrade.order_service import OrderService

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_MINUTES = 5


# -------------------------------------------------------
# Market hours helpers
# -------------------------------------------------------

def _is_cn_market_open() -> bool:
    """A-share market: 09:30-11:30 and 13:00-15:00 CST, Mon-Fri."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.weekday() >= 5:
        return False
    t = now.time().replace(second=0, microsecond=0)
    return dtime(9, 30) <= t < dtime(11, 30) or dtime(13, 0) <= t < dtime(15, 0)


def _is_us_market_open() -> bool:
    """US regular session: 09:30-16:00 ET, Mon-Fri."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    now = datetime.now(ZoneInfo('America/New_York'))
    if now.weekday() >= 5:
        return False
    t = now.time().replace(second=0, microsecond=0)
    return dtime(9, 30) <= t < dtime(16, 0)


def get_market_status() -> Dict[str, bool]:
    return {'cn_open': _is_cn_market_open(), 'us_open': _is_us_market_open()}


class AutoTradeService:
    """AI 自动交易调度服务（单例）。"""

    _instance: Optional['AutoTradeService'] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self):
        self.repo = SimTradeRepo()
        self.signal_svc = SignalService(repo=self.repo)
        self.order_svc = OrderService(repo=self.repo)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._interval_seconds = int(
            os.getenv('SIMTRADE_AUTO_TRADE_INTERVAL_MINUTES', str(_DEFAULT_INTERVAL_MINUTES))
        ) * 60
        self._last_run_result: Dict[str, Any] = {}
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._watcher_thread: Optional[threading.Thread] = None
        self._watcher_stop: threading.Event = threading.Event()
        self._market_hours_only: bool = os.getenv(
            'SIMTRADE_MARKET_HOURS_ONLY', 'true'
        ).lower() not in ('false', '0', 'no')

    @classmethod
    def get_instance(cls) -> 'AutoTradeService':
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # -------------------------------------------------------
    # 调度器管理
    # -------------------------------------------------------

    def start(self) -> None:
        """启动后台调度线程（幂等）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name='simtrade-auto-trader',
            daemon=True,
        )
        self._thread.start()
        logger.info("[AutoTrade] 调度线程已启动，间隔 %d 分钟", self._interval_seconds // 60)

    def stop(self) -> None:
        """停止后台调度线程。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[AutoTrade] 调度线程已停止")

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                acct = self.repo.get_or_create_account()
                if acct.get('auto_trade_enabled') and acct.get('status') == 'active':
                    self._run_cycle(acct)
            except Exception as exc:
                logger.error("[AutoTrade] 调度循环异常: %s", exc, exc_info=True)
            self._stop_event.wait(timeout=self._interval_seconds)

    # -------------------------------------------------------
    # 单次周期
    # -------------------------------------------------------

    def run_once(self) -> Dict[str, Any]:
        """手动触发一次自动交易周期（同步，保留供内部调用）。"""
        acct = self.repo.get_or_create_account()
        return self._run_cycle(acct)

    def run_once_async(self) -> str:
        """在后台线程触发一次周期，立即返回 job_id 供轮询。"""
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {'status': 'running', 'started_at': datetime.now().isoformat()}
        if len(self._jobs) > 20:
            oldest = next(iter(self._jobs))
            del self._jobs[oldest]

        def _worker() -> None:
            try:
                acct = self.repo.get_or_create_account()
                result = self._run_cycle(acct)
                self._jobs[job_id] = {'status': 'done', **result}
            except Exception as exc:
                self._jobs[job_id] = {
                    'status': 'error',
                    'error': str(exc),
                    'started_at': self._jobs[job_id].get('started_at'),
                }

        threading.Thread(target=_worker, name=f'simtrade-run-{job_id[:8]}', daemon=True).start()
        return job_id

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._jobs.get(job_id)

    def _run_cycle(self, acct: Dict[str, Any]) -> Dict[str, Any]:
        account_id = acct['id']
        started_at = datetime.now().isoformat()
        result: Dict[str, Any] = {
            'started_at': started_at,
            'account_id': account_id,
            'signals_generated': 0,
            'orders_placed': 0,
            'stop_loss_triggered': [],
            'errors': [],
            'skipped_reason': None,
        }

        # ---- 前置条件检查 ----
        from src.services.watchlist_service import WatchlistService
        watchlist = WatchlistService().list_all()
        if not watchlist:
            result['skipped_reason'] = '自选股列表为空'
            logger.info("[AutoTrade] 跳过：自选股列表为空")
            self._last_run_result = result
            return result

        # ---- 市场交易时段检查 ----
        if self._market_hours_only:
            markets = {SignalService._infer_market(item['code']) for item in watchlist}
            cn_open = _is_cn_market_open()
            us_open = _is_us_market_open()
            any_open = ('CN' in markets and cn_open) or ('US' in markets and us_open)
            if not any_open:
                result['skipped_reason'] = '非交易时段'
                logger.info(
                    "[AutoTrade] 跳过：非交易时段（关注市场 %s，CN=%s US=%s）",
                    markets, cn_open, us_open,
                )
                self._last_run_result = result
                return result

        # ---- 最大回撤保护 ----
        snapshots = self.repo.list_snapshots(account_id, limit=1)
        if snapshots:
            last_dd = snapshots[-1].get('max_drawdown_pct', 0.0)
            if last_dd >= acct.get('max_drawdown_pct', 20.0):
                result['skipped_reason'] = f"最大回撤保护触发 ({last_dd:.1f}% ≥ {acct['max_drawdown_pct']:.1f}%)"
                self.repo.update_account(account_id, status='paused')
                logger.warning("[AutoTrade] 最大回撤保护：账户已暂停")
                self._last_run_result = result
                return result

        # ---- 生成信号并执行 ----
        signal_svc = SignalService(repo=self.repo)
        order_svc = OrderService(repo=self.repo)

        for item in watchlist:
            code = item['code']
            name = item.get('name', '')
            market = SignalService._infer_market(code)
            try:
                signal = signal_svc.generate_signal(code, market, name)
                result['signals_generated'] += 1

                if signal['signal'] in ('buy', 'sell') and signal.get('suggested_qty', 0):
                    try:
                        order = order_svc.place_order(
                            code=code,
                            market=market,
                            side=signal['signal'],
                            order_type='limit' if signal.get('suggested_price') else 'market',
                            qty=signal['suggested_qty'],
                            limit_price=signal.get('suggested_price'),
                            name=name,
                            source='auto',
                            ai_signal_id=signal['id'],
                            current_price=signal.get('price_at_signal'),
                        )
                        # 标记信号已执行
                        self.repo.update_signal(signal['id'], status='executed', order_id=order['id'])
                        result['orders_placed'] += 1
                    except Exception as exc:
                        self.repo.update_signal(signal['id'], status='rejected')
                        logger.warning("[AutoTrade] %s 下单失败: %s", code, exc)
                        result['errors'].append(f"{code}: {exc}")
                elif signal['signal'] in ('hold', 'skip'):
                    self.repo.update_signal(signal['id'], status='rejected')

            except Exception as exc:
                logger.error("[AutoTrade] %s 信号生成失败: %s", code, exc)
                result['errors'].append(f"{code} signal: {exc}")

        # ---- 挂单撮合 ----
        try:
            filled = order_svc.try_fill_pending_orders(account_id)
            if filled:
                result['orders_placed'] += filled
        except Exception as exc:
            logger.warning("[AutoTrade] 挂单撮合失败: %s", exc)

        # ---- 止损/止盈 ----
        try:
            triggered = order_svc.check_stop_loss_take_profit(account_id)
            result['stop_loss_triggered'] = triggered
        except Exception as exc:
            logger.warning("[AutoTrade] 止损/止盈检查失败: %s", exc)

        # ---- 刷新价格 ----
        try:
            order_svc.refresh_position_prices(account_id)
        except Exception as exc:
            logger.debug("[AutoTrade] 价格刷新失败: %s", exc)

        # ---- 日快照 ----
        try:
            order_svc.take_daily_snapshot(account_id)
        except Exception as exc:
            logger.warning("[AutoTrade] 日快照失败: %s", exc)

        # ---- 过期信号清理 ----
        try:
            self.repo.expire_old_signals(account_id)
        except Exception:
            pass

        result['finished_at'] = datetime.now().isoformat()
        self._last_run_result = result
        logger.info(
            "[AutoTrade] 周期完成：信号 %d，委托 %d，止损触发 %s",
            result['signals_generated'], result['orders_placed'], result['stop_loss_triggered'],
        )
        return result

    def get_last_run_result(self) -> Dict[str, Any]:
        return self._last_run_result

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_market_status(self) -> Dict[str, Any]:
        return {
            'cn_open': _is_cn_market_open(),
            'us_open': _is_us_market_open(),
            'market_hours_only': self._market_hours_only,
        }

    # -------------------------------------------------------
    # Market watcher — auto-start / auto-stop
    # -------------------------------------------------------

    def start_market_watcher(self) -> None:
        """Start the background market-hours watcher (idempotent)."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return
        self._watcher_stop.clear()
        self._watcher_thread = threading.Thread(
            target=self._market_watcher_loop,
            name='simtrade-market-watcher',
            daemon=True,
        )
        self._watcher_thread.start()
        logger.info("[AutoTrade] 市场监控线程已启动")

    def _market_watcher_loop(self) -> None:
        """Detect market open/close transitions; auto-start or stop when configured."""
        prev_any_open: Optional[bool] = None

        while not self._watcher_stop.is_set():
            try:
                cn_open = _is_cn_market_open()
                us_open = _is_us_market_open()
                any_open = cn_open or us_open

                acct = self.repo.get_or_create_account()
                if acct.get('auto_start_on_market_open'):
                    just_opened = any_open and prev_any_open is not None and not prev_any_open
                    just_closed = not any_open and prev_any_open
                    # Also handle server starting mid-session
                    server_start_open = prev_any_open is None and any_open

                    if just_opened or server_start_open:
                        from src.services.watchlist_service import WatchlistService
                        if WatchlistService().list_all():
                            logger.info("[AutoTrade] 市场开盘，自动启动交易")
                            self.repo.update_account(acct['id'], auto_trade_enabled=True, status='active')
                            self.start()
                    elif just_closed:
                        logger.info("[AutoTrade] 市场收盘，自动停止交易")
                        self.repo.update_account(acct['id'], auto_trade_enabled=False)
                        self.stop()

                prev_any_open = any_open
            except Exception as exc:
                logger.error("[AutoTrade] 市场监控异常: %s", exc)

            self._watcher_stop.wait(timeout=60)


def get_auto_trade_service() -> AutoTradeService:
    return AutoTradeService.get_instance()
