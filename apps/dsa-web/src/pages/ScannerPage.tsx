import type React from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Scan,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Clock,
  BarChart2,
  Globe,
  Newspaper,
  DollarSign,
  AlertTriangle,
  History,
} from 'lucide-react';
import {
  AppPage,
  PageHeader,
  Card,
  Badge,
  Button,
  EmptyState,
  InlineAlert,
} from '../components/common';
import { scannerApi } from '../api/scanner';
import type { ScanReport, StockPick, ScanMeta, ScanStatusResponse } from '../types/scanner';
import { getParsedApiError, createParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';

// ─── Helpers ────────────────────────────────────────────────────────────────

function scoreColor(score: number): string {
  if (score >= 75) return 'text-success';
  if (score >= 55) return 'text-cyan';
  if (score >= 40) return 'text-warning';
  return 'text-danger';
}

function decisionBadge(decision: string, signal: string) {
  const d = decision?.toLowerCase() ?? '';
  if (d === 'buy' || signal?.includes('买入')) {
    return <Badge variant="success" glow>买入</Badge>;
  }
  if (d === 'sell' || signal?.includes('卖出')) {
    return <Badge variant="danger">卖出</Badge>;
  }
  return <Badge variant="warning">持有</Badge>;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}分${s}秒`;
}

function formatTimestamp(ts: string): string {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function formatScanStatus(status: string): string {
  if (status === 'completed') return '完成';
  if (status === 'failed') return '失败';
  if (status === 'running') return '运行中';
  return status || '未知';
}

function translateScanMessage(message: string): string {
  if (!message) return '';

  const replacements: Array<[RegExp, string]> = [
    [/^Scan started$/i, '扫描已启动'],
    [/^Starting…$/i, '启动中…'],
    [/^Fetching stock universe…$/i, '正在获取股票池…'],
    [/^Universe: ([\d,]+) stocks\. Applying metadata filter…$/i, '股票池共 $1 只，正在执行基础筛选…'],
    [/^Tier 1 → ([\d,]+) stocks\. Running technical screen…$/i, '第一层筛选后剩余 $1 只，正在执行技术面筛选…'],
    [/^Technical screen batch (\d+)\/(\d+)$/i, '技术面筛选批次 $1/$2'],
    [/^US technical screen batch (\d+)\/(\d+)$/i, '美股技术面筛选批次 $1/$2'],
    [/^China technical screen (\d+)\/(\d+)$/i, 'A股技术面筛选 $1/$2'],
    [/^Tier 2 → (\d+) candidates\. Running fundamental screen…$/i, '第二层筛选后剩余 $1 只候选，正在执行基本面筛选…'],
    [/^Fundamental screen: fetching (\d+) stocks…$/i, '正在获取 $1 只股票的基本面数据…'],
    [/^Fundamentals (\d+)\/(\d+)$/i, '基本面筛选 $1/$2'],
    [/^Tier 3 → (\d+) candidates\. Applying sector filter…$/i, '第三层筛选后剩余 $1 只候选，正在执行行业分散筛选…'],
    [/^Applying China policy and hot-topic weighting…$/i, '正在应用中国政策与国家热点权重…'],
    [/^Applying sector diversity filter…$/i, '正在执行行业分散筛选…'],
    [/^Tier 4 → (\d+) diverse candidates\. Running LLM analysis…$/i, '第四层筛选后剩余 $1 只分散候选，正在执行 AI 深度分析…'],
    [/^AI analysis (\d+)\/(\d+)…$/i, 'AI 深度分析 $1/$2…'],
    [/^Analysis complete\. Building report…$/i, '分析完成，正在生成报告…'],
  ];

  for (const [pattern, replacement] of replacements) {
    if (pattern.test(message)) return message.replace(pattern, replacement);
  }
  return message;
}

// ─── Funnel Bar ─────────────────────────────────────────────────────────────

const FunnelBar: React.FC<{ label: string; value: number; max: number; color?: string }> = ({
  label, value, max, color = 'bg-cyan',
}) => {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 2;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-28 shrink-0 text-right text-xs text-secondary-text">{label}</span>
      <div className="relative flex-1 h-2 rounded-full bg-border/30">
        <div
          className={`${color} absolute left-0 top-0 h-2 rounded-full transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-16 text-right font-mono text-xs text-foreground">
        {value.toLocaleString()}
      </span>
    </div>
  );
};

