export interface SentinelStatus {
  enabled: boolean;
  dbPath: string;
  totalItems: number;
  unclassifiedCount: number;
  lastAnalysisAt: string | null;
}

export interface SentinelNewsItem {
  id: number;
  title: string;
  content: string;
  sourceName: string;
  url: string;
  priority: number | null;
  sentiment: string | null;
  category: string | null;
  marketScope: string | null;
  affectedSectors: string[];
  affectedStocks: string[];
  impactHorizon: string | null;
  llmReasoning: string | null;
  isActionable: boolean;
  publishedAt: string | null;
  fetchedAt: string;
}

export interface SentinelTheme {
  theme: string;
  confidence: number;
  sectors: string[];
}

export interface SentinelSectorOpp {
  sector: string;
  signal: string;
  horizon: string;
  reason: string;
}

export interface SentinelStockLead {
  code: string;
  name: string;
  reason: string;
  confidence: number;
}

export interface SentinelAnalysisItem {
  id: number;
  cycleAt: string;
  newsCount: number;
  themes: SentinelTheme[];
  sectorOpps: SentinelSectorOpp[];
  stockLeads: SentinelStockLead[];
  riskAlerts: string[];
  marketMood: string;
  triggeredStocks: string[];
  createdAt: string;
}
