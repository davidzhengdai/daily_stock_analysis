import type React from 'react';
import { useState, useEffect, useCallback } from 'react';
import {
  Newspaper,
  RefreshCw,
  AlertTriangle,
  TrendingUp,
  Layers,
  Activity,
  ExternalLink,
  CheckCircle,
  XCircle,
} from 'lucide-react';
import {
  AppPage,
  PageHeader,
  SectionCard,
  Card,
  Badge,
  Button,
  EmptyState,
  InlineAlert,
  Loading,
} from '../components/common';
import { sentinelApi } from '../api/sentinel';
import type {
  SentinelStatus,
  SentinelNewsItem,
  SentinelAnalysisItem,
  SentinelTheme,
  SentinelSectorOpp,
  SentinelStockLead,
} from '../types/sentinel';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return ts;
  }
}

function priorityVariant(p: number | null): 'danger' | 'warning' | 'default' {
  if (p === 5) return 'danger';
  if (p === 4) return 'warning';
  return 'default';
}

function marketMoodVariant(mood: string): 'success' | 'danger' | 'default' {
  if (!mood) return 'default';
  if (mood.includes('乐观')) return 'success';
  if (mood.includes('悲观')) return 'danger';
  return 'default';
}

function sentimentIcon(sentiment: string | null): React.ReactNode {
  if (!sentiment) return null;
  const s = sentiment.toLowerCase();
  if (s === 'positive' || s === '正面') return <TrendingUp className="h-3.5 w-3.5 text-success" />;
  if (s === 'negative' || s === '负面') return <AlertTriangle className="h-3.5 w-3.5 text-danger" />;
  return <Activity className="h-3.5 w-3.5 text-secondary-text" />;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const StatusCard: React.FC<{ status: SentinelStatus }> = ({ status }) => (
  <Card padding="md" className="flex flex-wrap gap-6 items-center">
    <div className="flex items-center gap-2">
      {status.enabled ? (
        <CheckCircle className="h-5 w-5 text-success" />
      ) : (
        <XCircle className="h-5 w-5 text-danger" />
      )}
      <Badge variant={status.enabled ? 'success' : 'danger'}>
        {status.enabled ? '已启用' : '未启用'}
      </Badge>
    </div>
    <div className="text-center">
      <p className="text-xs text-muted-text">新闻总量</p>
      <p className="font-mono font-bold text-foreground">{status.totalItems.toLocaleString()}</p>
    </div>
    <div className="text-center">
      <p className="text-xs text-muted-text">待分类</p>
      <p className="font-mono font-bold text-warning">{status.unclassifiedCount.toLocaleString()}</p>
    </div>
    <div className="text-center">
      <p className="text-xs text-muted-text">关注股票</p>
      <p className="font-mono font-bold text-cyan">{status.watchedStocksCount}</p>
    </div>
    <div className="text-center">
      <p className="text-xs text-muted-text">上次综合分析</p>
      <p className="text-sm text-foreground">{formatTimestamp(status.lastAnalysisAt)}</p>
    </div>
  </Card>
);

const AnalysisCard: React.FC<{ analysis: SentinelAnalysisItem | null }> = ({ analysis }) => {
  if (!analysis) {
    return (
      <SectionCard title="最新综合分析">
        <p className="text-sm text-secondary-text">暂无综合分析</p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="最新综合分析">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <span className="text-xs text-muted-text">{formatTimestamp(analysis.cycleAt)}</span>
        <Badge variant="default" size="sm">分析了 {analysis.newsCount ?? 0} 条新闻</Badge>
        {analysis.marketMood && (
          <Badge variant={marketMoodVariant(analysis.marketMood)}>
            市场情绪：{analysis.marketMood}
          </Badge>
        )}
      </div>

      {/* Themes */}
      {analysis.themes.length > 0 && (
        <div className="mb-4">
          <p className="mb-2 text-xs font-medium text-secondary-text flex items-center gap-1">
            <Layers className="h-3.5 w-3.5" /> 主要主题
          </p>
          <div className="flex flex-wrap gap-2">
            {analysis.themes.map((theme: SentinelTheme, i: number) => (
              <div
                key={`theme-${i}`}
                className="rounded-lg border border-border/50 bg-card/40 px-3 py-1.5 text-xs"
              >
                <span className="font-medium text-foreground">{theme.theme}</span>
                {theme.confidence > 0 && (
                  <span className="ml-1.5 text-muted-text">
                    {Math.round(theme.confidence * 100)}%
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sector opportunities */}
      {analysis.sectorOpps.length > 0 && (
        <div className="mb-4">
          <p className="mb-2 text-xs font-medium text-secondary-text flex items-center gap-1">
            <TrendingUp className="h-3.5 w-3.5" /> 板块机会
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {analysis.sectorOpps.map((opp: SentinelSectorOpp, i: number) => (
              <div
                key={`opp-${i}`}
                className="rounded-lg border border-border/50 bg-card/40 p-2 text-xs"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-medium text-foreground">{opp.sector}</span>
                  <Badge variant="success" size="sm">{opp.signal}</Badge>
                </div>
                {opp.reason && <p className="text-muted-text">{opp.reason}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stock leads */}
      {analysis.stockLeads.length > 0 && (
        <div className="mb-4">
          <p className="mb-2 text-xs font-medium text-secondary-text">关注个股</p>
          <div className="flex flex-wrap gap-2">
            {analysis.stockLeads.map((lead: SentinelStockLead, i: number) => (
              <div
                key={`lead-${i}`}
                className="rounded-lg border border-border/40 bg-primary/5 px-3 py-1.5 text-xs"
              >
                <span className="font-mono font-bold text-primary">{lead.code}</span>
                {lead.name && <span className="ml-1.5 text-secondary-text">{lead.name}</span>}
                {lead.confidence > 0 && (
                  <span className="ml-1.5 text-muted-text">
                    {Math.round(lead.confidence * 100)}%
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Risk alerts */}
      {analysis.riskAlerts.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-danger flex items-center gap-1">
            <AlertTriangle className="h-3.5 w-3.5" /> 风险提示
          </p>
          <ul className="space-y-1">
            {analysis.riskAlerts.map((alert: string, i: number) => (
              <li key={`alert-${i}`} className="flex gap-2 text-xs text-secondary-text">
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-danger/70" />
                {alert}
              </li>
            ))}
          </ul>
        </div>
      )}
    </SectionCard>
  );
};

const NewsCard: React.FC<{ item: SentinelNewsItem }> = ({ item }) => (
  <div className="rounded-xl border border-border/50 bg-card/60 p-4 hover:bg-card/80 transition-colors">
    <div className="mb-2 flex flex-wrap items-start gap-2">
      <Badge variant={priorityVariant(item.priority)} size="sm">
        P{item.priority ?? '?'}
      </Badge>
      {item.category && <Badge variant="default" size="sm">{item.category}</Badge>}
      {item.marketScope && <Badge variant="default" size="sm">{item.marketScope}</Badge>}
      <div className="ml-auto flex items-center gap-1 text-xs text-muted-text">
        {sentimentIcon(item.sentiment)}
        {item.sentiment && <span>{item.sentiment}</span>}
      </div>
    </div>

    <a
      href={item.url || undefined}
      target="_blank"
      rel="noreferrer"
      className="group flex items-start gap-1 hover:text-cyan transition-colors"
    >
      <span className="text-sm font-medium text-foreground group-hover:text-cyan leading-snug">
        {item.title}
      </span>
      {item.url && (
        <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-text group-hover:text-cyan" />
      )}
    </a>

    <div className="mt-1 flex items-center gap-3 text-xs text-muted-text">
      <span>{item.sourceName}</span>
      <span>{formatTimestamp(item.publishedAt || item.fetchedAt)}</span>
    </div>

    {item.llmReasoning && (
      <p className="mt-2 text-xs text-secondary-text line-clamp-2">{item.llmReasoning}</p>
    )}
  </div>
);

// ---------------------------------------------------------------------------
// Priority filter buttons
// ---------------------------------------------------------------------------

const PRIORITY_OPTIONS = [
  { label: '全部', value: 1 },
  { label: 'P3+', value: 3 },
  { label: 'P4+', value: 4 },
  { label: 'P5', value: 5 },
];

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const SentinelPage: React.FC = () => {
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [news, setNews] = useState<SentinelNewsItem[]>([]);
  const [latestAnalysis, setLatestAnalysis] = useState<SentinelAnalysisItem | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [priorityMin, setPriorityMin] = useState(3);

  const fetchAll = useCallback(async (pMin = priorityMin) => {
    setIsLoading(true);
    setError(null);
    try {
      const [statusRes, newsRes, analysesRes] = await Promise.all([
        sentinelApi.getStatus(),
        sentinelApi.getNews({ hours: 48, priorityMin: pMin, limit: 50 }),
        sentinelApi.getAnalyses(1),
      ]);
      setStatus(statusRes);
      setNews(newsRes);
      setLatestAnalysis(analysesRes.length > 0 ? analysesRes[0] : null);
    } catch (e) {
      setError(getParsedApiError(e));
    } finally {
      setIsLoading(false);
    }
  }, [priorityMin]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // Auto-refresh every 5 minutes
  useEffect(() => {
    const timer = setInterval(() => {
      void fetchAll();
    }, 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [fetchAll]);

  const handlePriorityChange = (p: number) => {
    setPriorityMin(p);
    void fetchAll(p);
  };

  const totalItems = status?.totalItems ?? 0;
  const subtitle = status
    ? `${status.enabled ? '已启用' : '未启用'} · 缓存 ${totalItems.toLocaleString()} 条新闻`
    : '加载中…';

  return (
    <AppPage>
      <PageHeader
        eyebrow="实时情报"
        title="情报中心"
        description={subtitle}
        actions={
          <Button
            variant="secondary"
            size="sm"
            isLoading={isLoading}
            loadingText="刷新中…"
            onClick={() => { void fetchAll(); }}
          >
            <RefreshCw className="mr-1.5 h-4 w-4" />
            刷新
          </Button>
        }
      />

      {error && (
        <div className="mt-3">
          <InlineAlert variant="danger" message={error.message} />
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-16">
          <Loading />
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {/* Status */}
          {status && <StatusCard status={status} />}

          {/* Latest analysis */}
          <AnalysisCard analysis={latestAnalysis} />

          {/* News feed */}
          <SectionCard title="近期高优先级新闻">
            {/* Priority filter */}
            <div className="mb-4 flex flex-wrap gap-2">
              {PRIORITY_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => handlePriorityChange(opt.value)}
                  className={`rounded-lg border px-3 py-1 text-xs transition-colors ${
                    priorityMin === opt.value
                      ? 'border-cyan/50 bg-cyan/10 text-cyan font-medium'
                      : 'border-border/50 bg-card text-secondary-text hover:bg-hover'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            {news.length === 0 ? (
              <EmptyState
                icon={<Newspaper className="h-8 w-8" />}
                title="暂无新闻"
                description="当前筛选条件下没有找到相关新闻，请调整优先级过滤或等待新数据到来。"
              />
            ) : (
              <div className="space-y-3">
                {news.map((item) => (
                  <NewsCard key={`${item.id}-${item.fetchedAt}`} item={item} />
                ))}
              </div>
            )}
          </SectionCard>
        </div>
      )}
    </AppPage>
  );
};

export default SentinelPage;