// ─── Progress View ───────────────────────────────────────────────────────────

const ScanProgress: React.FC<{ status: ScanStatusResponse }> = ({ status }) => (
  <Card variant="gradient" padding="md" className="animate-fade-in">
    <div className="flex items-center gap-3 mb-4">
      <RefreshCw className="h-5 w-5 text-cyan animate-spin" />
      <span className="font-semibold text-foreground">正在扫描配置的市场…</span>
      <span className="ml-auto font-mono text-cyan text-lg">{status.progress}%</span>
    </div>
    <div className="w-full h-2 rounded-full bg-border/30 overflow-hidden">
      <div
        className="h-2 rounded-full bg-primary-gradient transition-all duration-500"
        style={{ width: `${status.progress}%` }}
      />
    </div>
    <p className="mt-3 text-sm text-secondary-text">{translateScanMessage(status.message)}</p>
    {status.startedAt ? (
      <p className="mt-1 text-xs text-muted-text flex items-center gap-1">
        <Clock className="h-3 w-3" />
        开始时间：{formatTimestamp(status.startedAt)}
      </p>
    ) : null}
    <p className="mt-4 text-xs text-muted-text">
      全量扫描通常需要 35–50 分钟，可离开本页，稍后返回查看结果。
    </p>
  </Card>
);

// ─── Thesis Section ──────────────────────────────────────────────────────────

const ThesisSection: React.FC<{
  icon: React.ReactNode;
  label: string;
  content: string;
}> = ({ icon, label, content }) => {
  if (!content || content === 'No data' || content.toLowerCase().includes('not available')) {
    return null;
  }
  return (
    <div className="border-t border-border/30 pt-3 mt-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-cyan">{icon}</span>
        <span className="label-uppercase">{label}</span>
      </div>
      <p className="text-sm text-secondary-text leading-relaxed whitespace-pre-wrap">{content}</p>
    </div>
  );
};

// ─── Pick Card ───────────────────────────────────────────────────────────────

