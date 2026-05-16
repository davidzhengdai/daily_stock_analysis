# -*- coding: utf-8 -*-
"""
Sentinel REST endpoints.

GET /api/v1/sentinel/status       — sentinel status & DB stats
GET /api/v1/sentinel/news         — recent high-priority news items
GET /api/v1/sentinel/news/search  — FTS search over news items
GET /api/v1/sentinel/analyses     — cycle analysis records
"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class SentinelStatusResponse(BaseModel):
    enabled: bool
    db_path: str
    total_items: int
    unclassified_count: int
    last_analysis_at: Optional[str] = None


class SentinelNewsItem(BaseModel):
    id: Optional[int] = None
    title: str
    content: str
    source_name: str
    url: str
    priority: Optional[int] = None
    sentiment: Optional[str] = None
    category: Optional[str] = None
    market_scope: Optional[str] = None
    affected_sectors: List[str] = []
    affected_stocks: List[str] = []
    impact_horizon: Optional[str] = None
    llm_reasoning: Optional[str] = None
    is_actionable: bool = False
    published_at: Optional[str] = None
    fetched_at: str = ""


class SentinelAnalysisItem(BaseModel):
    id: Optional[int] = None
    cycle_at: str
    news_count: Optional[int] = None
    themes: List[Any] = []
    sector_opps: List[Any] = []
    stock_leads: List[Any] = []
    risk_alerts: List[Any] = []
    market_mood: Optional[str] = None
    triggered_stocks: List[Any] = []
    created_at: Optional[str] = None


class WatchedStockItem(BaseModel):
    code: str
    name: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_field(value: Any) -> list:
    """Parse a JSON-string field into a Python list; return [] on failure."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _row_to_news_item(row) -> SentinelNewsItem:
    d = dict(row)
    content_raw = d.get("content") or ""
    return SentinelNewsItem(
        id=d.get("id"),
        title=d.get("title") or "",
        content=content_raw[:500],
        source_name=d.get("source_name") or "",
        url=d.get("url") or "",
        priority=d.get("priority"),
        sentiment=d.get("sentiment"),
        category=d.get("category"),
        market_scope=d.get("market_scope"),
        affected_sectors=_parse_json_field(d.get("affected_sectors")),
        affected_stocks=_parse_json_field(d.get("affected_stocks")),
        impact_horizon=d.get("impact_horizon"),
        llm_reasoning=d.get("llm_reasoning"),
        is_actionable=bool(d.get("is_actionable", 0)),
        published_at=d.get("published_at"),
        fetched_at=d.get("fetched_at") or "",
    )


def _row_to_analysis_item(row) -> SentinelAnalysisItem:
    d = dict(row)
    return SentinelAnalysisItem(
        id=d.get("id"),
        cycle_at=d.get("cycle_at") or "",
        news_count=d.get("news_count"),
        themes=_parse_json_field(d.get("themes")),
        sector_opps=_parse_json_field(d.get("sector_opps")),
        stock_leads=_parse_json_field(d.get("stock_leads")),
        risk_alerts=_parse_json_field(d.get("risk_alerts")),
        market_mood=d.get("market_mood"),
        triggered_stocks=_parse_json_field(d.get("triggered_stocks")),
        created_at=d.get("created_at"),
    )


def _get_store_and_config():
    """Lazy-import and return (config, store) or raise RuntimeError."""
    from src.services.sentinel.config import SentinelConfig
    from src.services.sentinel.store import NewsStore
    cfg = SentinelConfig.from_env()
    store = NewsStore(db_path=cfg.db_path)
    return cfg, store


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=SentinelStatusResponse,
    summary="Sentinel status & DB stats",
)
def get_status() -> SentinelStatusResponse:
    try:
        cfg, store = _get_store_and_config()
        return SentinelStatusResponse(
            enabled=cfg.enabled,
            db_path=cfg.db_path,
            total_items=store.count(),
            unclassified_count=store.count_unclassified(),
            last_analysis_at=store.get_last_cycle_analysis_at(),
        )
    except Exception as exc:
        logger.warning("sentinel /status error: %s", exc)
        return SentinelStatusResponse(
            enabled=False,
            db_path="",
            total_items=0,
            unclassified_count=0,
            last_analysis_at=None,
        )


@router.get(
    "/news",
    response_model=List[SentinelNewsItem],
    summary="Recent high-priority news items",
)
def get_news(
    hours: int = Query(default=24, ge=1, le=720, description="Look-back window in hours"),
    priority_min: int = Query(default=3, ge=1, le=5, description="Minimum priority level"),
    limit: int = Query(default=50, ge=1, le=200, description="Max items to return"),
) -> List[SentinelNewsItem]:
    try:
        _, store = _get_store_and_config()
        rows = store.get_recent_classified(hours=hours, priority_min=priority_min, limit=limit)
        return [_row_to_news_item(r) for r in rows]
    except Exception as exc:
        logger.warning("sentinel /news error: %s", exc)
        return []


@router.get(
    "/news/search",
    response_model=List[SentinelNewsItem],
    summary="FTS search over news items",
)
def search_news(
    q: str = Query(..., description="Full-text search query"),
    limit: int = Query(default=20, ge=1, le=100, description="Max items to return"),
) -> List[SentinelNewsItem]:
    try:
        _, store = _get_store_and_config()
        rows = store.search_fts(q, limit=limit)
        return [_row_to_news_item(r) for r in rows]
    except Exception as exc:
        logger.warning("sentinel /news/search error: %s", exc)
        return []


@router.get(
    "/analyses",
    response_model=List[SentinelAnalysisItem],
    summary="Cycle analysis records",
)
def get_analyses(
    limit: int = Query(default=10, ge=1, le=50, description="Max items to return"),
) -> List[SentinelAnalysisItem]:
    try:
        _, store = _get_store_and_config()
        rows = store.get_cycle_analyses(limit=limit)
        return [_row_to_analysis_item(r) for r in rows]
    except Exception as exc:
        logger.warning("sentinel /analyses error: %s", exc)
        return []


@router.get("/watched-stocks", response_model=List[dict], summary="List current watched stocks")
def get_watched_stocks() -> List[dict]:
    try:
        _, store = _get_store_and_config()
        return store.get_watched_stocks()
    except Exception as exc:
        logger.warning("sentinel /watched-stocks GET error: %s", exc)
        return []


@router.put("/watched-stocks", summary="Set watched stock list for targeted scraping")
def set_watched_stocks(stocks: List[WatchedStockItem]) -> dict:
    try:
        _, store = _get_store_and_config()
        data = [{"code": s.code.strip(), "name": s.name.strip()} for s in stocks if s.code.strip()]
        count = store.upsert_watched_stocks(data)
        return {"updated": count}
    except Exception as exc:
        logger.warning("sentinel /watched-stocks PUT error: %s", exc)
        return {"updated": 0, "error": str(exc)}
