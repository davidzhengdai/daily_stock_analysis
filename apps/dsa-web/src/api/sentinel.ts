import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  SentinelStatus,
  SentinelNewsItem,
  SentinelAnalysisItem,
  WatchedStockItem,
  FetchNowResult,
} from '../types/sentinel';

export const sentinelApi = {
  getStatus: async (): Promise<SentinelStatus> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/sentinel/status');
    return toCamelCase<SentinelStatus>(res.data);
  },

  getNews: async (params?: {
    hours?: number;
    priorityMin?: number;
    limit?: number;
  }): Promise<SentinelNewsItem[]> => {
    const query: Record<string, string | number> = {};
    if (params?.hours !== undefined) query.hours = params.hours;
    if (params?.priorityMin !== undefined) query.priority_min = params.priorityMin;
    if (params?.limit !== undefined) query.limit = params.limit;
    const res = await apiClient.get<unknown[]>('/api/v1/sentinel/news', { params: query });
    return toCamelCase<SentinelNewsItem[]>(res.data);
  },

  searchNews: async (q: string, limit = 20): Promise<SentinelNewsItem[]> => {
    const res = await apiClient.get<unknown[]>('/api/v1/sentinel/news/search', {
      params: { q, limit },
    });
    return toCamelCase<SentinelNewsItem[]>(res.data);
  },

  getAnalyses: async (limit = 5): Promise<SentinelAnalysisItem[]> => {
    const res = await apiClient.get<unknown[]>('/api/v1/sentinel/analyses', {
      params: { limit },
    });
    return toCamelCase<SentinelAnalysisItem[]>(res.data);
  },

  getWatchedStocks: async (): Promise<WatchedStockItem[]> => {
    const res = await apiClient.get<WatchedStockItem[]>('/api/v1/sentinel/watched-stocks');
    return Array.isArray(res.data) ? res.data : [];
  },

  setWatchedStocks: async (stocks: WatchedStockItem[], merge = false): Promise<void> => {
    await apiClient.put('/api/v1/sentinel/watched-stocks', stocks, {
      params: { merge },
    });
  },

  fetchNow: async (code: string, name = ''): Promise<FetchNowResult> => {
    const res = await apiClient.post<FetchNowResult>('/api/v1/sentinel/fetch-now', { code, name });
    return res.data;
  },
};
