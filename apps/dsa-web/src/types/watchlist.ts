export interface RealtimeQuote {
  code?: string;
  name?: string;
  source?: string;
  price?: number;
  changePct?: number;
  changeAmount?: number;
  volume?: number;
  amount?: number;
  volumeRatio?: number;
  turnoverRate?: number;
  openPrice?: number;
  high?: number;
  low?: number;
  preClose?: number;
  fetchedAt?: string;
}

export interface WatchlistItem {
  code: string;
  name: string;
  addedAt: string | null;
  notes: string;
  lastAnalyzedAt: string | null;
  quote?: RealtimeQuote | null;
}

export interface WatchlistListResponse {
  items: WatchlistItem[];
  total: number;
}

export interface AnalyzeWatchlistResult {
  submitted: number;
  codes: string[];
}

export interface SymbolSuggestion {
  symbol: string;
  name: string;
  exchange: string;
  quoteType: string;
  source: string;
}

export interface SymbolSearchResponse {
  query: string;
  items: SymbolSuggestion[];
  total: number;
}
