import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  BarChart2,
  CreditCard,
  Play,
  Power,
  RefreshCw,
  ShoppingCart,
  TrendingUp,
} from 'lucide-react';
import * as api from '../api/simtrade';
import type {
  AccountSettingsRequest,
  AutoRunResult,
  AutoTradeStatus,
  FundItem,
  FundRequest,
  MarketStatus,
  OrderRequest,
  RunJob,
  SimAccount,
  SimOrder,
  SimPosition,
  SimSignal,
  SimSnapshot,
} from '../types/simtrade';
import { cn } from '../utils/cn';

// ─── helpers ──────────────────────────────────────────────────────────────

const fmt = (n: number, digits = 2) =>
  n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtPct = (n: number) => `${n >= 0 ? '+' : ''}${fmt(n)}%`;

const pnlColor = (n: number) =>
  n > 0 ? 'text-emerald-500' : n < 0 ? 'text-red-500' : 'text-secondary-text';

const SIGNAL_COLOR: Record<string, string> = {
  buy: 'text-emerald-500',
  sell: 'text-red-500',
  hold: 'text-amber-500',
  skip: 'text-secondary-text',
};

const STATUS_LABEL: Record<string, string> = {
  pending: '待成交',
  filled: '已成交',
  partial: '部分成交',
  cancelled: '已撤销',
  executed: '已执行',
  expired: '已过期',
  rejected: '已拒绝',
};

const MODE_LABEL: Record<string, string> = {
  conservative: '保守',
  balanced: '均衡',
  aggressive: '激进',
};

// ─── sub-components ───────────────────────────────────────────────────────

const MarketStatusBadges: React.FC<{ status?: MarketStatus }> = ({ status }) => {
  if (!status) return null;
  const dot = (open: boolean) => (
    <span className={`inline-block h-1.5 w-1.5 rounded-full ${open ? 'bg-emerald-500' : 'bg-gray-400'}`} />
  );
  return (
    <div className="flex items-center gap-3 mt-1.5">
      <span className="flex items-center gap-1 text-xs text-secondary-text">
        {dot(status.cn_open)} A-share {status.cn_open ? 'open' : 'closed'}
      </span>
      <span className="flex items-center gap-1 text-xs text-secondary-text">
        {dot(status.us_open)} US {status.us_open ? 'open' : 'closed'}
      </span>
      {status.market_hours_only && (
        <span className="text-xs text-secondary-text opacity-60">· runs during market hours only</span>
      )}
    </div>
  );
};

const StatCard: React.FC<{
  label: string;
  value: string;
  sub?: string;
  color?: string;
}> = ({ label, value, sub, color }) => (
  <div className="flex flex-col gap-1 rounded-xl border border-border bg-card p-4">
    <span className="text-xs text-secondary-text">{label}</span>
    <span className={cn('text-xl font-semibold tabular-nums', color ?? 'text-foreground')}>{value}</span>
    {sub ? <span className="text-xs text-secondary-text">{sub}</span> : null}
  </div>
);

const SectionTitle: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <h3 className="mb-3 text-sm font-semibold text-secondary-text uppercase tracking-wide">{children}</h3>
);

// ─── Tabs ─────────────────────────────────────────────────────────────────

type Tab = 'overview' | 'manual' | 'auto' | 'funding';

const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
  { key: 'overview', label: '账户概览', icon: <BarChart2 className="h-4 w-4" /> },
  { key: 'manual', label: '手动交易', icon: <ShoppingCart className="h-4 w-4" /> },
  { key: 'auto', label: '自动交易', icon: <TrendingUp className="h-4 w-4" /> },
  { key: 'funding', label: '资金管理', icon: <CreditCard className="h-4 w-4" /> },
];

// ─── Overview Tab ─────────────────────────────────────────────────────────

