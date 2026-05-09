export interface InvestmentTheme {
  name: string;
  description: string;
  keywords: string[];
  relevantSectors: string[];
  marketRegions: string[];
  sentiment: 'bullish' | 'bearish' | 'neutral';
}

export interface GoldPick {
  rank: number;
  ticker: string;
  name: string;
  market: string;
  sector: string;
  industry: string;
  currentPrice: number;
  priceChange6mPct: number;
  peRatio: number | null;
  peDiscountPct: number | null;
  compositeScore: number;
  llmConfidence: number;
  matchedThemes: string[];
  whyGarbage: string;
  whyGold: string;
  analysisSummary: string;
  keyCatalysts: string;
  keyRisks: string;
  entryStrategy: string;
}

export interface DigFunnel {
  usUniverse: number;
  cnUniverse: number;
  garbageFiltered: number;
  themeMatched: number;
  deepAnalyzed: number;
  goldPicks: number;
}

export interface DigReport {
  runId: string;
  timestamp: string;
  config: Record<string, unknown>;
  detectedThemes: InvestmentTheme[];
  funnel: DigFunnel;
  goldPicks: GoldPick[];
  durationS: number;
  status: string;
  error?: string;
}

export interface DigMeta {
  runId: string;
  timestamp: string;
  topTicker: string;
  topName: string;
  themeCount: number;
  goldPicks: number;
  durationS: number;
  status: string;
}

export interface DigStatus {
  runId: string;
  status: 'running' | 'completed' | 'error';
  progress: number;
  message: string;
}

export interface StartDigResponse {
  runId: string;
  status: string;
  message: string;
}
