# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List


@dataclass
class RawArticle:
    url: str
    title: str
    source_name: str
    source_url: str
    spider_name: str
    content: str = ""
    published_at: Optional[datetime] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    language: str = "zh"


@dataclass
class NewsItem:
    url_hash: str
    url: str
    title: str
    source_name: str
    source_url: str
    spider_name: str
    content: str = ""
    language: str = "zh"
    simhash: Optional[int] = None
    published_at: Optional[str] = None   # ISO8601 UTC string
    fetched_at: str = ""
    expires_at: Optional[str] = None
    is_expired: bool = False
    is_archived: bool = False
    # LLM fields (populated in Phase 2)
    category: Optional[str] = None
    priority: Optional[int] = None
    sentiment: Optional[str] = None
    market_scope: Optional[str] = None
    affected_sectors: Optional[str] = None   # JSON array string
    affected_stocks: Optional[str] = None    # JSON array string
    impact_horizon: Optional[str] = None
    llm_reasoning: Optional[str] = None
    is_actionable: bool = False
    id: Optional[int] = None


@dataclass
class CycleSummary:
    started_at: datetime
    finished_at: Optional[datetime] = None
    spider_results: dict = field(default_factory=dict)
    total_fetched: int = 0
    total_new: int = 0
    total_deduped: int = 0
    errors: List[str] = field(default_factory=list)
