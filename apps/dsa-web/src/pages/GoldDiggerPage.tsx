import type React from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Gem,
  Play,
  RefreshCw,
  Clock,
  ChevronDown,
  ChevronUp,
  Sparkles,
  AlertTriangle,
  TrendingUp,
  Layers,
} from 'lucide-react';
import {
  AppPage,
  PageHeader,
  SectionCard,
  Button,
  Badge,
  InlineAlert,
  EmptyState,
  Loading,
} from '../components/common';
import { goldDiggerApi } from '../api/goldDigger';
import { watchlistApi } from '../api/watchlist';
import { FavoriteStockButton } from '../components/watchlist/FavoriteStockButton';
import type { DigReport, GoldPick, DigMeta, InvestmentTheme } from '../types/goldDigger';
import { getParsedApiError, createParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function translateDigMessage(message: string): string {
  if (!message) return '';

  const replacements: Array<[RegExp, string]> = [
    [/^Starting…$/i, '启动中…'],
    [/^Fetching macro\/finance\/political news…$/i, '正在获取宏观、金融与政策新闻…'],
    [/^Synthesizing investment themes from news…$/i, '正在从新闻中提炼投资主题…'],
    [/^Loading US small-cap universe…$/i, '正在加载美股小盘股股票池…'],
    [/^Filtering US garbage stocks \(6-month price history\)…$/i, '正在按 6 个月价格历史筛选美股低位候选…'],
    [/^Enriching (\d+) US candidates with fundamentals…$/i, '正在补充 $1 只美股候选的基本面数据…'],
    [/^Loading A-share universe…$/i, '正在加载 A 股股票池…'],
    [/^Filtering A-share garbage stocks…$/i, '正在筛选 A 股低位候选…'],
    [/^Scoring (\d+) candidates against (\d+) themes…$/i, '正在根据 $2 个主题为 $1 只候选评分…'],
    [/^AI deep analysis of (\d+) candidates…$/i, '正在对 $1 只候选进行 AI 深度分析…'],
    [/^AI analysis (\d+)\/(\d+)…$/i, 'AI 深度分析 $1/$2…'],
    [/^Completed — (\d+) gold picks found$/i, '扫描完成，找到 $1 只金股'],
    [/^Failed: (.+)$/i, '扫描失败：$1'],
  ];

  for (const [pattern, replacement] of replacements) {
    if (pattern.test(message)) return message.replace(pattern, replacement);
  }
  return message;
}

const SentimentBadge: React.FC<{ sentiment: string }> = ({ sentiment }) => {
  if (sentiment === 'bullish') return <Badge variant="success" size="sm">看多</Badge>;
  if (sentiment === 'bearish') return <Badge variant="danger" size="sm">看空</Badge>;
  return <Badge variant="default" size="sm">中性</Badge>;
};

const MarketBadge: React.FC<{ market: string }> = ({ market }) => (
  <Badge variant={market === 'US' ? 'default' : 'warning'} size="sm">
    {market === 'US' ? '美股' : 'A股'}
  </Badge>
);

function splitPicksByMarket(picks: GoldPick[]) {
  return {
    us: picks.filter((pick) => pick.market === 'US'),
    cn: picks.filter((pick) => pick.market !== 'US'),
  };
}

const ThemeCard: React.FC<{ theme: InvestmentTheme; index: number }> = ({ theme, index }) => (
  <div className="rounded-lg border border-border/60 bg-card/40 p-3">
    <div className="mb-1 flex items-center gap-2">
      <span className="text-xs text-secondary-text">#{index + 1}</span>
      <span className="text-sm font-medium text-foreground">{theme.name}</span>
      <SentimentBadge sentiment={theme.sentiment} />
    </div>
    <p className="mb-2 text-xs text-secondary-text">{theme.description}</p>
    <div className="flex flex-wrap gap-1">
      {theme.keywords.slice(0, 6).map((kw) => (
        <span key={kw} className="rounded bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">
          {kw}
        </span>
      ))}
    </div>
  </div>
);

const DigProgress: React.FC<{ progress: number; message: string }> = ({ progress, message }) => (
  <div className="space-y-2">
    <div className="flex items-center justify-between text-sm">
      <span className="text-secondary-text">{translateDigMessage(message)}</span>
      <span className="font-medium text-foreground">{progress}%</span>
    </div>
    <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
      <div
        className="h-full rounded-full bg-primary transition-all duration-500"
        style={{ width: `${progress}%` }}
      />
    </div>
  </div>
);

const FunnelBar: React.FC<{ report: DigReport }> = ({ report }) => {
  const f = report.funnel;
  const steps = [
    { label: '美股宇宙', value: f.usUniverse },
    { label: 'A股宇宙', value: f.cnUniverse },
    { label: '垃圾筛选', value: f.garbageFiltered },
    { label: '主题匹配', value: f.themeMatched },
    { label: 'AI分析', value: f.deepAnalyzed },
    { label: '金股', value: f.goldPicks },
  ];
  const max = Math.max(...steps.map((s) => s.value), 1);
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {steps.map((step) => (
        <div key={step.label} className="flex flex-col items-center gap-1">
          <div className="flex h-12 w-full items-end justify-center">
            <div
              className="w-full rounded-t bg-primary/60 transition-all"
              style={{ height: `${Math.max(4, (step.value / max) * 48)}px` }}
            />
          </div>
          <p className="text-center text-xs font-medium tabular-nums text-foreground">
            {step.value.toLocaleString()}
          </p>
          <p className="text-center text-[10px] text-secondary-text">{step.label}</p>
        </div>
      ))}
    </div>
  );
};

