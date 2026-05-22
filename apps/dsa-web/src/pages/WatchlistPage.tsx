import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { Loader2, RefreshCw, Search, Star, Trash2, TrendingUp } from 'lucide-react';
import { watchlistApi } from '../api/watchlist';
import type { SymbolSuggestion, WatchlistItem } from '../types/watchlist';
import {
  AppPage,
  Button,
  EmptyState,
  InlineAlert,
  PageHeader,
  SectionCard,
} from '../components/common';
import { REFRESH_POLICY_MS } from '../utils/refreshPolicy';

const INPUT_CLS =
  'h-9 rounded-lg border border-border/60 bg-input px-3 text-sm text-foreground placeholder:text-secondary-text focus:outline-none focus:ring-1 focus:ring-cyan/50 disabled:opacity-50 transition-colors';

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatPrice(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3).replace(/\.?0+$/, '') : '—';
}

function formatChange(value?: number | null): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
}

function formatTime(value?: string | null): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleTimeString();
  } catch {
    return value;
  }
}

function latestQuoteFetchedAt(items: WatchlistItem[]): string | null {
  const timestamps = items
    .map((item) => item.quote?.fetchedAt)
    .filter((value): value is string => Boolean(value));
  if (timestamps.length === 0) return null;
  return timestamps.sort().at(-1) ?? null;
}

