// 模拟交易 API 客户端

import type {
  AccountSettingsRequest,
  AutoTradeRun,
  AutoTradeStatus,
  FundItem,
  FundRequest,
  RunJob,
  SimAccount,
  SimOrder,
  SimPosition,
  SimSignal,
  SimSnapshot,
  OrderRequest,
} from '../types/simtrade';

const BASE = '/api/v1/sim-trade';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail?.message ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// Account
export const getAccount = () => request<SimAccount>('/account');

export const resetAccount = () =>
  request<SimAccount>('/account/reset', { method: 'POST' });

export const updateSettings = (data: AccountSettingsRequest) =>
  request<SimAccount>('/account/settings', {
    method: 'PATCH',
    body: JSON.stringify(data),
  });

// Funding
export const fund = (data: FundRequest) =>
  request<FundItem>('/fund', { method: 'POST', body: JSON.stringify(data) });

export const getFundHistory = (limit = 50) =>
  request<{ items: FundItem[]; total: number }>(`/fund/history?limit=${limit}`);

// Orders
export const getOrders = (params?: {
  status?: string;
  source?: string;
  limit?: number;
}) => {
  const q = new URLSearchParams();
  if (params?.status) q.set('status', params.status);
  if (params?.source) q.set('source', params.source);
  if (params?.limit) q.set('limit', String(params.limit));
  return request<{ items: SimOrder[]; total: number }>(`/orders?${q.toString()}`);
};

export const placeOrder = (data: OrderRequest) =>
  request<SimOrder>('/orders', { method: 'POST', body: JSON.stringify(data) });

export const cancelOrder = (orderId: number) =>
  request<{ message: string }>(`/orders/${orderId}`, { method: 'DELETE' });

// Positions
export const getPositions = (options?: { refresh?: boolean }) =>
  request<{ items: SimPosition[]; total: number }>(
    `/positions${options?.refresh ? '?refresh=true' : ''}`
  );

// AI Signals
export const getSignals = (limit = 30) =>
  request<{ items: SimSignal[]; total: number }>(`/signals?limit=${limit}`);

// Auto-trade
export const toggleAutoTrade = (enabled: boolean) =>
  request<SimAccount>('/auto-trade/toggle', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });

export const startAutoTrade = () =>
  request<RunJob>('/auto-trade/run', { method: 'POST' });

export const getRunJob = (jobId: string) =>
  request<RunJob>(`/auto-trade/run/${jobId}`);

export const getAutoTradeStatus = () =>
  request<AutoTradeStatus>('/auto-trade/status');

export const getAutoTradeHistory = (limit = 50) =>
  request<{ items: AutoTradeRun[]; total: number }>(`/auto-trade/history?limit=${limit}`);

// Snapshots
export const getSnapshotHistory = (limit = 90) =>
  request<{ items: SimSnapshot[]; total: number }>(
    `/snapshot/history?limit=${limit}`
  );
