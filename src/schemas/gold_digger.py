# -*- coding: utf-8 -*-
"""
Data models for the 沙里淘金 (Gold Digger) feature.

沙里淘金 finds hidden gems inside "garbage" stocks:
  small/micro-cap  ·  beaten-down  ·  low analyst coverage  ·  PE undervalued
that are positioned to benefit from current macro/industry themes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InvestmentTheme:
    """A current macro or sector-level investment theme detected from news."""
    name: str                      # e.g. "AI Data Center Buildout"
    description: str
    keywords: List[str]            # keywords for matching stock sectors/industries
    relevant_sectors: List[str]    # GICS-style sectors
    market_regions: List[str]      # "us", "cn", "global"
    sentiment: str                 # "bullish" | "bearish" | "neutral"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "relevant_sectors": self.relevant_sectors,
            "market_regions": self.market_regions,
            "sentiment": self.sentiment,
        }


@dataclass
class GarbageStockInfo:
    """Raw data for a candidate garbage stock after initial filtering."""
    ticker: str
    name: str
    market: str            # "us" | "cn"
    sector: str
    industry: str
    market_cap_m: float    # USD millions
    current_price: float
    price_change_6m_pct: float    # negative = beaten-down
    price_change_1m_pct: float
    pe_ratio: Optional[float]
    sector_median_pe: Optional[float]
    pe_discount_pct: Optional[float]   # (stock_pe - sector_pe) / sector_pe * 100; negative = cheap
    analyst_count: int     # 0 = no coverage
    held_by_institutions_pct: Optional[float]
    short_ratio: Optional[float]


@dataclass
class ThemeMatch:
    """How well a stock matches an investment theme."""
    theme_name: str
    relevance_score: float   # 0-100
    match_reason: str        # short explanation


@dataclass
class GoldCandidate:
    """A screened candidate with all scoring dimensions."""
    stock: GarbageStockInfo
    value_score: float         # 0-100  (PE undervaluation)
    momentum_reversal_score: float  # 0-100 (beaten-down + technical bottom signals)
    theme_matches: List[ThemeMatch]
    top_theme_score: float     # max theme relevance
    institutional_score: float # 0-100 (institutional accumulation signals)
    composite_score: float     # weighted combination

    @property
    def ticker(self) -> str:
        return self.stock.ticker

    @property
    def market(self) -> str:
        return self.stock.market


@dataclass
class GoldPick:
    """Final ranked pick with full investment thesis."""
    rank: int
    ticker: str
    name: str
    market: str          # "US" | "A-share"
    sector: str
    industry: str
    current_price: float
    price_change_6m_pct: float
    pe_ratio: Optional[float]
    pe_discount_pct: Optional[float]
    composite_score: float
    llm_confidence: int          # 0-100 from LLM
    matched_themes: List[str]    # theme names
    why_garbage: str             # why the market overlooks it
    why_gold: str                # the hidden value / catalyst
    analysis_summary: str
    key_catalysts: str
    key_risks: str
    entry_strategy: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "ticker": self.ticker,
            "name": self.name,
            "market": self.market,
            "sector": self.sector,
            "industry": self.industry,
            "current_price": self.current_price,
            "price_change_6m_pct": round(self.price_change_6m_pct, 1),
            "pe_ratio": self.pe_ratio,
            "pe_discount_pct": round(self.pe_discount_pct, 1) if self.pe_discount_pct is not None else None,
            "composite_score": round(self.composite_score, 1),
            "llm_confidence": self.llm_confidence,
            "matched_themes": self.matched_themes,
            "why_garbage": self.why_garbage,
            "why_gold": self.why_gold,
            "analysis_summary": self.analysis_summary,
            "key_catalysts": self.key_catalysts,
            "key_risks": self.key_risks,
            "entry_strategy": self.entry_strategy,
        }


@dataclass
class DigConfig:
    """Configuration for a single 沙里淘金 run."""
    top_n: int = 10
    markets: List[str] = field(default_factory=lambda: ["us", "cn"])
    # US small/micro-cap bounds
    us_min_market_cap_m: float = 50.0
    us_max_market_cap_m: float = 1000.0
    # Beaten-down threshold (negative %)
    min_price_decline_6m_pct: float = 20.0
    # PE discount vs sector (positive % = how much cheaper than median, e.g. 20 means 20% below)
    min_pe_discount_pct: float = 10.0
    # Max stocks for deep LLM analysis per market
    max_tier5_per_market: int = 15
    # Number of themes to detect
    theme_count: int = 8
    # Extra weight for China policy / national hot-topic theme relevance.
    china_policy_weight: float = 0.25

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_n": self.top_n,
            "markets": self.markets,
            "us_min_market_cap_m": self.us_min_market_cap_m,
            "us_max_market_cap_m": self.us_max_market_cap_m,
            "min_price_decline_6m_pct": self.min_price_decline_6m_pct,
            "min_pe_discount_pct": self.min_pe_discount_pct,
            "max_tier5_per_market": self.max_tier5_per_market,
            "theme_count": self.theme_count,
            "china_policy_weight": self.china_policy_weight,
        }


@dataclass
class DigMeta:
    """Lightweight summary for listing endpoint."""
    run_id: str
    timestamp: str
    top_ticker: str
    top_name: str
    theme_count: int
    gold_picks: int
    duration_s: float
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "top_ticker": self.top_ticker,
            "top_name": self.top_name,
            "theme_count": self.theme_count,
            "gold_picks": self.gold_picks,
            "duration_s": round(self.duration_s, 1),
            "status": self.status,
        }


@dataclass
class DigReport:
    """Full run report returned to API and saved to disk."""
    run_id: str
    timestamp: str
    config: Dict[str, Any]
    detected_themes: List[InvestmentTheme]
    us_universe_size: int
    cn_universe_size: int
    garbage_filtered: int    # stocks passing garbage filters
    theme_matched: int       # stocks with theme relevance > threshold
    deep_analyzed: int
    gold_picks: List[GoldPick]
    duration_s: float
    status: str = "completed"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "config": self.config,
            "detected_themes": [t.to_dict() for t in self.detected_themes],
            "funnel": {
                "us_universe": self.us_universe_size,
                "cn_universe": self.cn_universe_size,
                "garbage_filtered": self.garbage_filtered,
                "theme_matched": self.theme_matched,
                "deep_analyzed": self.deep_analyzed,
                "gold_picks": len(self.gold_picks),
            },
            "gold_picks": [p.to_dict() for p in self.gold_picks],
            "duration_s": round(self.duration_s, 1),
            "status": self.status,
            "error": self.error,
        }