const PickCard: React.FC<{ pick: StockPick }> = ({ pick }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="terminal-card p-5 animate-fade-in">
      {/* Header row */}
      <div className="flex flex-wrap items-start gap-3">
        {/* Rank + Ticker */}
        <div className="flex items-center gap-3 min-w-0">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-gradient font-bold text-sm text-primary-foreground shadow-lg shadow-cyan/20">
            #{pick.rank}
          </span>
          <div className="min-w-0">
            <span className="text-xl font-bold text-foreground">{pick.ticker}</span>
            <span className="ml-2 text-sm text-secondary-text truncate">{pick.name}</span>
          </div>
        </div>

        {/* Badges */}
        <div className="flex flex-wrap items-center gap-2 ml-auto">
          {decisionBadge(pick.llmDecision, pick.buySignal)}
          <Badge variant="default">{pick.sector}</Badge>
          <Badge variant={pick.market === 'cn' ? 'warning' : 'default'}>
            {pick.market === 'cn' ? 'A股' : '美股'}
          </Badge>
        </div>
      </div>

      {/* Score row */}
      <div className="mt-3 flex flex-wrap gap-4">
        <div className="flex items-center gap-2">
          <span className="label-uppercase">综合评分</span>
          <span className={`text-2xl font-bold font-mono ${scoreColor(pick.compositeScore)}`}>
            {pick.compositeScore.toFixed(0)}
          </span>
          <span className="text-xs text-muted-text">/100</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="label-uppercase">AI置信度</span>
          <span className={`text-2xl font-bold font-mono ${scoreColor(pick.llmConfidence)}`}>
            {pick.llmConfidence}
          </span>
          <span className="text-xs text-muted-text">/100</span>
        </div>
        {pick.currentPrice > 0 && (
          <div className="flex items-center gap-2">
            <span className="label-uppercase">价格</span>
            <span className="text-lg font-mono text-foreground">
              {pick.market === 'cn' ? '¥' : '$'}{pick.currentPrice.toFixed(2)}
            </span>
          </div>
        )}
      </div>

      {/* Summary */}
      {(pick.whySelected || pick.analysisSummary) && (
        <div className="mt-3 border-t border-border/30 pt-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-cyan"><Scan className="h-4 w-4" /></span>
            <span className="label-uppercase">入选理由</span>
          </div>
          <p className="text-sm text-secondary-text leading-relaxed">
            {pick.whySelected || pick.analysisSummary}
          </p>
          {pick.selectionFactors && pick.selectionFactors.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-muted-text">
              {pick.selectionFactors.map((factor) => (
                <li key={factor} className="flex gap-2">
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan/70" />
                  <span>{factor}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Expand toggle */}
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        className="mt-3 flex items-center gap-1.5 text-xs text-cyan hover:text-cyan/80 transition-colors"
      >
        {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        {expanded ? '收起投资论点' : '展开投资论点'}
      </button>

      {expanded && (
        <div className="mt-1">
          <ThesisSection
            icon={<BarChart2 className="h-4 w-4" />}
            label="历史财务健康度"
            content={pick.thesis.financialSummary}
          />
          <ThesisSection
            icon={<Newspaper className="h-4 w-4" />}
            label="行业新闻"
            content={pick.thesis.industryNews}
          />
          <ThesisSection
            icon={<Globe className="h-4 w-4" />}
            label="全球行业状态"
            content={pick.thesis.globalIndustryStatus}
          />
          <ThesisSection
            icon={<DollarSign className="h-4 w-4" />}
            label="入场策略"
            content={pick.thesis.entryStrategy}
          />
          <ThesisSection
            icon={<AlertTriangle className="h-4 w-4" />}
            label="主要风险"
            content={pick.thesis.keyRisks}
          />
        </div>
      )}
    </div>
  );
};

// ─── Results View ─────────────────────────────────────────────────────────────

const ScanResults: React.FC<{ report: ScanReport }> = ({ report }) => (
  <div className="space-y-4 animate-fade-in">
    {/* Summary banner */}
    <Card padding="md" className="flex flex-wrap gap-4 items-center">
      <div>
        <span className="label-uppercase">扫描完成</span>
        <p className="text-sm text-secondary-text mt-0.5">{formatTimestamp(report.timestamp)}</p>
      </div>
      <div className="ml-auto flex flex-wrap gap-6">
        <div className="text-center">
          <p className="text-xs text-muted-text">股票池</p>
          <p className="font-mono font-bold text-foreground">{report.funnel.universe.toLocaleString()}</p>
        </div>
        <div className="text-center">
          <p className="text-xs text-muted-text">深度分析</p>
          <p className="font-mono font-bold text-foreground">{report.funnel.tier5Analyzed}</p>
        </div>
        <div className="text-center">
          <p className="text-xs text-muted-text">推荐</p>
          <p className="font-mono font-bold text-cyan">{report.funnel.finalPicks}</p>
        </div>
        <div className="text-center">
          <p className="text-xs text-muted-text">耗时</p>
          <p className="font-mono font-bold text-foreground">{formatDuration(report.durationS)}</p>
        </div>
      </div>
    </Card>

    {/* Funnel */}
    <Card padding="md">
      <span className="label-uppercase mb-3 block">筛选漏斗</span>
      <div className="space-y-2">
        <FunnelBar label="全部股票" value={report.funnel.universe} max={report.funnel.universe} color="bg-border/60" />
        <FunnelBar label="基础筛选后" value={report.funnel.tier1} max={report.funnel.universe} color="bg-purple/70" />
        <FunnelBar label="技术面筛选" value={report.funnel.tier2} max={report.funnel.universe} color="bg-cyan/70" />
        <FunnelBar label="基本面筛选" value={report.funnel.tier3} max={report.funnel.universe} color="bg-success/70" />
        <FunnelBar label="AI分析" value={report.funnel.tier5Analyzed} max={report.funnel.universe} color="bg-warning/70" />
        <FunnelBar label="最终推荐" value={report.funnel.finalPicks} max={report.funnel.universe} color="bg-primary-gradient" />
      </div>
    </Card>

    {/* Picks */}
    <div>
      <h2 className="label-uppercase mb-3">
        中期投资机会 Top {report.topPicks.length}（1–6个月）
      </h2>
      <div className="space-y-3">
        {report.topPicks.map(pick => (
          <PickCard key={pick.ticker} pick={pick} />
        ))}
      </div>
    </div>
  </div>
);

// ─── History List ─────────────────────────────────────────────────────────────

const HistoryList: React.FC<{
  history: ScanMeta[];
  onSelect: (scanId: string) => void;
  activeScanId?: string;
}> = ({ history, onSelect, activeScanId }) => (
  <div className="space-y-2">
    {history.map(meta => (
      <button
        key={meta.scanId}
        type="button"
        onClick={() => onSelect(meta.scanId)}
        className={`w-full text-left rounded-xl border px-4 py-3 text-sm transition-all hover:bg-hover ${
          activeScanId === meta.scanId
            ? 'border-cyan/40 bg-cyan/5 text-foreground'
            : 'border-border/50 bg-card text-secondary-text'
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono font-bold text-foreground">{meta.topTicker || '—'}</span>
          <Badge variant={meta.status === 'completed' ? 'success' : 'danger'} size="sm">
            {formatScanStatus(meta.status)}
          </Badge>
        </div>
        <p className="mt-0.5 text-xs text-muted-text">{formatTimestamp(meta.timestamp)}</p>
        <p className="text-xs text-muted-text">{meta.universeSize.toLocaleString()} 只股票 · {formatDuration(meta.durationS)}</p>
      </button>
    ))}
  </div>
);

// ─── Config Panel ─────────────────────────────────────────────────────────────

const LABEL_CLASS = 'block text-xs text-secondary-text mb-1';
const INPUT_CLASS =
  'w-full rounded-lg border border-border/60 bg-card px-3 py-1.5 text-sm text-foreground focus:border-cyan/50 focus:outline-none';

const ConfigPanel: React.FC<{
  topN: number; setTopN: (v: number) => void;
  markets: string[]; setMarkets: (v: string[]) => void;
  minCap: number; setMinCap: (v: number) => void;
  tier5: number; setTier5: (v: number) => void;
  maxCnStocks: number; setMaxCnStocks: (v: number) => void;
  chinaPolicyWeight: number; setChinaPolicyWeight: (v: number) => void;
}> = ({
  topN, setTopN, markets, setMarkets, minCap, setMinCap, tier5, setTier5,
  maxCnStocks, setMaxCnStocks, chinaPolicyWeight, setChinaPolicyWeight,
}) => {
  const toggleMarket = (market: string) => {
    const next = markets.includes(market)
      ? markets.filter((m) => m !== market)
      : [...markets, market];
    if (next.length > 0) setMarkets(next);
  };

  return (
    <div className="mt-3 space-y-3 border-t border-border/40 pt-3">
      <div>
        <span className={LABEL_CLASS}>扫描市场</span>
        <div className="flex flex-wrap gap-3">
          {[
            ['us', '美股'],
            ['cn', 'A股'],
          ].map(([market, label]) => (
            <label key={market} className="inline-flex items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={markets.includes(market)}
                onChange={() => toggleMarket(market)}
                className="h-4 w-4 rounded border-border bg-card accent-cyan"
              />
              {label}
            </label>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div>
          <label className={LABEL_CLASS}>推荐数量</label>
          <input type="number" min={1} max={50} value={topN} onChange={e => setTopN(Number(e.target.value))} className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>最低市值（百万美元）</label>
          <input type="number" min={0} value={minCap} onChange={e => setMinCap(Number(e.target.value))} className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>AI分析候选数量</label>
          <input type="number" min={5} max={100} value={tier5} onChange={e => setTier5(Number(e.target.value))} className={INPUT_CLASS} />
        </div>
        {markets.includes('cn') && (
          <>
            <div>
              <label className={LABEL_CLASS}>A股股票池上限</label>
              <input type="number" min={50} max={5000} value={maxCnStocks} onChange={e => setMaxCnStocks(Number(e.target.value))} className={INPUT_CLASS} />
            </div>
            <div>
              <label className={LABEL_CLASS}>中国政策热点权重</label>
              <input type="number" min={0} max={1} step={0.05} value={chinaPolicyWeight} onChange={e => setChinaPolicyWeight(Number(e.target.value))} className={INPUT_CLASS} />
            </div>
          </>
        )}
      </div>
    </div>
  );
};

// ─── Main Page ────────────────────────────────────────────────────────────────

const ScannerPage: React.FC = () => {
  const [report, setReport] = useState<ScanReport | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatusResponse | null>(null);
  const [runningScanId, setRunningScanId] = useState<string | null>(null);
  const [history, setHistory] = useState<ScanMeta[]>([]);
  const [activeScanId, setActiveScanId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  // Config state
  const [topN, setTopN] = useState(10);
  const [markets, setMarkets] = useState<string[]>(['us', 'cn']);
  const [minCap, setMinCap] = useState(500);
  const [tier5, setTier5] = useState(30);
  const [maxCnStocks, setMaxCnStocks] = useState(800);
  const [chinaPolicyWeight, setChinaPolicyWeight] = useState(0.25);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      const r = await scannerApi.getLatestResult();
      setReport(r);
      setActiveScanId(r.scanId);
    } catch {
      // No result yet — that's fine
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const h = await scannerApi.getHistory();
      setHistory(h.scans ?? []);
    } catch {
      // ignore
    }
  }, []);

  // Initial load
  useEffect(() => {
    void loadLatest();
    void loadHistory();
  }, [loadLatest, loadHistory]);

  // Poll scan status when running
  useEffect(() => {
    if (!runningScanId) return;

    const poll = async () => {
      try {
        const s = await scannerApi.getStatus(runningScanId);
        setScanStatus(s);
        if (s.status === 'completed') {
          stopPoll();
          setRunningScanId(null);
          setScanStatus(null);
          // Load the finished result
          try {
            const r = await scannerApi.getResult(runningScanId);
            setReport(r);
            setActiveScanId(r.scanId);
          } catch {
            void loadLatest();
          }
          void loadHistory();
        } else if (s.status === 'failed') {
          stopPoll();
          setRunningScanId(null);
          setError(createParsedApiError({ title: '扫描失败', message: s.error ?? '扫描失败', status: 500 }));
        }
      } catch {
        // network glitch — keep polling
      }
    };

    void poll();
    pollRef.current = setInterval(() => { void poll(); }, 5000);
    return () => stopPoll();
  }, [runningScanId, stopPoll, loadLatest, loadHistory]);

  const handleStartScan = async () => {
    setIsStarting(true);
    setError(null);
    try {
      const res = await scannerApi.startScan({
        topN,
        markets,
        minMarketCapM: minCap,
        maxTier5Stocks: tier5,
        maxCnStocks,
        chinaPolicyWeight,
      });
      setRunningScanId(res.scanId);
      setScanStatus({ scanId: res.scanId, status: 'running', progress: 0, message: 'Starting…', startedAt: null, completedAt: null, error: null });
    } catch (e) {
      setError(getParsedApiError(e));
    } finally {
      setIsStarting(false);
    }
  };

  const handleSelectHistory = async (scanId: string) => {
    if (scanId === activeScanId) return;
    setIsLoading(true);
    setError(null);
    try {
      const r = await scannerApi.getResult(scanId);
      setReport(r);
      setActiveScanId(scanId);
    } catch (e) {
      setError(getParsedApiError(e));
    } finally {
      setIsLoading(false);
    }
  };

  const isRunning = !!runningScanId && !!scanStatus && scanStatus.status === 'running';

  return (
    <AppPage>
      <PageHeader
        eyebrow="跨市场情报"
        title="全市场扫股"
        description="通过五层 AI 漏斗扫描美股与 A 股股票，结合中国政策和国家热点权重寻找中期投资机会。"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {history.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowHistory(h => !h)}
              >
                <History className="mr-1.5 h-4 w-4" />
                历史记录（{history.length}）
              </Button>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowConfig(c => !c)}
            >
              {showConfig ? <ChevronUp className="mr-1.5 h-4 w-4" /> : <ChevronDown className="mr-1.5 h-4 w-4" />}
              配置
            </Button>
            <Button
              variant="primary"
              size="md"
              glow
              isLoading={isStarting}
              loadingText="启动中…"
              disabled={isRunning || isStarting}
              onClick={() => { void handleStartScan(); }}
            >
              <Scan className="mr-2 h-4 w-4" />
              {isRunning ? '扫描中…' : '开始全量扫描'}
            </Button>
          </div>
        }
      />

      {/* Config panel */}
      {showConfig && (
        <div className="mt-3 terminal-card p-4 animate-fade-in">
          <span className="label-uppercase">扫描配置</span>
          <ConfigPanel
            topN={topN} setTopN={setTopN}
            markets={markets} setMarkets={setMarkets}
            minCap={minCap} setMinCap={setMinCap}
            tier5={tier5} setTier5={setTier5}
            maxCnStocks={maxCnStocks} setMaxCnStocks={setMaxCnStocks}
            chinaPolicyWeight={chinaPolicyWeight} setChinaPolicyWeight={setChinaPolicyWeight}
          />
          <p className="mt-2 text-xs text-muted-text">
            提示：AI 分析候选越多，结果覆盖更充分，但耗时也更长；每只候选大约增加 1–2 分钟。
          </p>
        </div>
      )}

      {error && (
        <div className="mt-3">
          <InlineAlert variant="danger" message={error.message} />
        </div>
      )}

      <div className="mt-4 flex gap-4">
        {/* History sidebar */}
        {showHistory && history.length > 0 && (
          <div className="w-52 shrink-0">
            <span className="label-uppercase mb-2 block">历史扫描</span>
            <HistoryList history={history} onSelect={handleSelectHistory} activeScanId={activeScanId ?? undefined} />
          </div>
        )}

        {/* Main content */}
        <div className="min-w-0 flex-1 space-y-4">
          {/* Running */}
          {isRunning && scanStatus && <ScanProgress status={scanStatus} />}

          {/* Loading historical result */}
          {isLoading && (
            <div className="flex justify-center py-12">
              <RefreshCw className="h-6 w-6 animate-spin text-cyan" />
            </div>
          )}

          {/* Results */}
          {!isRunning && !isLoading && report && report.topPicks.length > 0 && (
            <ScanResults report={report} />
          )}

          {/* Empty state */}
          {!isRunning && !isLoading && !report && (
            <EmptyState
              icon={<Scan className="h-10 w-10" />}
              title="暂无扫描结果"
              description="启动全量扫描后，系统会在后台从配置的市场中筛选中期投资机会，通常需要 35–50 分钟。"
              action={
                <Button
                  variant="primary"
                  glow
                  isLoading={isStarting}
                  disabled={isStarting}
                  onClick={() => { void handleStartScan(); }}
                >
                  <Scan className="mr-2 h-4 w-4" />
                  开始全量扫描
                </Button>
              }
            />
          )}
        </div>
      </div>
    </AppPage>
  );
};

export default ScannerPage;
