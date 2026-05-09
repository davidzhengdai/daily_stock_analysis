export interface ScanRequest {
  topN?: number;
  markets?: string[];
  minMarketCapM?: number;
  minAvgVolume?: number;
  minPrice?: number;
  maxPrice?: number;
  maxTier5Stocks?: number;
  maxCnStocks?: number;
  chinaPolicyWeight?: number;
  extraContext?: string;
}

export interface ScanStartResponse {
  scanId: string;
  status: string;
  message: string;
  progressUrl: string;
  resultUrl: string;
}

export interface ScanStatusResponse {
  scanId: string;
  status: 'running' | 'completed' | 'failed';
  progress: number;
  message: string;
  startedAt: string | null;
  completedAt: string | null;
  error: string | null;
}

export interface ScanFunnel {
  universe: number;
  tier1: number;
  tier2: number;
  tier3: number;
  tier4: number;
  tier5Analyzed: number;
  finalPicks: number;
}

export interface InvestmentThesis {
  financialSummary: string;
  industryNews: string;
  globalIndustryStatus: string;
  entryStrategy: string;
  keyRisks: string;
}

export interface StockPick {
  rank: number;
  ticker: string;
  name: string;
  market?: string;
  sector: string;
  industry: string;
  currentPrice: number;
  compositeScore: number;
  llmConfidence: number;
  buySignal: string;
  llmDecision: string;
  whySelected?: string;
  selectionFactors?: string[];
  analysisSummary: string;
  thesis: InvestmentThesis;
}

export interface ScanReport {
  scanId: string;
  timestamp: string;
  config: Record<string, unknown>;
  funnel: ScanFunnel;
  topPicks: StockPick[];
  durationS: number;
  status: string;
  error: string | null;
}

export interface ScanMeta {
  scanId: string;
  timestamp: string;
  topTicker: string;
  topScore: number;
  universeSize: number;
  topN: number;
  durationS: number;
  status: string;
}

export interface ScanHistoryResponse {
  scans: ScanMeta[];
}
