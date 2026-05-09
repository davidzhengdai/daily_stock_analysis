# -*- coding: utf-8 -*-
"""
Data models for the US Market Scanner feature.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class StockInfo:
    """Basic stock metadata from universe source."""
    ticker: str
    name: str
    sector: str
    industry: str
    market_cap_m: float       # USD millions
    avg_volume: float
    price: float
    exchange: str = ""
    market: str = "us"        # "us" | "cn"


@dataclass
class TechScore:
    """Stock with technical analysis score appended."""
    stock: StockInfo
    signal_score: int         # 0-100
    trend_status: str         # e.g. "强势多头"
    buy_signal: str           # e.g. "买入"
    rsi_12: float
    macd_status: str
    volume_status: str

    @property
    def ticker(self) -> str:
        return self.stock.ticker


@dataclass
class FundScore:
    """Stock with fundamental score appended (after Tier 3)."""
    tech: TechScore
    pe_ratio: Optional[float]
    forward_pe: Optional[float]
    roe: Optional[float]               # return on equity (decimal, e.g. 0.25 = 25%)
    revenue_growth: Optional[float]    # YoY growth (decimal)
    profit_margin: Optional[float]
    debt_to_equity: Optional[float]
    fundamental_score: float          # 0-100 computed score
    composite_score: float            # weighted tech + fundamental

    @property
    def ticker(self) -> str:
        return self.tech.ticker

    @property
    def sector(self) -> str:
        return self.tech.stock.sector


@dataclass
class CandidateStock:
    """Top candidate after sector diversity filtering (Tier 4)."""
    fund: FundScore
    sector_rank: int   # rank within its sector (1 = best in sector)

    @property
    def ticker(self) -> str:
        return self.fund.ticker

    @property
    def sector(self) -> str:
        return self.fund.sector

    @property
    def composite_score(self) -> float:
        return self.fund.composite_score


@dataclass
class ScanConfig:
    """Configuration for a single scan run."""
    top_n: int = 10
    markets: List[str] = field(default_factory=lambda: ["us"])
    min_market_cap_m: float = 500.0
    min_avg_volume: int = 500_000
    min_price: float = 5.0
    max_price: float = 3000.0
    max_tier2_candidates: int = 200
    max_tier3_candidates: int = 50
    max_tier5_stocks: int = 30
    max_cn_stocks: int = 800
    china_policy_weight: float = 0.25
    horizon_label: str = "medium"   # medium = 1-6 months
    extra_context: str = ""         # optional additional LLM instructions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_n": self.top_n,
            "markets": self.markets,
            "min_market_cap_m": self.min_market_cap_m,
            "min_avg_volume": self.min_avg_volume,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "max_tier5_stocks": self.max_tier5_stocks,
            "max_cn_stocks": self.max_cn_stocks,
            "china_policy_weight": self.china_policy_weight,
            "horizon_label": self.horizon_label,
        }


@dataclass
class InvestmentThesis:
    """Structured investment thesis for a top-pick stock."""
    financial_summary: str      # historical earnings, growth, margins, debt
    industry_news: str          # recent industry catalysts and headwinds
    global_industry_status: str # macro/global industry trends
    entry_strategy: str         # buy zone, stop loss, targets
    key_risks: str


@dataclass
class StockRecommendation:
    """Final ranked recommendation with full investment thesis."""
    rank: int
    ticker: str
    name: str
    market: str
    sector: str
    industry: str
    current_price: float
    composite_score: float
    llm_confidence: int         # 0-100 from LLM analysis
    buy_signal: str
    why_selected: str           # concise explanation of why this stock survived the funnel
    selection_factors: List[str] # key screen factors that drove the ranking
    news_evidence: List[Dict[str, Any]]
    thesis: InvestmentThesis
    llm_decision: str           # LLM buy/sell/hold label
    analysis_summary: str       # brief LLM summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "ticker": self.ticker,
            "name": self.name,
            "market": self.market,
            "sector": self.sector,
            "industry": self.industry,
            "current_price": self.current_price,
            "composite_score": round(self.composite_score, 1),
            "llm_confidence": self.llm_confidence,
            "buy_signal": self.buy_signal,
            "why_selected": self.why_selected,
            "selection_factors": self.selection_factors,
            "news_evidence": self.news_evidence,
            "llm_decision": self.llm_decision,
            "analysis_summary": self.analysis_summary,
            "thesis": {
                "financial_summary": self.thesis.financial_summary,
                "industry_news": self.thesis.industry_news,
                "global_industry_status": self.thesis.global_industry_status,
                "entry_strategy": self.thesis.entry_strategy,
                "key_risks": self.thesis.key_risks,
            },
        }


@dataclass
class ScanMeta:
    """Lightweight scan summary for listing endpoint."""
    scan_id: str
    timestamp: str
    top_ticker: str
    top_score: float
    universe_size: int
    top_n: int
    duration_s: float
    status: str   # "completed" | "failed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp,
            "top_ticker": self.top_ticker,
            "top_score": self.top_score,
            "universe_size": self.universe_size,
            "top_n": self.top_n,
            "duration_s": round(self.duration_s, 1),
            "status": self.status,
        }


@dataclass
class ScanReport:
    """Full scan report returned to API clients and saved to disk."""
    scan_id: str
    timestamp: str
    config: Dict[str, Any]
    universe_size: int
    tier1_survivors: int
    tier2_survivors: int
    tier3_survivors: int
    tier4_survivors: int
    tier5_analyzed: int
    top_picks: List[StockRecommendation]
    duration_s: float
    status: str = "completed"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp,
            "config": self.config,
            "funnel": {
                "universe": self.universe_size,
                "tier1": self.tier1_survivors,
                "tier2": self.tier2_survivors,
                "tier3": self.tier3_survivors,
                "tier4": self.tier4_survivors,
                "tier5_analyzed": self.tier5_analyzed,
                "final_picks": len(self.top_picks),
            },
            "top_picks": [p.to_dict() for p in self.top_picks],
            "duration_s": round(self.duration_s, 1),
            "status": self.status,
            "error": self.error,
        }
