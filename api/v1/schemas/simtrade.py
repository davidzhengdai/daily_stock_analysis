# -*- coding: utf-8 -*-
"""模拟交易 API Pydantic 模型。"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FundRequest(BaseModel):
    direction: str = Field(..., description="deposit 或 withdrawal")
    amount: int = Field(..., gt=0, description="金额，必须为大于 0 的整数")
    currency: str = Field(..., description="CNY 或 USD")
    note: str = Field("", description="备注")


class FundItem(BaseModel):
    id: int
    account_id: int
    direction: str
    amount: float
    currency: str
    note: Optional[str]
    created_at: Optional[str]


class FundHistoryResponse(BaseModel):
    items: List[FundItem]
    total: int


class OrderRequest(BaseModel):
    code: str = Field(..., description="股票代码")
    market: str = Field(..., description="CN 或 US")
    side: str = Field(..., description="buy 或 sell")
    order_type: str = Field(..., description="limit 或 market")
    qty: int = Field(..., gt=0, description="委托数量")
    limit_price: Optional[float] = Field(None, description="限价，market 订单留空")
    name: str = Field("", description="股票名称（可选）")


class OrderItem(BaseModel):
    id: int
    account_id: int
    code: str
    name: Optional[str]
    market: str
    currency: str
    side: str
    order_type: str
    qty: int
    limit_price: Optional[float]
    fill_price: Optional[float]
    fill_qty: int
    commission: float
    realized_pnl: Optional[float] = None
    status: str
    source: str
    ai_signal_id: Optional[int]
    rejection_reason: Optional[str]
    created_at: Optional[str]
    filled_at: Optional[str]


class OrderListResponse(BaseModel):
    items: List[OrderItem]
    total: int


class TradeHistoryItem(BaseModel):
    id: str
    account_id: int
    account_name: Optional[str] = None
    code: str
    name: Optional[str]
    market: str
    currency: str
    status: str
    source: str
    qty: int
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    opened_at: Optional[str]
    closed_at: Optional[str] = None
    ai_reasoning: Optional[str] = None
    sell_reason: Optional[str] = None
    order_ids: List[int]


class TradeHistoryResponse(BaseModel):
    items: List[TradeHistoryItem]
    total: int


class PositionItem(BaseModel):
    id: int
    account_id: int
    code: str
    name: Optional[str]
    market: str
    currency: str
    qty: int
    avg_cost: float
    total_cost: float
    last_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float
    stop_loss_price: Optional[float]
    take_profit_price: Optional[float]
    updated_at: Optional[str]


class PositionListResponse(BaseModel):
    items: List[PositionItem]
    total: int


class SignalItem(BaseModel):
    id: int
    account_id: int
    code: str
    name: Optional[str]
    market: str
    signal: str
    confidence: float
    price_at_signal: Optional[float]
    technical_score: Optional[float]
    sentiment_score: Optional[float]
    risk_score: Optional[float]
    position_size_pct: Optional[float]
    suggested_qty: Optional[int]
    suggested_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    reasoning: Optional[str]
    signal_factors: Optional[str]
    status: str
    order_id: Optional[int]
    created_at: Optional[str]
    expires_at: Optional[str]


class SignalListResponse(BaseModel):
    items: List[SignalItem]
    total: int


class EquitySummary(BaseModel):
    cash_equiv_cny: float
    market_value_cny: float
    total_equity_cny: float
    unrealized_pnl: float
    realized_pnl: float
    total_return_pct: float
    fx_rate_usd_cny: float
    positions_count: int


class AccountResponse(BaseModel):
    id: int
    name: str
    base_currency: str
    cash_cny: float
    cash_usd: float
    total_deposited_cny: float
    total_deposited_usd: float
    total_withdrawn_cny: float
    total_withdrawn_usd: float
    auto_trade_enabled: bool
    auto_trade_mode: str
    max_position_pct: float
    max_drawdown_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    min_signal_confidence: float
    auto_start_on_market_open: bool = False
    status: str
    created_at: Optional[str]
    updated_at: Optional[str]
    equity_summary: Optional[EquitySummary] = None


class AccountSettingsRequest(BaseModel):
    auto_trade_mode: Optional[str] = Field(None, description="conservative / balanced / aggressive")
    max_position_pct: Optional[float] = Field(None, ge=1, le=100)
    max_drawdown_pct: Optional[float] = Field(None, ge=1, le=100)
    stop_loss_pct: Optional[float] = Field(None, ge=0.5, le=100)
    take_profit_pct: Optional[float] = Field(None, ge=1, le=200)
    min_signal_confidence: Optional[float] = Field(None, ge=0, le=1)
    auto_start_on_market_open: Optional[bool] = None
    clear_on_market_close: Optional[bool] = None
    clear_before_close_minutes: Optional[int] = Field(None, ge=1, le=60)
    scan_interval_minutes: Optional[int] = Field(None, ge=0, le=60, description="0 或 null 表示使用全局默认值")


class AutoTradeToggleRequest(BaseModel):
    enabled: bool


class SnapshotItem(BaseModel):
    id: int
    account_id: int
    date: str
    cash_cny: float
    cash_usd: float
    fx_rate_usd_cny: float
    market_value_cny: float
    total_equity_cny: float
    realized_pnl: float
    unrealized_pnl: float
    total_return_pct: float
    max_drawdown_pct: float
    peak_equity_cny: float
    created_at: Optional[str]


class SnapshotHistoryResponse(BaseModel):
    items: List[SnapshotItem]
    total: int


class AutoRunResult(BaseModel):
    started_at: Optional[str]
    finished_at: Optional[str]
    account_id: Optional[int]
    signals_generated: int
    orders_placed: int
    stop_loss_triggered: List[str]
    errors: List[str]
    skipped_reason: Optional[str]


class RunJobStarted(BaseModel):
    job_id: str
    status: str  # "running"


class RunJobStatus(BaseModel):
    job_id: str
    status: str  # "running" | "done" | "error"
    result: Optional[AutoRunResult] = None
    error: Optional[str] = None


class AutoTradeRunItem(BaseModel):
    id: int
    account_id: int
    triggered_by: str
    started_at: Optional[str]
    finished_at: Optional[str]
    skipped_reason: Optional[str]
    signals_generated: int
    orders_placed: int
    stop_loss_triggered: List[str]
    errors: List[str]


class AutoTradeRunListResponse(BaseModel):
    items: List[AutoTradeRunItem]
    total: int
