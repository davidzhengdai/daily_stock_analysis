import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw, Star, Trash2, TrendingUp } from 'lucide-react';
import { watchlistApi } from '../api/watchlist';
import type { WatchlistItem } from '../types/watchlist';
import {
  AppPage,
  Button,
  EmptyState,
  InlineAlert,
  PageHeader,
  SectionCard,
} from '../components/common';

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

const WatchlistPage: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [newCode, setNewCode] = useState('');
  const [newName, setNewName] = useState('');
  const [newNotes, setNewNotes] = useState('');
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState<{ variant: 'success' | 'danger'; message: string } | null>(null);
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

  const loadItems = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await watchlistApi.listAll();
      setItems(res.items);
    } catch {
      showFeedback('danger', '加载自选股列表失败');
    } finally {
      setIsLoading(false);
    }
  }, [showFeedback]);

  useEffect(() => {
    document.title = '自选股 - DSA';
    void loadItems();
    return () => {
      if (feedbackTimer.current !== null) {
        window.clearTimeout(feedbackTimer.current);
      }
    };
  }, [loadItems]);

  const handleAdd = useCallback(async () => {
    const code = newCode.trim().toUpperCase();
    if (!code) {
      showFeedback('danger', '请输入股票代码');
      return;
    }
    setIsAdding(true);
    try {
      await watchlistApi.add(code, newName.trim(), newNotes.trim());
      setNewCode('');
      setNewName('');
      setNewNotes('');
      await loadItems();
      showFeedback('success', `已添加 ${code}`);
      codeInputRef.current?.focus();
    } catch {
      showFeedback('danger', `添加 ${code} 失败`);
    } finally {
      setIsAdding(false);
    }
  }, [newCode, newName, newNotes, loadItems, showFeedback]);

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
        void handleAdd();
      }
    },
    [handleAdd],
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
                onClick={() => void loadItems()}
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
                刷新
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
            <div className="flex flex-col gap-1">
              <label className="text-xs text-secondary-text">股票代码 *</label>
              <input
                ref={codeInputRef}
                type="text"
                value={newCode}
                onChange={(e) => setNewCode(e.target.value.toUpperCase())}
                onKeyDown={handleKeyDown}
                placeholder="如 600519 / AAPL"
                className={INPUT_CLS + ' w-40'}
                disabled={isAdding}
              />
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
          subtitle={`共 ${items.length} 只`}
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
