export interface WatchlistItem {
  code: string;
  name: string;
  addedAt: string | null;
  notes: string;
  lastAnalyzedAt: string | null;
}

export interface WatchlistListResponse {
  items: WatchlistItem[];
  total: number;
}

export interface AnalyzeWatchlistResult {
  submitted: number;
  codes: string[];
}
