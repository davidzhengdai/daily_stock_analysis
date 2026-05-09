import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  ScanRequest,
  ScanStartResponse,
  ScanStatusResponse,
  ScanReport,
  ScanHistoryResponse,
} from '../types/scanner';

export const scannerApi = {
  startScan: async (request: ScanRequest = {}): Promise<ScanStartResponse> => {
    const body = {
      top_n: request.topN ?? 10,
      markets: request.markets ?? ['us', 'cn'],
      min_market_cap_m: request.minMarketCapM ?? 500,
      min_avg_volume: request.minAvgVolume ?? 500000,
      min_price: request.minPrice ?? 5,
      max_price: request.maxPrice ?? 3000,
      max_tier5_stocks: request.maxTier5Stocks ?? 30,
      max_cn_stocks: request.maxCnStocks ?? 800,
      china_policy_weight: request.chinaPolicyWeight ?? 0.25,
      extra_context: request.extraContext ?? '',
    };
    const res = await apiClient.post<Record<string, unknown>>('/api/v1/scanner/scan', body);
    return toCamelCase<ScanStartResponse>(res.data);
  },

  getStatus: async (scanId: string): Promise<ScanStatusResponse> => {
    const res = await apiClient.get<Record<string, unknown>>(`/api/v1/scanner/status/${scanId}`);
    return toCamelCase<ScanStatusResponse>(res.data);
  },

  getLatestResult: async (): Promise<ScanReport> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/scanner/results');
    return toCamelCase<ScanReport>(res.data);
  },

  getResult: async (scanId: string): Promise<ScanReport> => {
    const res = await apiClient.get<Record<string, unknown>>(`/api/v1/scanner/results/${scanId}`);
    return toCamelCase<ScanReport>(res.data);
  },

  getHistory: async (): Promise<ScanHistoryResponse> => {
    const res = await apiClient.get<Record<string, unknown>>('/api/v1/scanner/history');
    return toCamelCase<ScanHistoryResponse>(res.data);
  },
};
