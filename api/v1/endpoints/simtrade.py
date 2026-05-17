# -*- coding: utf-8 -*-
"""
===================================
模拟交易 API 接口
===================================

路由前缀：/api/v1/sim-trade

端点列表：
  GET    /account                  — 账户信息 + 权益摘要
  POST   /account/reset            — 重置账户
  PATCH  /account/settings         — 更新策略设置
  POST   /fund                     — 入金 / 出金
  GET    /fund/history             — 资金流水
  GET    /positions                — 持仓列表
  GET    /orders                   — 委托列表
  POST   /orders                   — 手动下单
  DELETE /orders/{order_id}        — 撤单
  GET    /signals                  — AI 信号列表
  POST   /auto-trade/toggle        — 开启 / 关闭自动交易
  POST   /auto-trade/run           — 异步触发一次自动交易循环，返回 job_id
  GET    /auto-trade/run/{job_id}  — 轮询任务状态
  GET    /auto-trade/status        — 自动交易运行状态
  GET    /snapshot/history         — 权益曲线历史
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from api.v1.schemas.simtrade import (
    AccountResponse,
    AccountSettingsRequest,
    AutoRunResult,
    AutoTradeToggleRequest,
    FundHistoryResponse,
    FundItem,
    FundRequest,
    OrderItem,
    OrderListResponse,
    OrderRequest,
    PositionItem,
    PositionListResponse,
    RunJobStarted,
    RunJobStatus,
    SignalItem,
    SignalListResponse,
    SnapshotHistoryResponse,
    SnapshotItem,
)
from src.services.simtrade.account_service import AccountService
from src.services.simtrade.order_service import OrderService
from src.services.simtrade.signal_service import SignalService
from src.services.simtrade.auto_trade_service import get_auto_trade_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Helpers
# ============================================================

def _err(msg: str, exc: Exception, status: int = 500) -> HTTPException:
    logger.error("%s: %s", msg, exc, exc_info=True)
    return HTTPException(status_code=status, detail={"error": "server_error", "message": f"{msg}: {exc}"})


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "message": msg})


def _get_fx() -> float:
    return float(os.getenv('SIMTRADE_USD_CNY_RATE', '7.25'))


# ============================================================
# Account
# ============================================================

@router.get("/account", response_model=AccountResponse, summary="获取模拟账户信息")
def get_account():
    svc = AccountService()
    try:
        data = svc.get_account_with_equity(fx_rate=_get_fx())
        return AccountResponse(**data)
    except Exception as exc:
        raise _err("获取账户失败", exc)


@router.post("/account/reset", response_model=AccountResponse, summary="重置模拟账户")
def reset_account():
    svc = AccountService()
    try:
        data = svc.reset_account()
        return AccountResponse(**data)
    except Exception as exc:
        raise _err("重置账户失败", exc)


@router.patch("/account/settings", response_model=AccountResponse, summary="更新自动交易策略设置")
def update_settings(request: AccountSettingsRequest):
    svc = AccountService()
    try:
        data = svc.update_settings(
            auto_trade_mode=request.auto_trade_mode,
            max_position_pct=request.max_position_pct,
            max_drawdown_pct=request.max_drawdown_pct,
            stop_loss_pct=request.stop_loss_pct,
            take_profit_pct=request.take_profit_pct,
            min_signal_confidence=request.min_signal_confidence,
            auto_start_on_market_open=request.auto_start_on_market_open,
            clear_on_market_close=request.clear_on_market_close,
            clear_before_close_minutes=request.clear_before_close_minutes,
            scan_interval_minutes=request.scan_interval_minutes,
        )
        return AccountResponse(**data)
    except ValueError as exc:
        raise _bad(str(exc))
    except Exception as exc:
        raise _err("更新设置失败", exc)


# ============================================================
# Funding
# ============================================================

@router.post("/fund", response_model=FundItem, status_code=201, summary="入金或出金")
def fund(request: FundRequest):
    svc = AccountService()
    try:
        direction = (request.direction or '').strip().lower()
        if direction not in ('deposit', 'withdrawal'):
            raise ValueError("direction 须为 deposit 或 withdrawal")
        if direction == 'deposit':
            record = svc.deposit(request.amount, request.currency, request.note)
        else:
            record = svc.withdraw(request.amount, request.currency, request.note)
        return FundItem(**record)
    except ValueError as exc:
        raise _bad(str(exc))
    except Exception as exc:
        raise _err("资金操作失败", exc)


@router.get("/fund/history", response_model=FundHistoryResponse, summary="资金流水")
def fund_history(limit: int = Query(50, ge=1, le=200)):
    svc = AccountService()
    try:
        items = svc.list_funding_history(limit=limit)
        return FundHistoryResponse(items=[FundItem(**i) for i in items], total=len(items))
    except Exception as exc:
        raise _err("获取资金流水失败", exc)


# ============================================================
# Orders
# ============================================================

@router.get("/orders", response_model=OrderListResponse, summary="委托列表")
def list_orders(
    status: Optional[str] = Query(None, description="pending/filled/cancelled"),
    source: Optional[str] = Query(None, description="manual/auto"),
    limit: int = Query(50, ge=1, le=200),
):
    svc = OrderService()
    try:
        items = svc.list_orders(status=status, source=source, limit=limit)
        return OrderListResponse(items=[OrderItem(**i) for i in items], total=len(items))
    except Exception as exc:
        raise _err("获取委托列表失败", exc)


@router.post("/orders", response_model=OrderItem, status_code=201, summary="手动下单")
def place_order(request: OrderRequest):
    svc = OrderService()
    try:
        order = svc.place_order(
            code=request.code.strip().upper(),
            market=request.market,
            side=request.side,
            order_type=request.order_type,
            qty=request.qty,
            limit_price=request.limit_price,
            name=request.name or "",
            source='manual',
        )
        return OrderItem(**order)
    except ValueError as exc:
        raise _bad(str(exc))
    except Exception as exc:
        raise _err("下单失败", exc)


@router.delete("/orders/{order_id}", summary="撤销委托")
def cancel_order(order_id: int):
    svc = OrderService()
    try:
        svc.cancel_order(order_id)
        return JSONResponse(content={"message": f"委托 {order_id} 已撤销"})
    except ValueError as exc:
        raise _bad(str(exc))
    except Exception as exc:
        raise _err("撤单失败", exc)


# ============================================================
# Positions
# ============================================================

@router.get("/positions", response_model=PositionListResponse, summary="持仓列表")
def list_positions():
    svc = OrderService()
    try:
        items = svc.list_positions()
        return PositionListResponse(items=[PositionItem(**i) for i in items], total=len(items))
    except Exception as exc:
        raise _err("获取持仓失败", exc)


# ============================================================
# AI Signals
# ============================================================

@router.get("/signals", response_model=SignalListResponse, summary="AI 信号列表")
def list_signals(limit: int = Query(30, ge=1, le=100)):
    svc = SignalService()
    try:
        items = svc.list_signals(limit=limit)
        return SignalListResponse(items=[SignalItem(**i) for i in items], total=len(items))
    except Exception as exc:
        raise _err("获取信号列表失败", exc)


# ============================================================
# Auto-trade
# ============================================================

@router.post("/auto-trade/toggle", response_model=AccountResponse, summary="开启或关闭自动交易")
def toggle_auto_trade(request: AutoTradeToggleRequest):
    svc = AccountService()
    auto_svc = get_auto_trade_service()
    try:
        data = svc.set_auto_trade_enabled(request.enabled)
        if request.enabled:
            auto_svc.start()
        else:
            auto_svc.stop()
        return AccountResponse(**data)
    except ValueError as exc:
        raise _bad(str(exc))
    except Exception as exc:
        raise _err("切换自动交易状态失败", exc)


@router.post("/auto-trade/run", response_model=RunJobStarted, status_code=202, summary="异步触发一次自动交易循环，返回 job_id 供轮询")
def run_auto_trade():
    auto_svc = get_auto_trade_service()
    try:
        job_id = auto_svc.run_once_async()
        return RunJobStarted(job_id=job_id, status='running')
    except Exception as exc:
        raise _err("启动自动交易失败", exc)


@router.get("/auto-trade/run/{job_id}", response_model=RunJobStatus, summary="轮询自动交易任务状态")
def get_run_job(job_id: str):
    auto_svc = get_auto_trade_service()
    job = auto_svc.get_job_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "任务不存在或已过期"})
    status = job.get('status', 'running')
    if status == 'done':
        return RunJobStatus(
            job_id=job_id,
            status='done',
            result=AutoRunResult(
                started_at=job.get('started_at'),
                finished_at=job.get('finished_at'),
                account_id=job.get('account_id'),
                signals_generated=job.get('signals_generated', 0),
                orders_placed=job.get('orders_placed', 0),
                stop_loss_triggered=job.get('stop_loss_triggered', []),
                errors=job.get('errors', []),
                skipped_reason=job.get('skipped_reason'),
            ),
        )
    if status == 'error':
        return RunJobStatus(job_id=job_id, status='error', error=job.get('error', '未知错误'))
    return RunJobStatus(job_id=job_id, status='running')


@router.get("/auto-trade/status", summary="自动交易运行状态")
def auto_trade_status():
    auto_svc = get_auto_trade_service()
    acct_svc = AccountService()
    try:
        acct = acct_svc.get_account()
        from src.services.watchlist_service import WatchlistService
        watchlist_count = len(WatchlistService().list_all())
        return {
            "auto_trade_enabled": acct.get('auto_trade_enabled', False),
            "account_status": acct.get('status', 'active'),
            "scheduler_running": auto_svc.is_running(),
            "watchlist_count": watchlist_count,
            "last_run": auto_svc.get_last_run_result(),
            "market_status": auto_svc.get_market_status(),
        }
    except Exception as exc:
        raise _err("获取自动交易状态失败", exc)


# ============================================================
# Snapshots
# ============================================================

@router.get("/snapshot/history", response_model=SnapshotHistoryResponse, summary="权益曲线历史")
def snapshot_history(limit: int = Query(90, ge=1, le=365)):
    from src.repositories.simtrade_repo import SimTradeRepo
    repo = SimTradeRepo()
    try:
        acct = repo.get_or_create_account()
        items = repo.list_snapshots(acct['id'], limit=limit)
        return SnapshotHistoryResponse(
            items=[SnapshotItem(**i) for i in items],
            total=len(items),
        )
    except Exception as exc:
        raise _err("获取快照历史失败", exc)
