import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  WatchlistItem,
  WatchlistListResponse,
  AnalyzeWatchlistResult,
  SymbolSearchResponse,
} from '../types/watchlist';

export const watchlistApi = {
  /**
   * 获取全部自选股列表。
   */
  listAll: async (options: { refresh?: boolean } = {}): Promise<WatchlistListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/watchlist/', {
      params: { refresh: options.refresh || undefined },
    });
    return toCamelCase<WatchlistListResponse>(response.data);
  },

  /**
   * 添加自选股。
   */
  add: async (code: string, name = '', notes = ''): Promise<WatchlistItem> => {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/watchlist/', {
      code,
      name,
      notes,
    });
    return toCamelCase<WatchlistItem>(response.data);
  },

  /**
   * 删除自选股。
   */
  remove: async (code: string): Promise<void> => {
    await apiClient.delete(`/api/v1/watchlist/${encodeURIComponent(code)}`);
  },

  /**
   * 更新自选股名称或备注。
   */
  update: async (code: string, patch: { name?: string; notes?: string }): Promise<WatchlistItem> => {
    const response = await apiClient.patch<Record<string, unknown>>(
      `/api/v1/watchlist/${encodeURIComponent(code)}`,
      patch
    );
    return toCamelCase<WatchlistItem>(response.data);
  },

  /**
   * 触发分析。codes 为空时分析全部自选股。
   */
  analyze: async (codes?: string[]): Promise<AnalyzeWatchlistResult> => {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/watchlist/analyze', {
      codes: codes ?? null,
    });
    return toCamelCase<AnalyzeWatchlistResult>(response.data);
  },

  /**
   * 按美股代码、公司名或常见别名搜索股票代码。
   */
  searchSymbols: async (query: string, limit = 8): Promise<SymbolSearchResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/watchlist/symbols/search', {
      params: { q: query, limit },
    });
    return toCamelCase<SymbolSearchResponse>(response.data);
  },

  /**
   * 判断指定股票是否已在自选股中（本地列表检查版本，需要先调用 listAll）。
   */
  isWatched: async (code: string): Promise<boolean> => {
    const { items } = await watchlistApi.listAll();
    return items.some((item) => item.code === code);
  },
};
