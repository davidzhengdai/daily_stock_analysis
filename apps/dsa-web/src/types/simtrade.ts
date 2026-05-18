// 模拟交易类型定义

export interface EquitySummary {
  cash_equiv_cny: number;
  market_value_cny: number;
  total_equity_cny: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_return_pct: number;
  fx_rate_usd_cny: number;
  positions_count: number;
}

export interface SimAccount {
  id: number;
  name: string;
  base_currency: string;
  cash_cny: number;
  cash_usd: number;
  total_deposited_cny: number;
  total_deposited_usd: number;
  total_withdrawn_cny: number;
  total_withdrawn_usd: number;
  auto_trade_enabled: boolean;
  auto_trade_mode: 'conservative' | 'balanced' | 'aggressive';
  auto_start_on_market_open: boolean;
  clear_on_market_close: boolean;
  clear_before_close_minutes: number;
  scan_interval_minutes: number | null;
  max_position_pct: number;
  max_drawdown_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  min_signal_confidence: number;
  status: 'active' | 'paused';
  created_at: string | null;
  updated_at: string | null;
  equity_summary?: EquitySummary;
}

export interface FundItem {
  id: number;
  account_id: number;
  direction: 'deposit' | 'withdrawal';
  amount: number;
  currency: 'CNY' | 'USD';
  note: string | null;
  created_at: string | null;
}

export interface SimOrder {
  id: number;
  account_id: number;
  code: string;
  name: string | null;
  market: 'CN' | 'US';
  currency: 'CNY' | 'USD';
  side: 'buy' | 'sell';
  order_type: 'limit' | 'market';
  qty: number;
  limit_price: number | null;
  fill_price: number | null;
  fill_qty: number;
  commission: number;
  status: 'pending' | 'filled' | 'partial' | 'cancelled';
  source: 'manual' | 'auto';
  ai_signal_id: number | null;
  rejection_reason: string | null;
  created_at: string | null;
  filled_at: string | null;
}

export interface SimPosition {
  id: number;
  account_id: number;
  code: string;
  name: string | null;
  market: 'CN' | 'US';
  currency: 'CNY' | 'USD';
  qty: number;
  avg_cost: number;
  total_cost: number;
  last_price: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  realized_pnl: number;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  updated_at: string | null;
}

export interface SimSignal {
  id: number;
  account_id: number;
  code: string;
  name: string | null;
  market: string;
  signal: 'buy' | 'sell' | 'hold' | 'skip';
  confidence: number;
  price_at_signal: number | null;
  technical_score: number | null;
  sentiment_score: number | null;
  risk_score: number | null;
  position_size_pct: number | null;
  suggested_qty: number | null;
  suggested_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  reasoning: string | null;
  signal_factors: string | null;
  status: 'pending' | 'executed' | 'expired' | 'rejected';
  order_id: number | null;
  created_at: string | null;
  expires_at: string | null;
}

export interface SimSnapshot {
  id: number;
  account_id: number;
  date: string;
  cash_cny: number;
  cash_usd: number;
  fx_rate_usd_cny: number;
  market_value_cny: number;
  total_equity_cny: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  peak_equity_cny: number;
  created_at: string | null;
}

export interface AutoRunResult {
  started_at: string | null;
  finished_at: string | null;
  account_id: number | null;
  signals_generated: number;
  orders_placed: number;
  stop_loss_triggered: string[];
  errors: string[];
  skipped_reason: string | null;
}

export interface MarketStatus {
  cn_open: boolean;
  us_open: boolean;
  market_hours_only: boolean;
}

export interface AutoTradeStatus {
  auto_trade_enabled: boolean;
  account_status: string;
  scheduler_running: boolean;
  run_in_progress?: boolean;
  watchlist_count: number;
  last_run: AutoRunResult | null;
  market_status?: MarketStatus;
}

export interface RunJob {
  job_id: string;
  status: 'running' | 'done' | 'error';
  result?: AutoRunResult;
  error?: string;
}

// Request types
export interface FundRequest {
  direction: 'deposit' | 'withdrawal';
  amount: number;
  currency: 'CNY' | 'USD';
  note?: string;
}

export interface OrderRequest {
  code: string;
  market: 'CN' | 'US';
  side: 'buy' | 'sell';
  order_type: 'limit' | 'market';
  qty: number;
  limit_price?: number;
  name?: string;
}

export interface AccountSettingsRequest {
  auto_trade_mode?: 'conservative' | 'balanced' | 'aggressive';
  auto_start_on_market_open?: boolean;
  clear_on_market_close?: boolean;
  clear_before_close_minutes?: number;
  scan_interval_minutes?: number | null;
  max_position_pct?: number;
  max_drawdown_pct?: number;
  stop_loss_pct?: number;
  take_profit_pct?: number;
  min_signal_confidence?: number;
}