const OverviewTab: React.FC<{
  account: SimAccount;
  positions: SimPosition[];
  signals: SimSignal[];
  snapshots: SimSnapshot[];
  loading: boolean;
}> = ({ account, positions, signals, snapshots, loading }) => {
  const eq = account.equity_summary;

  if (loading) {
    return <div className="flex justify-center py-12 text-secondary-text">加载中…</div>;
  }

  // Simple ASCII equity curve using last 10 snapshots
  const recentSnaps = snapshots.slice(-10);

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="账户总权益 (CNY)"
          value={eq ? `¥${fmt(eq.total_equity_cny)}` : '—'}
          sub={account.status === 'paused' ? '⏸ 已暂停' : account.auto_trade_enabled ? '🤖 自动交易中' : '手动模式'}
        />
        <StatCard
          label="浮动盈亏"
          value={eq ? fmtPct(eq.total_return_pct) : '—'}
          color={eq ? pnlColor(eq.total_return_pct) : undefined}
          sub={eq ? `未实现 ¥${fmt(eq.unrealized_pnl)}` : undefined}
        />
        <StatCard label="可用 CNY" value={`¥${fmt(account.cash_cny)}`} />
        <StatCard label="可用 USD" value={`$${fmt(account.cash_usd)}`} sub={`≈ ¥${fmt(account.cash_usd * (eq?.fx_rate_usd_cny ?? 7.25))}`} />
      </div>

      {/* Equity curve (mini) */}
      {recentSnaps.length >= 2 ? (
        <div>
          <SectionTitle>净值走势（近 {recentSnaps.length} 日）</SectionTitle>
          <div className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-end gap-1 h-20">
              {recentSnaps.map((s, i) => {
                const max = Math.max(...recentSnaps.map((x) => x.total_equity_cny));
                const min = Math.min(...recentSnaps.map((x) => x.total_equity_cny));
                const range = max - min || 1;
                const h = Math.max(8, ((s.total_equity_cny - min) / range) * 72);
                const isLast = i === recentSnaps.length - 1;
                const color = s.total_return_pct >= 0 ? 'bg-emerald-500/70' : 'bg-red-500/70';
                return (
                  <div key={s.id} className="flex flex-1 flex-col items-center gap-1">
                    <div className={cn('w-full rounded-sm', color)} style={{ height: h }} title={`¥${fmt(s.total_equity_cny)}`} />
                    {isLast ? <span className="text-[9px] text-secondary-text">{s.date.slice(5)}</span> : null}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}

      {/* Positions */}
      {positions.length > 0 ? (
        <div>
          <SectionTitle>当前持仓</SectionTitle>
          <div className="rounded-xl border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-secondary-text text-xs">
                  {['代码', '名称', '数量', '成本价', '现价', '浮动盈亏', '止损', '止盈'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.id} className="border-b border-border/50 hover:bg-hover/50">
                    <td className="px-3 py-2 font-mono font-medium">{p.code}</td>
                    <td className="px-3 py-2 text-secondary-text">{p.name ?? '—'}</td>
                    <td className="px-3 py-2 tabular-nums">{p.qty}</td>
                    <td className="px-3 py-2 tabular-nums">{fmt(p.avg_cost, 3)}</td>
                    <td className="px-3 py-2 tabular-nums">{fmt(p.last_price, 3)}</td>
                    <td className={cn('px-3 py-2 tabular-nums font-medium', pnlColor(p.unrealized_pnl))}>
                      {fmtPct(p.unrealized_pnl_pct)}
                    </td>
                    <td className="px-3 py-2 tabular-nums text-red-400">{p.stop_loss_price ? fmt(p.stop_loss_price, 3) : '—'}</td>
                    <td className="px-3 py-2 tabular-nums text-emerald-400">{p.take_profit_price ? fmt(p.take_profit_price, 3) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-2 py-8 text-secondary-text">
          <ShoppingCart className="h-8 w-8 opacity-30" />
          <span className="text-sm">暂无持仓</span>
        </div>
      )}

      {/* Recent signals */}
      {signals.length > 0 ? (
        <div>
          <SectionTitle>AI 信号动态（最近 10 条）</SectionTitle>
          <div className="space-y-2">
            {signals.slice(0, 10).map((s) => (
              <div key={s.id} className="flex items-start gap-3 rounded-lg border border-border bg-card px-3 py-2">
                <span className={cn('mt-0.5 text-xs font-bold uppercase', SIGNAL_COLOR[s.signal])}>{s.signal}</span>
                <div className="flex-1 min-w-0">
                  <span className="font-mono font-medium">{s.code}</span>
                  {s.name ? <span className="ml-1 text-xs text-secondary-text">({s.name})</span> : null}
                  <p className="text-xs text-secondary-text mt-0.5 truncate">{s.reasoning ?? '—'}</p>
                </div>
                <div className="flex flex-col items-end gap-0.5 shrink-0">
                  <span className="text-xs text-secondary-text">{(s.confidence * 100).toFixed(0)}%</span>
                  <span className="text-[10px] text-secondary-text/60">{STATUS_LABEL[s.status] ?? s.status}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};

// ─── Manual Trade Tab ─────────────────────────────────────────────────────

const ManualTradeTab: React.FC<{
  account: SimAccount;
  orders: SimOrder[];
  positions: SimPosition[];
  onRefresh: () => void;
}> = ({ account, orders, positions, onRefresh }) => {
  const [form, setForm] = useState<OrderRequest>({
    code: '',
    market: 'CN',
    side: 'buy',
    order_type: 'market',
    qty: 100,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    if (!form.code.trim()) {
      setError('请输入股票代码');
      return;
    }
    setSubmitting(true);
    try {
      const order = await api.placeOrder({
        ...form,
        code: form.code.toUpperCase(),
        limit_price: form.order_type === 'market' ? undefined : form.limit_price,
      });
      setSuccess(`委托已提交：${order.code} ${order.side === 'buy' ? '买入' : '卖出'} ${order.qty} 股，状态：${STATUS_LABEL[order.status]}`);
      onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '下单失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async (orderId: number) => {
    try {
      await api.cancelOrder(orderId);
      onRefresh();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '撤单失败');
    }
  };

  if (account.auto_trade_enabled) {
    return (
      <div className="flex flex-col items-center gap-4 py-16 text-center">
        <Power className="h-10 w-10 text-amber-500" />
        <p className="text-lg font-medium">自动交易已开启</p>
        <p className="text-sm text-secondary-text max-w-sm">
          自动交易运行中，手动下单已禁用。<br />
          请前往「自动交易」页关闭自动交易后再手动操作。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Order form */}
      <div>
        <SectionTitle>新建委托</SectionTitle>
        <form onSubmit={(e) => void handleSubmit(e)} className="rounded-xl border border-border bg-card p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="col-span-2 sm:col-span-1">
              <label className="block text-xs text-secondary-text mb-1">股票代码</label>
              <input
                className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/30"
                placeholder="如 600519 或 AAPL"
                value={form.code}
                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
              />
            </div>
            <div>
              <label className="block text-xs text-secondary-text mb-1">市场</label>
              <select
                className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm"
                value={form.market}
                onChange={(e) => setForm((f) => ({ ...f, market: e.target.value as 'CN' | 'US' }))}
              >
                <option value="CN">A 股 (CN)</option>
                <option value="US">美股 (US)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-secondary-text mb-1">方向</label>
              <div className="flex gap-2">
                {(['buy', 'sell'] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, side: s }))}
                    className={cn(
                      'flex-1 rounded-lg border py-2 text-sm font-medium transition-colors',
                      form.side === s
                        ? s === 'buy'
                          ? 'border-emerald-500 bg-emerald-500/10 text-emerald-500'
                          : 'border-red-500 bg-red-500/10 text-red-500'
                        : 'border-border text-secondary-text hover:bg-hover'
                    )}
                  >
                    {s === 'buy' ? '买入' : '卖出'}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs text-secondary-text mb-1">委托类型</label>
              <select
                className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm"
                value={form.order_type}
                onChange={(e) => setForm((f) => ({ ...f, order_type: e.target.value as 'limit' | 'market' }))}
              >
                <option value="market">市价单</option>
                <option value="limit">限价单</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-secondary-text mb-1">
                数量{form.market === 'CN' ? '（须为 100 的整数倍）' : ''}
              </label>
              <input
                type="number"
                min={form.market === 'CN' ? 100 : 1}
                step={form.market === 'CN' ? 100 : 1}
                className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm tabular-nums"
                value={form.qty}
                onChange={(e) => setForm((f) => ({ ...f, qty: Number(e.target.value) }))}
              />
            </div>
            {form.order_type === 'limit' ? (
              <div>
                <label className="block text-xs text-secondary-text mb-1">限价</label>
                <input
                  type="number"
                  step="0.001"
                  min="0.001"
                  className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm tabular-nums"
                  value={form.limit_price ?? ''}
                  onChange={(e) => setForm((f) => ({ ...f, limit_price: Number(e.target.value) || undefined }))}
                />
              </div>
            ) : null}
          </div>

          {error ? (
            <div className="flex items-center gap-2 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-500">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          ) : null}
          {success ? (
            <div className="rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-500">{success}</div>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className={cn(
              'w-full rounded-lg py-2.5 text-sm font-medium text-white transition-colors',
              form.side === 'buy' ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500',
              submitting && 'opacity-50 cursor-not-allowed'
            )}
          >
            {submitting ? '提交中…' : form.side === 'buy' ? '买入' : '卖出'}
          </button>
        </form>
      </div>

      {/* Positions */}
      {positions.length > 0 ? (
        <div>
          <SectionTitle>当前持仓</SectionTitle>
          <div className="rounded-xl border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-secondary-text text-xs">
                  {['代码', '市场', '数量', '成本', '现价', '浮动 P&L', '止损', '止盈'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.id} className="border-b border-border/40 hover:bg-hover/50">
                    <td className="px-3 py-2 font-mono font-medium">{p.code}</td>
                    <td className="px-3 py-2 text-secondary-text text-xs">{p.market}</td>
                    <td className="px-3 py-2 tabular-nums">{p.qty}</td>
                    <td className="px-3 py-2 tabular-nums">{fmt(p.avg_cost, 3)}</td>
                    <td className="px-3 py-2 tabular-nums">{fmt(p.last_price, 3)}</td>
                    <td className={cn('px-3 py-2 tabular-nums font-medium', pnlColor(p.unrealized_pnl))}>
                      {fmtPct(p.unrealized_pnl_pct)}
                    </td>
                    <td className="px-3 py-2 tabular-nums text-red-400 text-xs">{p.stop_loss_price ? fmt(p.stop_loss_price, 3) : '—'}</td>
                    <td className="px-3 py-2 tabular-nums text-emerald-400 text-xs">{p.take_profit_price ? fmt(p.take_profit_price, 3) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Recent orders */}
      <div>
        <SectionTitle>近期委托</SectionTitle>
        {orders.length === 0 ? (
          <p className="text-sm text-secondary-text py-4 text-center">暂无委托记录</p>
        ) : (
          <div className="rounded-xl border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-secondary-text text-xs">
                  {['代码', '方向', '类型', '数量', '限价/成交价', '状态', '来源', '操作'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-b border-border/40 hover:bg-hover/50">
                    <td className="px-3 py-2 font-mono font-medium">{o.code}</td>
                    <td className={cn('px-3 py-2 font-medium', o.side === 'buy' ? 'text-emerald-500' : 'text-red-500')}>
                      {o.side === 'buy' ? '买入' : '卖出'}
                    </td>
                    <td className="px-3 py-2 text-secondary-text text-xs">{o.order_type === 'market' ? '市价' : '限价'}</td>
                    <td className="px-3 py-2 tabular-nums">{o.qty}</td>
                    <td className="px-3 py-2 tabular-nums">
                      {o.fill_price ? fmt(o.fill_price, 3) : o.limit_price ? fmt(o.limit_price, 3) : '—'}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span className={cn(
                        'inline-block rounded-full px-2 py-0.5',
                        o.status === 'filled' ? 'bg-emerald-500/15 text-emerald-500' :
                        o.status === 'pending' ? 'bg-amber-500/15 text-amber-500' :
                        'bg-secondary/20 text-secondary-text'
                      )}>
                        {STATUS_LABEL[o.status] ?? o.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-secondary-text">{o.source === 'auto' ? '🤖 AI' : '手动'}</td>
                    <td className="px-3 py-2">
                      {o.status === 'pending' ? (
                        <button
                          type="button"
                          onClick={() => void handleCancel(o.id)}
                          className="rounded px-2 py-1 text-xs text-red-400 hover:bg-red-500/10"
                        >
                          撤单
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Auto Trade Tab ────────────────────────────────────────────────────────

const AutoTradeTab: React.FC<{
  account: SimAccount;
  signals: SimSignal[];
  autoStatus: AutoTradeStatus | null;
  onRefresh: () => void;
}> = ({ account, signals, autoStatus, onRefresh }) => {
  const [toggling, setToggling] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<AutoRunResult | null>(null);
  const [settings, setSettings] = useState<AccountSettingsRequest>({
    auto_trade_mode: account.auto_trade_mode,
    auto_start_on_market_open: account.auto_start_on_market_open,
    clear_on_market_close: account.clear_on_market_close,
    clear_before_close_minutes: account.clear_before_close_minutes,
    scan_interval_minutes: account.scan_interval_minutes,
    max_position_pct: account.max_position_pct,
    max_drawdown_pct: account.max_drawdown_pct,
    stop_loss_pct: account.stop_loss_pct,
    take_profit_pct: account.take_profit_pct,
    min_signal_confidence: account.min_signal_confidence,
  });
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsMsg, setSettingsMsg] = useState('');

  const noWatchlist = (autoStatus?.watchlist_count ?? 0) === 0;
  const ms = autoStatus?.market_status;
  const marketClosed = !!ms?.market_hours_only && !ms.cn_open && !ms.us_open;
  const executing = running || !!autoStatus?.run_in_progress;
  const schedulerActive = !!autoStatus?.scheduler_running;
  const autoStateLabel = executing
    ? '执行中'
    : account.auto_trade_enabled
      ? marketClosed
        ? '等待开市'
        : schedulerActive
          ? '运行中'
          : '已开启'
      : '已停止';
  const autoStateClass = executing
    ? 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400'
    : account.auto_trade_enabled && !marketClosed
      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
      : 'border-border bg-muted text-secondary-text';
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current);
    };
  }, []);

  const handleToggle = async () => {
    setToggling(true);
    const turningOff = account.auto_trade_enabled;
    try {
      await api.toggleAutoTrade(!account.auto_trade_enabled);
      if (turningOff) {
        if (pollRef.current !== null) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setRunning(false);
      }
      onRefresh();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '操作失败');
    } finally {
      setToggling(false);
    }
  };

  const handleRunNow = async () => {
    setRunning(true);
    setRunResult(null);
    let job: RunJob;
    try {
      job = await api.startAutoTrade();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : '执行失败');
      setRunning(false);
      return;
    }
    let attempts = 0;
    pollRef.current = setInterval(() => {
      attempts++;
      api.getRunJob(job.job_id).then((status) => {
        if (status.status === 'done') {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          if (status.result) setRunResult(status.result);
          onRefresh();
          setRunning(false);
        } else if (status.status === 'error') {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          alert(status.error ?? '执行失败');
          setRunning(false);
        } else if (attempts >= 120) {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          alert('执行超时，请稍后刷新查看结果');
          setRunning(false);
        }
      }).catch(() => {
        clearInterval(pollRef.current!);
        pollRef.current = null;
        setRunning(false);
      });
    }, 2000);
  };

  const handleSaveSettings = async () => {
    setSavingSettings(true);
    setSettingsMsg('');
    try {
      await api.updateSettings(settings);
      setSettingsMsg('设置已保存');
      onRefresh();
      setTimeout(() => setSettingsMsg(''), 2000);
    } catch (err: unknown) {
      setSettingsMsg(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSavingSettings(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Watchlist warning */}
      {noWatchlist ? (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
          <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-amber-600 dark:text-amber-400">自选股列表为空</p>
            <p className="text-sm text-secondary-text mt-0.5">
              自动交易需要先添加自选股。
              <Link to="/watchlist" className="ml-1 text-primary hover:underline">
                前往自选股 →
              </Link>
            </p>
          </div>
        </div>
      ) : null}

      {/* Toggle & status */}
      <div className="flex items-center justify-between rounded-xl border border-border bg-card p-4">
        <div>
          <div className="flex items-center gap-2">
            <p className="font-medium">AI 自动交易</p>
            <span className={cn('rounded-full border px-2 py-0.5 text-xs font-medium', autoStateClass)}>
              {autoStateLabel}
            </span>
          </div>
          <p className="text-sm text-secondary-text mt-0.5">
            {account.auto_trade_enabled
              ? marketClosed
                ? `Waiting for market open · ${MODE_LABEL[account.auto_trade_mode]} mode`
                : `Active · ${MODE_LABEL[account.auto_trade_mode]} mode`
              : 'Stopped · enable to auto-monitor watchlist and place orders'}
          </p>
          <MarketStatusBadges status={autoStatus?.market_status} />
        </div>
        <div className="flex items-center gap-3">
          {account.auto_trade_enabled ? (
            <button
              type="button"
              onClick={() => void handleRunNow()}
              disabled={executing || schedulerActive || noWatchlist || marketClosed}
              className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm hover:bg-hover disabled:opacity-40"
              title={marketClosed ? 'Market is closed' : schedulerActive ? '自动交易运行中，无需手动触发' : undefined}
            >
              <Play className="h-3.5 w-3.5" />
              {executing ? '执行中…' : schedulerActive ? '运行中…' : '立即运行'}
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void handleToggle()}
            disabled={toggling || (noWatchlist && !account.auto_trade_enabled)}
            className={cn(
              'flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-colors',
              account.auto_trade_enabled
                ? 'bg-red-500/15 text-red-500 hover:bg-red-500/25'
                : 'bg-emerald-500/15 text-emerald-500 hover:bg-emerald-500/25',
              (toggling || (noWatchlist && !account.auto_trade_enabled)) && 'opacity-40 cursor-not-allowed'
            )}
          >
            <Power className="h-4 w-4" />
            {toggling ? '切换中…' : account.auto_trade_enabled ? '关闭' : '开启'}
          </button>
        </div>
      </div>

      {/* Last run result */}
      {runResult ? (
        <div className="rounded-xl border border-border bg-card p-4 text-sm space-y-1">
          <p className="font-medium text-secondary-text text-xs uppercase tracking-wide">上次运行结果</p>
          {runResult.skipped_reason ? (
            <p className="text-amber-500">⚠ 跳过：{runResult.skipped_reason}</p>
          ) : (
            <>
              <p>生成信号：<span className="font-medium">{runResult.signals_generated}</span> 条</p>
              <p>提交委托：<span className="font-medium">{runResult.orders_placed}</span> 笔</p>
              {runResult.stop_loss_triggered.length > 0 ? (
                <p className="text-red-400">止损触发：{runResult.stop_loss_triggered.join(', ')}</p>
              ) : null}
              {runResult.errors.length > 0 ? (
                <p className="text-red-400">错误：{runResult.errors[0]}{runResult.errors.length > 1 ? `… 共 ${runResult.errors.length} 条` : ''}</p>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      {/* Strategy settings */}
      <div>
        <SectionTitle>策略参数</SectionTitle>
        <div className="rounded-xl border border-border bg-card p-4 space-y-4">
          {/* Mode */}
          <div>
            <label className="block text-xs text-secondary-text mb-2">交易风格</label>
            <div className="flex gap-2">
              {(['conservative', 'balanced', 'aggressive'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setSettings((s) => ({ ...s, auto_trade_mode: m }))}
                  className={cn(
                    'flex-1 rounded-lg border py-2 text-sm transition-colors',
                    settings.auto_trade_mode === m
                      ? 'border-primary/60 bg-primary/10 text-primary font-medium'
                      : 'border-border text-secondary-text hover:bg-hover'
                  )}
                >
                  {MODE_LABEL[m]}
                </button>
              ))}
            </div>
            <p className="text-xs text-secondary-text mt-1">
              保守：置信度 &gt;75% · 均衡：&gt;65% · 激进：&gt;55%
            </p>
          </div>

          {/* Sliders / inputs */}
          {[
            { key: 'max_position_pct', label: '单股最大仓位 (%)', min: 1, max: 100 },
            { key: 'max_drawdown_pct', label: '最大回撤保护 (%)', min: 1, max: 100 },
            { key: 'stop_loss_pct', label: '默认止损 (%)', min: 0.5, max: 50, step: 0.5 },
            { key: 'take_profit_pct', label: '默认止盈 (%)', min: 1, max: 200 },
            { key: 'min_signal_confidence', label: '最低信号置信度', min: 0, max: 1, step: 0.05 },
          ].map(({ key, label, min, max, step }) => (
            <div key={key} className="flex items-center gap-3">
              <label className="w-48 text-xs text-secondary-text shrink-0">{label}</label>
              <input
                type="number"
                min={min}
                max={max}
                step={step ?? 1}
                value={settings[key as keyof AccountSettingsRequest] as number ?? 0}
                onChange={(e) => setSettings((s) => ({ ...s, [key]: Number(e.target.value) }))}
                className="w-24 rounded-lg border border-border bg-base px-3 py-1.5 text-sm tabular-nums"
              />
            </div>
          ))}

          {/* Auto-start on market open */}
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={!!settings.auto_start_on_market_open}
              onChange={(e) => setSettings((s) => ({ ...s, auto_start_on_market_open: e.target.checked }))}
              className="h-4 w-4 rounded border-border accent-primary"
            />
            <span className="text-sm">
              开盘自动启动 / 收盘自动停止
            </span>
          </label>

          {/* 空仓过夜 */}
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={!!settings.clear_on_market_close}
              onChange={(e) => setSettings((s) => ({ ...s, clear_on_market_close: e.target.checked }))}
              className="h-4 w-4 rounded border-border accent-primary"
            />
            <span className="text-sm font-medium">
              空仓过夜（收盘前自动清仓）
            </span>
          </label>

          {/* clear_before_close_minutes — only visible when 空仓过夜 is enabled */}
          {settings.clear_on_market_close && (
            <div className="flex items-center gap-3 pl-7">
              <label className="w-48 text-xs text-secondary-text shrink-0">收盘前 N 分钟清仓</label>
              <input
                type="number"
                min={1}
                max={60}
                step={1}
                value={settings.clear_before_close_minutes ?? 15}
                onChange={(e) => setSettings((s) => ({ ...s, clear_before_close_minutes: Number(e.target.value) }))}
                className="w-24 rounded-lg border border-border bg-base px-3 py-1.5 text-sm tabular-nums"
              />
              <span className="text-xs text-secondary-text">分钟</span>
            </div>
          )}

          {/* Scan interval */}
          <div className="flex items-center gap-3">
            <label className="w-48 text-xs text-secondary-text shrink-0">AI 扫描间隔（分钟）</label>
            <input
              type="number"
              min={1}
              max={60}
              step={1}
              value={settings.scan_interval_minutes ?? ''}
              placeholder="默认 5"
              onChange={(e) => {
                const v = e.target.value === '' ? null : Number(e.target.value);
                setSettings((s) => ({ ...s, scan_interval_minutes: v }));
              }}
              className="w-24 rounded-lg border border-border bg-base px-3 py-1.5 text-sm tabular-nums"
            />
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void handleSaveSettings()}
              disabled={savingSettings}
              className="rounded-lg bg-primary/15 text-primary px-4 py-2 text-sm hover:bg-primary/25 disabled:opacity-40"
            >
              {savingSettings ? '保存中…' : '保存设置'}
            </button>
            {settingsMsg ? <span className="text-sm text-secondary-text">{settingsMsg}</span> : null}
          </div>
        </div>
      </div>

      {/* Signal history */}
      <div>
        <SectionTitle>AI 信号历史</SectionTitle>
        {signals.length === 0 ? (
          <p className="text-sm text-secondary-text py-4 text-center">暂无 AI 信号记录</p>
        ) : (
          <div className="rounded-xl border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-secondary-text text-xs">
                  {['代码', '信号', '置信度', '当前价', '止损', '止盈', '状态', '原因'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id} className="border-b border-border/40 hover:bg-hover/50">
                    <td className="px-3 py-2 font-mono font-medium">{s.code}</td>
                    <td className={cn('px-3 py-2 font-bold uppercase text-xs', SIGNAL_COLOR[s.signal])}>{s.signal}</td>
                    <td className="px-3 py-2 tabular-nums text-xs">{(s.confidence * 100).toFixed(0)}%</td>
                    <td className="px-3 py-2 tabular-nums text-xs">{s.price_at_signal ? fmt(s.price_at_signal, 3) : '—'}</td>
                    <td className="px-3 py-2 tabular-nums text-xs text-red-400">{s.stop_loss ? fmt(s.stop_loss, 3) : '—'}</td>
                    <td className="px-3 py-2 tabular-nums text-xs text-emerald-400">{s.take_profit ? fmt(s.take_profit, 3) : '—'}</td>
                    <td className="px-3 py-2 text-xs text-secondary-text">{STATUS_LABEL[s.status] ?? s.status}</td>
                    <td className="px-3 py-2 text-xs text-secondary-text max-w-48 truncate" title={s.reasoning ?? ''}>{s.reasoning ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Funding Tab ───────────────────────────────────────────────────────────

const FundingTab: React.FC<{
  account: SimAccount;
  history: FundItem[];
  onRefresh: () => void;
}> = ({ account, history, onRefresh }) => {
  const [form, setForm] = useState<Omit<FundRequest, 'amount'> & { amount: string }>({
    direction: 'deposit',
    amount: '10000',
    currency: 'CNY',
    note: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg('');
    const amount = Number(form.amount);
    if (!Number.isInteger(amount) || amount <= 0) { setMsg('金额必须为大于 0 的整数'); return; }
    setSubmitting(true);
    try {
      await api.fund({ ...form, amount });
      setMsg(`操作成功：${form.direction === 'deposit' ? '入金' : '出金'} ${amount} ${form.currency}`);
      onRefresh();
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : '操作失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Cash summary */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard label="CNY 余额" value={`¥${fmt(account.cash_cny)}`} />
        <StatCard label="USD 余额" value={`$${fmt(account.cash_usd)}`} />
      </div>

      {/* Fund form */}
      <div>
        <SectionTitle>入金 / 出金</SectionTitle>
        <form onSubmit={(e) => void handleSubmit(e)} className="rounded-xl border border-border bg-card p-4 space-y-3">
          <div className="flex gap-2">
            {(['deposit', 'withdrawal'] as const).map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setForm((f) => ({ ...f, direction: d }))}
                className={cn(
                  'flex-1 rounded-lg border py-2 text-sm font-medium transition-colors',
                  form.direction === d
                    ? d === 'deposit'
                      ? 'border-emerald-500 bg-emerald-500/10 text-emerald-500'
                      : 'border-amber-500 bg-amber-500/10 text-amber-500'
                    : 'border-border text-secondary-text hover:bg-hover'
                )}
              >
                {d === 'deposit' ? '入金' : '出金'}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-secondary-text mb-1">货币</label>
              <select
                className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm"
                value={form.currency}
                onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value as 'CNY' | 'USD' }))}
              >
                <option value="CNY">CNY 人民币</option>
                <option value="USD">USD 美元</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-secondary-text mb-1">金额</label>
              <input
                type="number"
                min="1"
                step="1"
                inputMode="numeric"
                pattern="[0-9]*"
                className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm tabular-nums"
                value={form.amount}
                onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value.replace(/\D/g, '') }))}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs text-secondary-text mb-1">备注（可选）</label>
            <input
              className="w-full rounded-lg border border-border bg-base px-3 py-2 text-sm"
              value={form.note}
              onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              placeholder="如：初始入金"
            />
          </div>

          {msg ? (
            <div className={cn('rounded-lg px-3 py-2 text-sm', msg.startsWith('操作成功') ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500')}>
              {msg}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? '处理中…' : '确认'}
          </button>
        </form>
      </div>

      {/* History */}
      <div>
        <SectionTitle>资金流水</SectionTitle>
        {history.length === 0 ? (
          <p className="text-sm text-secondary-text py-4 text-center">暂无资金流水</p>
        ) : (
          <div className="rounded-xl border border-border bg-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-secondary-text text-xs">
                  {['类型', '金额', '货币', '备注', '时间'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((f) => (
                  <tr key={f.id} className="border-b border-border/40 hover:bg-hover/50">
                    <td className={cn('px-3 py-2 font-medium', f.direction === 'deposit' ? 'text-emerald-500' : 'text-amber-500')}>
                      {f.direction === 'deposit' ? '入金' : '出金'}
                    </td>
                    <td className="px-3 py-2 tabular-nums">{fmt(f.amount)}</td>
                    <td className="px-3 py-2 text-secondary-text text-xs">{f.currency}</td>
                    <td className="px-3 py-2 text-secondary-text text-xs">{f.note ?? '—'}</td>
                    <td className="px-3 py-2 text-secondary-text text-xs">{f.created_at ? new Date(f.created_at).toLocaleString('zh-CN') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Main Page ─────────────────────────────────────────────────────────────

const SimTradePage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('overview');
  const [account, setAccount] = useState<SimAccount | null>(null);
  const [positions, setPositions] = useState<SimPosition[]>([]);
  const [orders, setOrders] = useState<SimOrder[]>([]);
  const [signals, setSignals] = useState<SimSignal[]>([]);
  const [snapshots, setSnapshots] = useState<SimSnapshot[]>([]);
  const [fundHistory, setFundHistory] = useState<FundItem[]>([]);
  const [autoStatus, setAutoStatus] = useState<AutoTradeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchAll = useCallback(async () => {
    try {
      const [acct, pos, ord, sigs, snaps, fh, ats] = await Promise.all([
        api.getAccount(),
        api.getPositions(),
        api.getOrders({ limit: 30 }),
        api.getSignals(30),
        api.getSnapshotHistory(60),
        api.getFundHistory(50),
        api.getAutoTradeStatus(),
      ]);
      setAccount(acct);
      setPositions(pos.items);
      setOrders(ord.items);
      setSignals(sigs.items);
      setSnapshots(snaps.items);
      setFundHistory(fh.items);
      setAutoStatus(ats);
      setError('');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshLivePositions = useCallback(async () => {
    try {
      const [pos, acct, ats] = await Promise.all([
        api.getPositions({ refresh: true }),
        api.getAccount(),
        api.getAutoTradeStatus(),
      ]);
      setPositions(pos.items);
      setAccount(acct);
      setAutoStatus(ats);
    } catch {
      // Keep the current table visible if a short-interval refresh misses.
    }
  }, []);

  useEffect(() => {
    void fetchAll();
    // Poll every 60s when auto-trading is active
    const id = setInterval(() => void fetchAll(), 60_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  useEffect(() => {
    const id = setInterval(() => void refreshLivePositions(), 10_000);
    return () => clearInterval(id);
  }, [refreshLivePositions]);

  return (
    <div className="flex flex-col gap-6 p-4 sm:p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-primary" />
            模拟交易
          </h1>
          <p className="text-sm text-secondary-text mt-0.5">
            使用真实行情数据练习交易策略，不消耗真实资金
          </p>
        </div>
        <button
          type="button"
          onClick={() => void fetchAll()}
          disabled={loading}
          className="rounded-lg border border-border px-3 py-2 text-xs text-secondary-text hover:bg-hover disabled:opacity-40 flex items-center gap-1.5"
        >
          <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      {error ? (
        <div className="flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border pb-0">
        {TABS.map(({ key, label, icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={cn(
              'flex items-center gap-1.5 border-b-2 px-3 pb-3 pt-1 text-sm font-medium transition-colors',
              tab === key
                ? 'border-primary text-primary'
                : 'border-transparent text-secondary-text hover:text-foreground'
            )}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {loading && !account ? (
        <div className="flex justify-center py-16 text-secondary-text">
          <RefreshCw className="h-6 w-6 animate-spin" />
        </div>
      ) : !account ? null : (
        <>
          {tab === 'overview' ? (
            <OverviewTab account={account} positions={positions} signals={signals} snapshots={snapshots} loading={loading} />
          ) : null}
          {tab === 'manual' ? (
            <ManualTradeTab account={account} orders={orders} positions={positions} onRefresh={() => void fetchAll()} />
          ) : null}
          {tab === 'auto' ? (
            <AutoTradeTab account={account} signals={signals} autoStatus={autoStatus} onRefresh={() => void fetchAll()} />
          ) : null}
          {tab === 'funding' ? (
            <FundingTab account={account} history={fundHistory} onRefresh={() => void fetchAll()} />
          ) : null}
        </>
      )}
    </div>
  );
};

export default SimTradePage;
