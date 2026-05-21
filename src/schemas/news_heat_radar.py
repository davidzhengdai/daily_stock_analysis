# -*- coding: utf-8 -*-
"""
热点雷达 (NewsHeatRadar) Schema 定义

短线新闻驱动扫描：HeatConfig / HotSector / HotPick / HeatReport
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class HeatConfig:
    """热点雷达运行配置"""
    top_n: int = 10
    markets: List[str] = field(default_factory=lambda: ["us", "cn"])
    ttl_days: int = 5
    theme_count: int = 8                  # 识别的热门板块数
    max_stocks_per_sector: int = 3        # 每个板块最多提名股票数
    model: str = ""                       # LLM 模型覆盖（空=使用默认）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top_n": self.top_n,
            "markets": self.markets,
            "ttl_days": self.ttl_days,
            "theme_count": self.theme_count,
            "max_stocks_per_sector": self.max_stocks_per_sector,
        }


@dataclass
class HotSector:
    """一个短线热门板块，由新闻密度/速度识别"""
    name: str
    heat_score: float                     # 0-100，新闻频次 × 影响权重
    news_velocity: int                    # 48小时内高信号文章数
    catalyst_summary: str                 # 1-2句催化剂说明
    keywords: List[str]
    market_regions: List[str]             # ["us", "cn", "global"]
    sentiment: str                        # "bullish" | "bearish" | "neutral"
    source_queries: List[str]             # 触发此板块的查询词

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "heat_score": round(self.heat_score, 1),
            "news_velocity": self.news_velocity,
            "catalyst_summary": self.catalyst_summary,
            "keywords": self.keywords,
            "market_regions": self.market_regions,
            "sentiment": self.sentiment,
            "source_queries": self.source_queries,
        }


@dataclass
class HotPick:
    """从热门板块中识别出的具体股票（短线，5个交易日视野）"""
    rank: int
    ticker: str
    name: str
    market: str                           # "US" | "A-share"
    sector: str
    current_price: float
    heat_score: float                     # 继承自板块 + 个股修正
    llm_confidence: int                   # 0-100
    matched_sector: str                   # 对应的 HotSector.name
    catalyst_thesis: str                  # 为什么这只股票受益（≤100字）
    entry_window: str                     # 如 "days 1-3", "this week"
    key_risks: str
    trade_horizon: str = "short"          # 固定 "short"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "ticker": self.ticker,
            "name": self.name,
            "market": self.market,
            "sector": self.sector,
            "current_price": self.current_price,
            "heat_score": round(self.heat_score, 1),
            "llm_confidence": self.llm_confidence,
            "matched_sector": self.matched_sector,
            "catalyst_thesis": self.catalyst_thesis,
            "entry_window": self.entry_window,
            "key_risks": self.key_risks,
            "trade_horizon": self.trade_horizon,
        }


@dataclass
class HeatMeta:
    """轻量级摘要，用于列表展示"""
    run_id: str
    timestamp: str
    top_sector: str
    top_ticker: str
    sector_count: int
    pick_count: int
    duration_s: float
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "top_sector": self.top_sector,
            "top_ticker": self.top_ticker,
            "sector_count": self.sector_count,
            "pick_count": self.pick_count,
            "duration_s": round(self.duration_s, 1),
            "status": self.status,
        }


@dataclass
class HeatReport:
    """热点雷达完整报告"""
    run_id: str
    timestamp: str
    config: Dict[str, Any]
    hot_sectors: List[HotSector]
    hot_picks: List[HotPick]
    duration_s: float
    status: str = "completed"
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "config": self.config,
            "hot_sectors": [s.to_dict() for s in self.hot_sectors],
            "hot_picks": [p.to_dict() for p in self.hot_picks],
            "funnel": {
                "sectors_identified": len(self.hot_sectors),
                "picks": len(self.hot_picks),
            },
            "duration_s": round(self.duration_s, 1),
            "status": self.status,
            "error": self.error,
        }

    def to_meta(self) -> HeatMeta:
        top_sector = self.hot_sectors[0].name if self.hot_sectors else ""
        top_ticker = self.hot_picks[0].ticker if self.hot_picks else ""
        return HeatMeta(
            run_id=self.run_id,
            timestamp=self.timestamp,
            top_sector=top_sector,
            top_ticker=top_ticker,
            sector_count=len(self.hot_sectors),
            pick_count=len(self.hot_picks),
            duration_s=self.duration_s,
            status=self.status,
        )