const PickCard: React.FC<{
  pick: GoldPick;
  isWatched: boolean;
  onToggleWatchlist: (code: string, name: string) => void;
}> = ({ pick, isWatched, onToggleWatchlist }) => {
  const [expanded, setExpanded] = useState(false);
  const changeColor = pick.priceChange6mPct < 0 ? 'text-danger' : 'text-success';
  const changeSign = pick.priceChange6mPct > 0 ? '+' : '';

  return (
    <div className="rounded-xl border border-border/60 bg-card/60 shadow-sm">
      {/* Header */}
      <div
        className="flex cursor-pointer items-center gap-3 p-4"
        onClick={() => setExpanded((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded((v) => !v)}
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-bold text-primary">
          {pick.rank}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-foreground">{pick.ticker}</span>
            <span className="truncate text-sm text-secondary-text">{pick.name}</span>
            <MarketBadge market={pick.market} />
            {pick.sector && <Badge variant="default" size="sm">{pick.sector}</Badge>}
            {pick.industry && <Badge variant="default" size="sm">行业：{pick.industry}</Badge>}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-3 text-xs text-secondary-text">
            <span>{pick.market === 'US' ? '$' : '¥'}{pick.currentPrice.toFixed(2)}</span>
            <span className={changeColor}>
              {changeSign}{pick.priceChange6mPct.toFixed(1)}%（6个月）
            </span>
            {pick.peRatio != null && <span>PE {pick.peRatio.toFixed(1)}</span>}
            {pick.peDiscountPct != null && (
              <span className="text-success">
                低于行业 {Math.abs(pick.peDiscountPct).toFixed(0)}%
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-xs text-secondary-text">综合评分</span>
          <span className="text-lg font-bold text-primary">{pick.compositeScore.toFixed(0)}</span>
          <span className="text-[10px] text-secondary-text">AI置信度 {pick.llmConfidence}%</span>
        </div>
        <FavoriteStockButton
          isWatched={isWatched}
          onToggle={() => onToggleWatchlist(pick.ticker, pick.name || pick.ticker)}
        />
        {expanded ? (
          <ChevronUp className="h-4 w-4 shrink-0 text-secondary-text" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0 text-secondary-text" />
        )}
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-border/40 p-4 space-y-4">
          {/* Matched themes */}
          {pick.matchedThemes.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium text-secondary-text">匹配主题</p>
              <div className="flex flex-wrap gap-1">
                {pick.matchedThemes.map((t) => (
                  <span key={t} className="rounded bg-primary/10 px-2 py-0.5 text-xs text-primary">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Why garbage / why gold */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-border/50 bg-danger/5 p-3">
              <div className="mb-1 flex items-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 text-danger" />
                <span className="text-xs font-medium text-danger">为何被市场忽视</span>
              </div>
              <p className="text-xs text-secondary-text">{pick.whyGarbage}</p>
            </div>
            <div className="rounded-lg border border-border/50 bg-success/5 p-3">
              <div className="mb-1 flex items-center gap-1.5">
                <Gem className="h-3.5 w-3.5 text-success" />
                <span className="text-xs font-medium text-success">隐藏价值</span>
              </div>
              <p className="text-xs text-secondary-text">{pick.whyGold}</p>
            </div>
          </div>

          {/* Summary */}
          {pick.analysisSummary && (
            <div>
              <p className="mb-1 text-xs font-medium text-secondary-text">投资摘要</p>
              <p className="text-xs text-foreground/80">{pick.analysisSummary}</p>
            </div>
          )}

          {/* Catalysts / Risks */}
          <div className="grid gap-3 sm:grid-cols-2">
            {pick.keyCatalysts && (
              <div>
                <p className="mb-1 text-xs font-medium text-secondary-text">关键催化剂</p>
                <p className="text-xs text-foreground/80">{pick.keyCatalysts}</p>
              </div>
            )}
            {pick.keyRisks && (
              <div>
                <p className="mb-1 text-xs font-medium text-secondary-text">主要风险</p>
                <p className="text-xs text-foreground/80">{pick.keyRisks}</p>
              </div>
            )}
          </div>

          {/* Entry strategy */}
          {pick.entryStrategy && (
            <div className="rounded-lg border border-primary/20 bg-primary/5 p-3">
              <p className="mb-1 text-xs font-medium text-primary">入场策略</p>
              <p className="text-xs text-foreground/80">{pick.entryStrategy}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const HistoryList: React.FC<{
  history: DigMeta[];
  onLoadRun: (runId: string) => void;
  activeRunId?: string;
}> = ({ history, onLoadRun, activeRunId }) => {
  if (history.length === 0) {
    return <p className="text-sm text-secondary-text">暂无历史记录</p>;
  }
  return (
    <div className="space-y-2">
      {history.map((meta) => (
        <button
          key={meta.runId}
          type="button"
          onClick={() => onLoadRun(meta.runId)}
          className={cn(
            'flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors hover:bg-hover',
            activeRunId === meta.runId
              ? 'border-primary/40 bg-primary/5'
              : 'border-border/50 bg-card/40',
          )}
        >
          <Badge
            variant={meta.status === 'completed' ? 'success' : 'danger'}
            size="sm"
          >
            {meta.status === 'completed' ? '完成' : '失败'}
          </Badge>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium text-foreground">
              {meta.topTicker} {meta.topName && `— ${meta.topName}`}
            </p>
            <p className="text-[11px] text-secondary-text">
              {meta.timestamp} · {meta.goldPicks}只金股 · {meta.themeCount}个主题
            </p>
          </div>
          <span className="shrink-0 text-[11px] text-secondary-text">
            {(meta.durationS / 60).toFixed(0)}分钟
          </span>
        </button>
      ))}
    </div>
  );
};

const MARKET_OPTIONS = [
  { id: 'us', label: '美股' },
  { id: 'cn', label: 'A股' },
];

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const GoldDiggerPage: React.FC = () => {
  const [isRunning, setIsRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState('');
  const [report, setReport] = useState<DigReport | null>(null);
  const [history, setHistory] = useState<DigMeta[]>([]);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingResult, setLoadingResult] = useState(false);
  const [markets, setMarkets] = useState<string[]>(['us', 'cn']);
  const [topN, setTopN] = useState(10);
  const [maxTier5PerMarket, setMaxTier5PerMarket] = useState(15);
  const [chinaPolicyWeight, setChinaPolicyWeight] = useState(0.25);
  const [watchedCodes, setWatchedCodes] = useState<Set<string>>(new Set());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true);
    try {
      const h = await goldDiggerApi.listHistory();
      setHistory(h);
    } catch {
      // history load failure is non-critical
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  const loadResult = useCallback(async (runId?: string) => {
    setLoadingResult(true);
    setError(null);
    try {
      const r = runId
        ? await goldDiggerApi.getResult(runId)
        : await goldDiggerApi.getLatestResult();
      setReport(r);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoadingResult(false);
    }
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const res = await watchlistApi.listAll();
      setWatchedCodes(new Set(res.items.map((item) => item.code.toUpperCase())));
    } catch {
      // Watchlist state is non-critical for gold pick rendering.
    }
  }, []);

  useEffect(() => {
    void loadHistory();
    void loadResult().catch(() => {});
    void loadWatchlist();
  }, [loadHistory, loadResult, loadWatchlist]);

  const handleToggleWatchlist = useCallback((code: string, name: string) => {
    const normalizedCode = code.trim().toUpperCase();
    if (!normalizedCode) return;

    if (watchedCodes.has(normalizedCode)) {
      watchlistApi.remove(normalizedCode).then(() => {
        setWatchedCodes((prev) => {
          const next = new Set(prev);
          next.delete(normalizedCode);
          return next;
        });
      }).catch(() => {
        // Keep current UI state when the watchlist update fails.
      });
      return;
    }

    watchlistApi.add(normalizedCode, name).then(() => {
      setWatchedCodes((prev) => new Set([...prev, normalizedCode]));
    }).catch(() => {
      // Keep current UI state when the watchlist update fails.
    });
  }, [watchedCodes]);

  const startPoll = useCallback((runId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await goldDiggerApi.getStatus(runId);
        setProgress(s.progress);
        setProgressMsg(s.message);
        if (s.status === 'completed') {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setIsRunning(false);
          await loadResult(runId);
          await loadHistory();
        } else if (s.status === 'error') {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setIsRunning(false);
          setError(
            createParsedApiError({ title: '扫描失败', message: s.message ?? '未知错误', status: 500 }),
          );
        }
      } catch {
        // transient poll error, keep polling
      }
    }, 5000);
  }, [loadResult, loadHistory]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleStartDig = async () => {
    setError(null);
    setIsRunning(true);
    setProgress(0);
    setProgressMsg('启动中…');
    try {
      const res = await goldDiggerApi.startDig({
        markets,
        topN,
        maxTier5PerMarket,
        chinaPolicyWeight,
      });
      startPoll(res.runId);
    } catch (err) {
      setIsRunning(false);
      setError(getParsedApiError(err));
    }
  };

  const handleLoadRun = async (runId: string) => {
    await loadResult(runId);
  };

  const toggleMarket = (market: string) => {
    const next = markets.includes(market)
      ? markets.filter((m) => m !== market)
      : [...markets, market];
    if (next.length > 0) setMarkets(next);
  };

  const renderPickGroup = (title: string, picks: GoldPick[]) => {
    if (picks.length === 0) return null;
    return (
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="label-uppercase">{title}</h3>
          <Badge variant="default" size="sm">{picks.length} 只</Badge>
        </div>
        <div className="space-y-3">
          {picks.map((pick) => (
            <PickCard
              key={pick.ticker}
              pick={pick}
              isWatched={watchedCodes.has(pick.ticker.toUpperCase())}
              onToggleWatchlist={handleToggleWatchlist}
            />
          ))}
        </div>
      </section>
    );
  };

  return (
    <AppPage>
      <PageHeader
        title="沙里淘金"
        description="从美股与 A 股垃圾股中发掘被市场忽视的隐藏价值，并对 A 股候选加入中国政策与国家热点权重。"
        eyebrow="低位价值挖掘"
      />

      <div className="space-y-6">
        {/* Control */}
        <SectionCard title="扫描配置">
          <div className="flex flex-wrap items-center gap-4">
            <Button
              onClick={() => void handleStartDig()}
              disabled={isRunning}
              variant="primary"
              size="md"
            >
              {isRunning ? (
                <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Play className="mr-2 h-4 w-4" />
              )}
              {isRunning ? '扫描中…' : '开始淘金'}
            </Button>
            <div className="text-sm text-secondary-text">
              扫描所选市场，匹配宏观主题，AI深度分析找出隐藏金股
            </div>
          </div>
          <div className="mt-4 grid gap-3 border-t border-border/40 pt-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <span className="mb-1 block text-xs text-secondary-text">扫描市场</span>
              <div className="flex flex-wrap gap-3">
                {MARKET_OPTIONS.map((option) => (
                  <label key={option.id} className="inline-flex items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={markets.includes(option.id)}
                      onChange={() => toggleMarket(option.id)}
                      className="h-4 w-4 rounded border-border bg-card accent-primary"
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs text-secondary-text">推荐数量</label>
              <input
                type="number"
                min={1}
                max={30}
                value={topN}
                onChange={(e) => setTopN(Number(e.target.value))}
                className="w-full rounded-lg border border-border/60 bg-card px-3 py-1.5 text-sm text-foreground focus:border-primary/50 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-secondary-text">每市场AI候选</label>
              <input
                type="number"
                min={5}
                max={50}
                value={maxTier5PerMarket}
                onChange={(e) => setMaxTier5PerMarket(Number(e.target.value))}
                className="w-full rounded-lg border border-border/60 bg-card px-3 py-1.5 text-sm text-foreground focus:border-primary/50 focus:outline-none"
              />
            </div>
            {markets.includes('cn') && (
              <div>
                <label className="mb-1 block text-xs text-secondary-text">中国政策热点权重</label>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={chinaPolicyWeight}
                  onChange={(e) => setChinaPolicyWeight(Number(e.target.value))}
                  className="w-full rounded-lg border border-border/60 bg-card px-3 py-1.5 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                />
              </div>
            )}
          </div>
          {isRunning && (
            <div className="mt-4">
              <DigProgress progress={progress} message={progressMsg} />
            </div>
          )}
          {error && (
            <div className="mt-4">
              <InlineAlert variant="danger" message={error.message} />
            </div>
          )}
        </SectionCard>

        {/* Results */}
        {loadingResult ? (
          <div className="flex justify-center py-12">
            <Loading />
          </div>
        ) : report ? (
          <>
            {/* Report header */}
            <SectionCard title="扫描概览">
              <div className="mb-4 flex flex-wrap items-center gap-3">
                <Sparkles className="h-5 w-5 text-primary" />
                <h2 className="text-base font-semibold text-foreground">淘金结果</h2>
                <Badge variant="default" size="sm">{report.timestamp}</Badge>
                <Badge variant="success" size="sm">{report.goldPicks.length} 只金股</Badge>
                <span className="ml-auto flex items-center gap-1 text-xs text-secondary-text">
                  <Clock className="h-3.5 w-3.5" />
                  耗时 {(report.durationS / 60).toFixed(1)} 分钟
                </span>
              </div>

              {/* Funnel */}
              <div className="mb-4">
                <p className="mb-2 text-xs text-secondary-text flex items-center gap-1">
                  <Layers className="h-3.5 w-3.5" /> 筛选漏斗
                </p>
                <FunnelBar report={report} />
              </div>

              {/* Themes */}
              {report.detectedThemes.length > 0 && (
                <div>
                  <p className="mb-2 text-xs text-secondary-text flex items-center gap-1">
                    <TrendingUp className="h-3.5 w-3.5" /> 检测到的宏观主题 ({report.detectedThemes.length})
                  </p>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {report.detectedThemes.map((theme, i) => (
                      <ThemeCard key={theme.name} theme={theme} index={i} />
                    ))}
                  </div>
                </div>
              )}
            </SectionCard>

            {/* Gold picks */}
            {report.goldPicks.length > 0 ? (
              <SectionCard title="金股推荐">
                <div className="space-y-5">
                  {renderPickGroup('美股金股', splitPicksByMarket(report.goldPicks).us)}
                  {renderPickGroup('A股金股', splitPicksByMarket(report.goldPicks).cn)}
                </div>
              </SectionCard>
            ) : (
              <EmptyState
                icon={<Gem className="h-8 w-8" />}
                title="本次未找到金股"
                description="请稍后重试或调整筛选参数"
              />
            )}
          </>
        ) : (
          <EmptyState
            icon={<Gem className="h-8 w-8" />}
            title="暂无淘金记录"
            description="点击「开始淘金」启动扫描"
          />
        )}

        {/* History */}
        <SectionCard title="历史记录">
          {loadingHistory ? (
            <Loading />
          ) : (
            <HistoryList
              history={history}
              onLoadRun={(id) => void handleLoadRun(id)}
              activeRunId={report?.runId}
            />
          )}
        </SectionCard>
      </div>
    </AppPage>
  );
};

export default GoldDiggerPage;
