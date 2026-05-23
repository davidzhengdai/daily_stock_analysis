import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Clock3,
  Gem,
  History,
  RefreshCw,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react';
import {
  AppPage,
  Badge,
  Button,
  Card,
  EmptyState,
  InlineAlert,
  PageHeader,
} from '../components/common';
import { discoveryApi } from '../api/discovery';
import { createParsedApiError, getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import type { DiscoveryEvent, DiscoveryItem } from '../types/discovery';
import { REFRESH_POLICY_MS } from '../utils/refreshPolicy';

const sourceLabels: Record<string, string> = {
  scanner: '扫股',
  gold_digger: '沙里淘金',
  heat_radar: '热点雷达',
};

const actionLabels: Record<string, string> = {
  added: '加入',
  expired: '过期',
  rejected: '移出',
};

function formatDateTime(value?: string | null): string {
  if (!value) return '--';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function formatPrice(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3).replace(/\.?0+$/, '') : '--';
}

function formatChange(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '--';
}

function formatTime(value?: string | null): string {
  if (!value) return '--';
  try {
    return new Date(value).toLocaleTimeString();
  } catch {
    return value;
  }
}

function latestQuoteFetchedAt(items: DiscoveryItem[]): string | null {
  const timestamps = items
    .map((item) => item.quote?.fetchedAt)
    .filter((value): value is string => Boolean(value));
  if (timestamps.length === 0) return null;
  return timestamps.sort().at(-1) ?? null;
}

function quoteSourceLabel(item: DiscoveryItem): string {
  if (!item.quote?.source) return '--';
  const status = item.quote.isCached ? '缓存' : '实时';
  return `${item.quote.source} · ${status} · ${formatTime(item.quote.fetchedAt)}`;
}

function sourceLabel(source?: string): string {
  return sourceLabels[source || ''] || source || '--';
}

function actionVariant(action: string): 'success' | 'warning' | 'danger' | 'info' {
  if (action === 'added') return 'success';
  if (action === 'expired') return 'warning';
  if (action === 'rejected') return 'danger';
  return 'info';
}

function statusVariant(status: string): 'success' | 'warning' | 'danger' | 'default' {
  if (status === 'active') return 'success';
  if (status === 'expired') return 'warning';
  if (status === 'rejected') return 'danger';
  return 'default';
}

const DiscoveryCard: React.FC<{
  item: DiscoveryItem;
  onReject: (item: DiscoveryItem) => void;
  rejecting: boolean;
}> = ({ item, onReject, rejecting }) => {
  const daysLeft = useMemo(() => {
    if (!item.expiresAt) return null;
    const diff = new Date(item.expiresAt).getTime() - Date.now();
    return Math.ceil(diff / 86400000);
  }, [item.expiresAt]);

  return (
    <Card padding="md" className="flex h-full flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold text-foreground">{item.ticker}</h3>
            <Badge variant={statusVariant(item.status)}>{item.status === 'active' ? '活跃' : item.status}</Badge>
            <Badge variant="info">{sourceLabel(item.source)}</Badge>
          </div>
          <p className="mt-1 truncate text-sm text-secondary-text">{item.name || '--'}</p>
        </div>
        <div className="text-right">
          <p className="font-mono text-xl font-semibold text-cyan">{Number(item.score || 0).toFixed(1)}</p>
          <p className="text-xs text-muted-text">score</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-xs text-muted-text">实时价</p>
          <p className="mt-1 font-mono text-foreground">{formatPrice(item.quote?.price)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-text">涨跌幅</p>
          <p className={`mt-1 font-mono ${
            (item.quote?.changePct ?? 0) > 0
              ? 'text-emerald-400'
              : (item.quote?.changePct ?? 0) < 0
                ? 'text-rose-400'
                : 'text-foreground'
          }`}>
            {formatChange(item.quote?.changePct)}
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-text">市场</p>
          <p className="mt-1 text-foreground">{item.market || '--'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-text">置信度</p>
          <p className="mt-1 text-foreground">{item.confidence}%</p>
        </div>
        <div>
          <p className="text-xs text-muted-text">周期</p>
          <p className="mt-1 text-foreground">{item.tradeHorizon || '--'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-text">剩余</p>
          <p className="mt-1 text-foreground">{daysLeft === null ? '--' : `${Math.max(daysLeft, 0)} 天`}</p>
        </div>
      </div>

      {item.themes.length ? (
        <div className="flex flex-wrap gap-1.5">
          {item.themes.slice(0, 4).map((theme) => (
            <Badge key={theme} variant="history">{theme}</Badge>
          ))}
        </div>
      ) : null}

      <p className="max-h-[3.75rem] min-h-[3.75rem] overflow-hidden text-sm leading-relaxed text-secondary-text">
        {item.thesis || '暂无加入理由'}
      </p>

      <div className="mt-auto flex items-center justify-between border-t border-border/50 pt-3 text-xs text-muted-text">
        <span>加入：{formatDateTime(item.addedAt)}</span>
        <span className="hidden text-right sm:inline">
          行情：{quoteSourceLabel(item)}
        </span>
        <Button
          type="button"
          variant="settings-secondary"
          size="sm"
          disabled={rejecting}
          onClick={() => onReject(item)}
        >
          <Trash2 className="h-4 w-4" />
          移出
        </Button>
      </div>
    </Card>
  );
};

const HistoryRow: React.FC<{ event: DiscoveryEvent }> = ({ event }) => (
  <div className="grid gap-3 border-b border-border/45 px-4 py-3 last:border-b-0 md:grid-cols-[150px_120px_100px_1fr]">
    <div>
      <p className="font-mono text-sm font-medium text-foreground">{event.ticker}</p>
      <p className="truncate text-xs text-muted-text">{event.name || '--'}</p>
    </div>
    <div className="flex items-start gap-2">
      <Badge variant={actionVariant(event.action)}>{actionLabels[event.action] || event.action}</Badge>
    </div>
    <div className="text-sm text-secondary-text">{sourceLabel(event.source)}</div>
    <div>
      <p className="text-sm leading-relaxed text-foreground">{event.reason || '--'}</p>
      <p className="mt-1 text-xs text-muted-text">{formatDateTime(event.createdAt)}</p>
    </div>
  </div>
);

const DiscoveryPage: React.FC = () => {
  const [items, setItems] = useState<DiscoveryItem[]>([]);
  const [events, setEvents] = useState<DiscoveryEvent[]>([]);
  const [source, setSource] = useState('');
  const [market, setMarket] = useState('');
  const [loading, setLoading] = useState(true);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [lastPriceRefreshAt, setLastPriceRefreshAt] = useState<string | null>(null);

  const load = useCallback(async (silent = false, refreshQuotes = false) => {
    if (!silent) setLoading(true);
    if (!silent) setError(null);
    try {
      const [listResp, historyResp] = await Promise.all([
        discoveryApi.list({ source, market, limit: 120, refresh: refreshQuotes }),
        discoveryApi.history({ limit: 160 }),
      ]);
      setItems(listResp.items);
      setEvents(historyResp.events);
      if (refreshQuotes) {
        setLastPriceRefreshAt(latestQuoteFetchedAt(listResp.items) ?? new Date().toISOString());
      }
    } catch (err) {
      const parsed = getParsedApiError(err);
      if (!silent) {
        setError(parsed.message ? parsed : createParsedApiError({
          title: '加载失败',
          message: '加载淘金列表失败',
          rawMessage: String(err),
        }));
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [market, source]);

  useEffect(() => {
    let lastRefresh = Date.now();
    void (async () => {
      await load();
      void load(true, true);
    })();
    const refreshIfVisible = () => {
      if (document.hidden) return;
      const now = Date.now();
      if (now - lastRefresh < REFRESH_POLICY_MS.realtimeQuoteMinGap) return;
      lastRefresh = now;
      void load(true, true);
    };
    const handleVisibilityChange = () => {
      if (!document.hidden) refreshIfVisible();
    };
    const timer = window.setInterval(refreshIfVisible, REFRESH_POLICY_MS.realtimeQuoteFallback);
    window.addEventListener('focus', refreshIfVisible);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshIfVisible);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [load]);

  const rejectItem = useCallback(async (item: DiscoveryItem) => {
    setRejectingId(item.id);
    setError(null);
    try {
      await discoveryApi.reject(item.id);
      await load();
    } catch (err) {
      const parsed = getParsedApiError(err);
      setError(parsed.message ? parsed : createParsedApiError({
        title: '移出失败',
        message: `移出 ${item.ticker} 失败`,
        rawMessage: String(err),
      }));
    } finally {
      setRejectingId(null);
    }
  }, [load]);

  const activeCount = items.length;
  const shortCount = items.filter((item) => item.tradeHorizon === 'short').length;
  const autoTradeCount = items.filter((item) => item.allowAutoTrade).length;

  return (
    <AppPage>
      <PageHeader
        eyebrow="Discovery"
        title="淘金列表"
        description={`查看 Scanner、沙里淘金和热点雷达加入的候选股票，并追踪加入、过期和移出的原因。${lastPriceRefreshAt ? `行情数据 ${formatTime(lastPriceRefreshAt)} 更新` : ''}`}
        actions={
          <Button type="button" variant="settings-primary" onClick={() => void load(false, true)} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        }
      />

      {error ? (
        <InlineAlert
          className="mt-4"
          variant="danger"
          title={error.title}
          message={error.message}
        />
      ) : null}

      <section className="mt-5 grid gap-3 md:grid-cols-3">
        <Card padding="sm">
          <p className="text-xs text-muted-text">活跃候选</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{activeCount}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">短线候选</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{shortCount}</p>
        </Card>
        <Card padding="sm">
          <p className="text-xs text-muted-text">允许自动交易</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{autoTradeCount}</p>
        </Card>
      </section>

      <Card padding="md" className="mt-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <SlidersHorizontal className="h-4 w-4 text-cyan" />
            筛选
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              className="settings-input min-w-36"
              value={source}
              onChange={(event) => setSource(event.target.value)}
            >
              <option value="">全部来源</option>
              <option value="scanner">扫股</option>
              <option value="gold_digger">沙里淘金</option>
              <option value="heat_radar">热点雷达</option>
            </select>
            <select
              className="settings-input min-w-32"
              value={market}
              onChange={(event) => setMarket(event.target.value)}
            >
              <option value="">全部市场</option>
              <option value="US">US</option>
              <option value="CN">CN</option>
            </select>
          </div>
        </div>
      </Card>

      <section className="mt-5">
        <div className="mb-3 flex items-center gap-2">
          <Gem className="h-5 w-5 text-cyan" />
          <h2 className="text-lg font-semibold text-foreground">当前列表</h2>
        </div>
        {loading ? (
          <Card padding="lg">
            <div className="flex items-center justify-center gap-3 py-12 text-secondary-text">
              <RefreshCw className="h-5 w-5 animate-spin text-cyan" />
              正在加载淘金列表…
            </div>
          </Card>
        ) : items.length ? (
          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {items.map((item) => (
              <DiscoveryCard
                key={item.id}
                item={item}
                rejecting={rejectingId === item.id}
                onReject={rejectItem}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Gem className="h-8 w-8" />}
            title="暂无活跃淘金候选"
            description="运行扫股、沙里淘金或热点雷达后，符合条件的候选会自动进入这里。"
          />
        )}
      </section>

      <section className="mt-7">
        <div className="mb-3 flex items-center gap-2">
          <History className="h-5 w-5 text-cyan" />
          <h2 className="text-lg font-semibold text-foreground">变更历史与原因</h2>
        </div>
        <Card padding="none" className="overflow-hidden">
          {events.length ? (
            events.map((event) => <HistoryRow key={event.id} event={event} />)
          ) : (
            <div className="px-4 py-10">
              <EmptyState
                icon={<Clock3 className="h-8 w-8" />}
                title="暂无变更历史"
                description="新增、过期或手动移出淘金候选后，会在这里记录原因。"
              />
            </div>
          )}
        </Card>
      </section>
    </AppPage>
  );
};

export default DiscoveryPage;
