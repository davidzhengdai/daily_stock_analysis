export type DiscoverySource = 'scanner' | 'gold_digger' | 'heat_radar' | string;

export type DiscoveryStatus = 'active' | 'expired' | 'rejected' | string;

export type DiscoveryEventAction = 'added' | 'expired' | 'rejected' | string;

export interface DiscoveryItem {
  id: number;
  ticker: string;
  name: string;
  market: string;
  sector: string;
  source: DiscoverySource;
  sourceRunId: string;
  score: number;
  confidence: number;
  thesis: string;
  themes: string[];
  tradeHorizon: string;
  addedAt: string | null;
  expiresAt: string | null;
  status: DiscoveryStatus;
  rejectedAt: string | null;
  allowAutoTrade: boolean;
}

export interface DiscoveryEvent {
  id: number;
  itemId: number | null;
  ticker: string;
  name: string;
  market: string;
  source: DiscoverySource;
  sourceRunId: string;
  action: DiscoveryEventAction;
  reason: string;
  details: Record<string, unknown>;
  createdAt: string | null;
}

export interface DiscoveryListResponse {
  items: DiscoveryItem[];
  count: number;
}

export interface DiscoveryHistoryResponse {
  events: DiscoveryEvent[];
  count: number;
}

export interface DiscoveryStatsResponse {
  stats: Record<string, Record<string, number>>;
}
