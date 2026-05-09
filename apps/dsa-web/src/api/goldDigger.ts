import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  StartDigResponse,
  DigStatus,
  DigReport,
  DigMeta,
} from '../types/goldDigger';

export interface DigRequest {
  topN?: number;
  markets?: string[];
  usMinMarketCapM?: number;
  usMaxMarketCapM?: number;
  minPriceDecline6mPct?: number;
  minPeDiscountPct?: number;
  maxTier5PerMarket?: number;
  themeCount?: number;
  chinaPolicyWeight?: number;
}

export const goldDiggerApi = {
  startDig: async (request: DigRequest = {}): Promise<StartDigResponse> => {
    const body = {
      top_n: request.topN ?? 10,
      markets: request.markets ?? ['us', 'cn'],
      us_min_market_cap_m: request.usMinMarketCapM ?? 50,
      us_max_market_cap_m: request.usMaxMarketCapM ?? 1000,
      min_price_decline_6m_pct: request.minPriceDecline6mPct ?? 20,
      min_pe_discount_pct: request.minPeDiscountPct ?? 10,
      max_tier5_per_market: request.maxTier5PerMarket ?? 15,
      theme_count: request.themeCount ?? 8,
      china_policy_weight: request.chinaPolicyWeight ?? 0.25,
    };
    const res = await apiClient.post<Record<string, unknown>>('/api/v1/gold-digger/dig', body);
    return toCamelCase<StartDigResponse>(res.data);
  },

  getStatus: async (runId: string): Promise<DigStatus> => {
    const res = await apiClient.get<Record<string, unknown>>(`/api/v1/gold-digger/status/${runId}`);
    return toCamelCase<DigStatus>(res.data);
  },

  getLatestResult: async (): Promise<DigReport> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/gold-digger/results');
    return toCamelCase<DigReport>(res.data);
  },

  getResult: async (runId: string): Promise<DigReport> => {
    const res = await apiClient.get<Record<string, unknown>>(`/api/v1/gold-digger/results/${runId}`);
    return toCamelCase<DigReport>(res.data);
  },

  listHistory: async (): Promise<DigMeta[]> => {
    const res = await apiClient.get<unknown[]>('/api/v1/gold-digger/history');
    return (res.data as Record<string, unknown>[]).map((item) =>
      toCamelCase<DigMeta>(item),
    );
  },
};
