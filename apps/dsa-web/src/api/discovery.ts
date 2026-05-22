import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DiscoveryHistoryResponse,
  DiscoveryListResponse,
  DiscoveryStatsResponse,
} from '../types/discovery';

export interface DiscoveryListParams {
  source?: string;
  market?: string;
  limit?: number;
  refresh?: boolean;
}

export interface DiscoveryHistoryParams {
  ticker?: string;
  itemId?: number;
  limit?: number;
}

export const discoveryApi = {
  list: async (params: DiscoveryListParams = {}): Promise<DiscoveryListResponse> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/discovery/', {
      params: {
        source: params.source || undefined,
        market: params.market || undefined,
        limit: params.limit ?? 100,
        refresh: params.refresh || undefined,
      },
    });
    return toCamelCase<DiscoveryListResponse>(res.data);
  },

  history: async (params: DiscoveryHistoryParams = {}): Promise<DiscoveryHistoryResponse> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/discovery/history', {
      params: {
        ticker: params.ticker || undefined,
        item_id: params.itemId,
        limit: params.limit ?? 100,
      },
    });
    return toCamelCase<DiscoveryHistoryResponse>(res.data);
  },

  stats: async (): Promise<DiscoveryStatsResponse> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/discovery/stats');
    return toCamelCase<DiscoveryStatsResponse>(res.data);
  },

  reject: async (itemId: number): Promise<void> => {
    await apiClient.post(`/api/v1/discovery/${itemId}/reject`);
  },
};