const WatchlistPage: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [newCode, setNewCode] = useState('');
  const [newName, setNewName] = useState('');
  const [newNotes, setNewNotes] = useState('');
  const [symbolSuggestions, setSymbolSuggestions] = useState<SymbolSuggestion[]>([]);
  const [isSearchingSymbols, setIsSearchingSymbols] = useState(false);
  const [suggestionPanelStyle, setSuggestionPanelStyle] = useState<React.CSSProperties | null>(null);
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState<{ variant: 'success' | 'danger'; message: string } | null>(null);
  const [lastPriceRefreshAt, setLastPriceRefreshAt] = useState<string | null>(null);
  const feedbackTimer = useRef<number | null>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);

  const showFeedback = useCallback((variant: 'success' | 'danger', message: string) => {
    if (feedbackTimer.current !== null) {
      window.clearTimeout(feedbackTimer.current);
    }
    setFeedback({ variant, message });
    feedbackTimer.current = window.setTimeout(() => {
      setFeedback(null);
      feedbackTimer.current = null;
    }, 4000);
  }, []);

  const loadItems = useCallback(async (silent = false, refreshQuotes = false) => {
    if (!silent) setIsLoading(true);
    try {
      const res = await watchlistApi.listAll({ refresh: refreshQuotes });
      setItems(res.items);
      if (refreshQuotes) {
        setLastPriceRefreshAt(latestQuoteFetchedAt(res.items) ?? new Date().toISOString());
      }
    } catch {
      if (!silent) showFeedback('danger', '加载自选股列表失败');
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, [showFeedback]);

  useEffect(() => {
    document.title = '自选股 - DSA';
    let lastRefresh = Date.now();
    void (async () => {
      await loadItems();
      void loadItems(true, true);
    })();
    const refreshIfVisible = () => {
      if (document.hidden) return;
      const now = Date.now();
      if (now - lastRefresh < REFRESH_POLICY_MS.realtimeQuoteMinGap) return;
      lastRefresh = now;
      void loadItems(true, true);
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
      if (feedbackTimer.current !== null) {
        window.clearTimeout(feedbackTimer.current);
      }
    };
  }, [loadItems]);

  useEffect(() => {
    const query = newCode.trim();
    if (query.length < 2 || !/[A-Za-z]/.test(query)) {
      setSymbolSuggestions([]);
      setIsSearchingSymbols(false);
      return undefined;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setIsSearchingSymbols(true);
      watchlistApi.searchSymbols(query)
        .then((res) => {
          if (cancelled) return;
          const exact = query.toUpperCase();
          setSymbolSuggestions(res.items.filter((item) => item.symbol !== exact));
        })
        .catch(() => {
          if (!cancelled) {
            setSymbolSuggestions([]);
          }
        })
        .finally(() => {
          if (!cancelled) {
            setIsSearchingSymbols(false);
          }
        });
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [newCode]);

  const applySymbolSuggestion = useCallback((suggestion: SymbolSuggestion) => {
    setNewCode(suggestion.symbol);
    setNewName(suggestion.name);
    setSymbolSuggestions([]);
    setSuggestionPanelStyle(null);
  }, []);

  const updateSuggestionPanelPosition = useCallback(() => {
    const input = codeInputRef.current;
    if (!input) {
      setSuggestionPanelStyle(null);
      return;
    }

    const rect = input.getBoundingClientRect();
    const panelWidth = Math.min(288, window.innerWidth - 24);
    const left = Math.min(Math.max(rect.left, 12), window.innerWidth - panelWidth - 12);
    const estimatedHeight = Math.min(240, symbolSuggestions.length * 40);
    const belowTop = rect.bottom + 6;
    const aboveTop = Math.max(12, rect.top - estimatedHeight - 6);
    const hasRoomBelow = belowTop + estimatedHeight <= window.innerHeight - 12;

    setSuggestionPanelStyle({
      position: 'fixed',
      top: hasRoomBelow ? belowTop : aboveTop,
      left,
      width: panelWidth,
      zIndex: 160,
    });
  }, [symbolSuggestions.length]);

  useEffect(() => {
    if (symbolSuggestions.length === 0) {
      setSuggestionPanelStyle(null);
      return undefined;
    }

    const frameId = window.requestAnimationFrame(updateSuggestionPanelPosition);
    window.addEventListener('resize', updateSuggestionPanelPosition);
    window.addEventListener('scroll', updateSuggestionPanelPosition, true);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener('resize', updateSuggestionPanelPosition);
      window.removeEventListener('scroll', updateSuggestionPanelPosition, true);
    };
  }, [symbolSuggestions.length, updateSuggestionPanelPosition]);

  const handleAdd = useCallback(async () => {
    const typedCode = newCode.trim().toUpperCase();
    const builtinSuggestion = symbolSuggestions.find((item) => item.source === 'builtin');
    const code = builtinSuggestion?.symbol ?? typedCode;
    if (!code) {
      showFeedback('danger', '请输入股票代码');
      return;
    }
    setIsAdding(true);
    try {
      await watchlistApi.add(code, builtinSuggestion?.name ?? newName.trim(), newNotes.trim());
      setNewCode('');
      setNewName('');
      setNewNotes('');
      setSymbolSuggestions([]);
      setSuggestionPanelStyle(null);
      await loadItems();
      showFeedback('success', `已添加 ${code}`);
      codeInputRef.current?.focus();
    } catch {
      showFeedback('danger', `添加 ${code} 失败`);
    } finally {
      setIsAdding(false);
    }
  }, [newCode, newName, newNotes, symbolSuggestions, loadItems, showFeedback]);

  const handleRemove = useCallback(
    async (code: string) => {
      try {
        await watchlistApi.remove(code);
        setSelectedCodes((prev) => {
          const next = new Set(prev);
          next.delete(code);
          return next;
        });
        await loadItems();
        showFeedback('success', `已移除 ${code}`);
      } catch {
        showFeedback('danger', `移除 ${code} 失败`);
      }
    },
    [loadItems, showFeedback],
  );

  const handleAnalyze = useCallback(
    async (codes?: string[]) => {
      const targets = codes ?? [];
      try {
        const result = await watchlistApi.analyze(targets.length > 0 ? targets : undefined);
        if (result.submitted === 0) {
          showFeedback('danger', '没有可提交的分析任务（可能都已在队列中）');
        } else {
          setSelectedCodes(new Set());
          navigate('/');
        }
      } catch {
        showFeedback('danger', '提交分析任务失败');
      }
    },
    [showFeedback, navigate],
  );

  const handleToggleSelect = useCallback((code: string) => {
    setSelectedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) {
        next.delete(code);
      } else {
        next.add(code);
      }
      return next;
    });
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        if (
          e.currentTarget === codeInputRef.current
          && symbolSuggestions.length > 0
          && newCode.trim().toUpperCase() !== symbolSuggestions[0].symbol
        ) {
          e.preventDefault();
          applySymbolSuggestion(symbolSuggestions[0]);
          return;
        }
        void handleAdd();
      }
    },
    [applySymbolSuggestion, handleAdd, newCode, symbolSuggestions],
  );

  const selectedArray = Array.from(selectedCodes);

  return (
    <AppPage>
      <div className="flex flex-col gap-6">
        <PageHeader
          eyebrow="股票管理"
          title="自选股"
          description={
            items.length > 0
              ? `当前关注 ${items.length} 只股票`
              : '添加您感兴趣的股票，随时触发分析'
          }
          actions={
            <>
              <Button
                variant="secondary"
                size="md"
                onClick={() => void handleAnalyze()}
                disabled={items.length === 0}
              >
                <TrendingUp className="h-4 w-4" aria-hidden="true" />
                分析全部
              </Button>
              <Button
                variant="secondary"
                size="md"
                isLoading={isLoading}
                loadingText="刷新中"
                onClick={() => void loadItems(false, true)}
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                刷新价格
              </Button>
            </>
          }
        />

        {/* 反馈提示 */}
        {feedback ? (
          <InlineAlert
            variant={feedback.variant}
            message={feedback.message}
            className="rounded-xl px-4 py-2 text-sm shadow-none"
          />
        ) : null}

        {/* 添加自选股 */}
        <SectionCard title="添加自选股">
          <div className="flex flex-wrap items-end gap-2">
            <div className="relative flex flex-col gap-1">
              <label className="text-xs text-secondary-text">股票代码 *</label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-secondary-text" aria-hidden="true" />
                <input
                  ref={codeInputRef}
                  type="text"
                  value={newCode}
                  onChange={(e) => setNewCode(e.target.value.toUpperCase())}
                  onKeyDown={handleKeyDown}
                  placeholder="AAPL / GOOGLE"
                  className={INPUT_CLS + ' w-44 pl-8 pr-8'}
                  disabled={isAdding}
                  autoComplete="off"
                />
                {isSearchingSymbols ? (
                  <Loader2 className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-secondary-text" aria-hidden="true" />
                ) : null}
              </div>
              {symbolSuggestions.length > 0 && suggestionPanelStyle
                ? createPortal(
                <div
                  className="max-h-60 overflow-y-auto rounded-lg border border-border/70 bg-panel shadow-[0_24px_60px_rgba(3,8,20,0.32)] backdrop-blur-xl"
                  style={suggestionPanelStyle}
                >
                  {symbolSuggestions.map((suggestion) => (
                    <button
                      key={suggestion.symbol}
                      type="button"
                      className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors hover:bg-hover"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => applySymbolSuggestion(suggestion)}
                    >
                      <span className="w-16 flex-shrink-0 font-mono font-semibold text-cyan">
                        {suggestion.symbol}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-foreground">
                        {suggestion.name}
                      </span>
                      <span className="flex-shrink-0 text-xs text-secondary-text">
                        {suggestion.exchange || suggestion.quoteType}
                      </span>
                    </button>
                  ))}
                </div>,
                document.body,
              ) : null}
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-secondary-text">名称（可选）</label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="如 贵州茅台"
                className={INPUT_CLS + ' w-36'}
                disabled={isAdding}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-secondary-text">备注（可选）</label>
              <input
                type="text"
                value={newNotes}
                onChange={(e) => setNewNotes(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="备注"
                className={INPUT_CLS + ' w-48'}
                disabled={isAdding}
              />
            </div>
            <Button
              variant="primary"
              size="md"
              isLoading={isAdding}
              loadingText="添加中"
              onClick={() => void handleAdd()}
              disabled={isAdding || !newCode.trim()}
            >
              添加
            </Button>
          </div>
        </SectionCard>

        {/* 批量操作栏 */}
        {selectedArray.length > 0 ? (
          <div className="flex items-center gap-3 rounded-xl border border-cyan/30 bg-cyan/5 px-4 py-2.5 text-sm">
            <span className="text-secondary-text">
              已选择 <span className="font-semibold text-cyan">{selectedArray.length}</span> 只
            </span>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void handleAnalyze(selectedArray)}
            >
              <TrendingUp className="h-3.5 w-3.5" aria-hidden="true" />
              分析选中 ({selectedArray.length})
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelectedCodes(new Set())}
            >
              取消
            </Button>
          </div>
        ) : null}

        {/* 自选股列表 */}
        <SectionCard
          title="已关注股票"
          subtitle={`共 ${items.length} 只${lastPriceRefreshAt ? ` · 行情数据 ${formatTime(lastPriceRefreshAt)} 更新` : ''}`}
        >
          {isLoading && items.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-secondary-text text-sm">
              加载中…
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              title="暂无自选股"
              description="在上方输入股票代码添加您关注的股票"
              icon={<Star className="h-8 w-8" />}
            />
          ) : (
            <div className="flex flex-col divide-y divide-border/40">
              {items.map((item) => (
                <div
                  key={item.code}
                  className="flex items-center gap-3 py-3 hover:bg-hover/40 transition-colors rounded-lg px-2"
                >
                  {/* 复选框 */}
                  <input
                    type="checkbox"
                    checked={selectedCodes.has(item.code)}
                    onChange={() => handleToggleSelect(item.code)}
                    className="h-4 w-4 rounded border-border accent-primary flex-shrink-0"
                    aria-label={`选择 ${item.code}`}
                  />

                  {/* 星标 */}
                  <Star className="h-4 w-4 flex-shrink-0 text-yellow-400 fill-yellow-400" aria-hidden="true" />

                  {/* 代码 */}
                  <span className="font-mono text-sm font-semibold text-cyan w-24 flex-shrink-0">
                    {item.code}
                  </span>

                  {/* 名称 */}
                  <span className="text-sm text-foreground flex-1 min-w-0 truncate">
                    {item.name || <span className="text-secondary-text">—</span>}
                  </span>

                  {/* 实时价格 */}
                  <div className="hidden lg:flex w-32 flex-shrink-0 flex-col items-end">
                    <span className="font-mono text-sm font-semibold text-foreground">
                      {formatPrice(item.quote?.price)}
                    </span>
                    <span className={`text-xs ${
                      (item.quote?.changePct ?? 0) > 0
                        ? 'text-emerald-400'
                        : (item.quote?.changePct ?? 0) < 0
                          ? 'text-rose-400'
                          : 'text-secondary-text'
                    }`}>
                      {formatChange(item.quote?.changePct)}
                    </span>
                  </div>

                  <span className="hidden xl:block text-xs text-secondary-text flex-shrink-0 w-28 text-right">
                    {item.quote?.source ? `${item.quote.source} · ${formatTime(item.quote.fetchedAt)}` : '—'}
                  </span>

                  {/* 添加时间 */}
                  <span className="hidden sm:block text-xs text-secondary-text flex-shrink-0 w-28 text-right">
                    {formatDate(item.addedAt)}
                  </span>

                  {/* 最近分析 */}
                  <span className="hidden md:block text-xs text-secondary-text flex-shrink-0 w-28 text-right">
                    {item.lastAnalyzedAt ? formatDate(item.lastAnalyzedAt) : '未分析'}
                  </span>

                  {/* 操作 */}
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    <Button
                      variant="ghost"
                      size="xsm"
                      title={`分析 ${item.code}`}
                      onClick={() => void handleAnalyze([item.code])}
                    >
                      <TrendingUp className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                    <Button
                      variant="danger-subtle"
                      size="xsm"
                      title={`移除 ${item.code}`}
                      onClick={() => void handleRemove(item.code)}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    </AppPage>
  );
};

export default WatchlistPage;
