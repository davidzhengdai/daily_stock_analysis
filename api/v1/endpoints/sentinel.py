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
import os
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse, request

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
    watched_stocks_count: int = 0


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


def _sentinel_server_url() -> str:
    return os.getenv("SENTINEL_SERVER_URL", "").strip().rstrip("/")


def _http_json(
    path: str,
    *,
    query: Optional[dict] = None,
    method: str = "GET",
    body: Any = None,
) -> Optional[Any]:
    base_url = _sentinel_server_url()
    if not base_url:
        return None
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, urlerror.URLError, json.JSONDecodeError) as exc:
        logger.warning("sentinel HTTP proxy failed for %s: %s", path, exc)
        return None


def _items_payload(payload: Any) -> Optional[list]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=SentinelStatusResponse,
    summary="Sentinel status & DB stats",
)
def get_status() -> SentinelStatusResponse:
    payload = _http_json("/status")
    if isinstance(payload, dict):
        return SentinelStatusResponse(
            enabled=bool(payload.get("enabled", False)),
            db_path=str(payload.get("db_path") or ""),
            total_items=int(payload.get("total_items") or 0),
            unclassified_count=int(payload.get("unclassified_count") or 0),
            last_analysis_at=payload.get("last_analysis_at"),
            watched_stocks_count=int(payload.get("watched_stocks_count") or 0),
        )
    try:
        cfg, store = _get_store_and_config()
        return SentinelStatusResponse(
            enabled=cfg.enabled,
            db_path=cfg.db_path,
            total_items=store.count(),
            unclassified_count=store.count_unclassified(),
            last_analysis_at=store.get_last_cycle_analysis_at(),
            watched_stocks_count=len(store.get_watched_stocks()),
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
    payload = _http_json(
        "/news",
        query={"hours": hours, "priority_min": priority_min, "limit": limit},
    )
    items = _items_payload(payload)
    if items is not None:
        return [SentinelNewsItem(**item) for item in items if isinstance(item, dict)]
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
    payload = _http_json("/news/search", query={"q": q, "limit": limit})
    items = _items_payload(payload)
    if items is not None:
        return [SentinelNewsItem(**item) for item in items if isinstance(item, dict)]
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
    payload = _http_json("/analyses", query={"limit": limit})
    items = _items_payload(payload)
    if items is not None:
        return [SentinelAnalysisItem(**item) for item in items if isinstance(item, dict)]
    try:
        _, store = _get_store_and_config()
        rows = store.get_cycle_analyses(limit=limit)
        return [_row_to_analysis_item(r) for r in rows]
    except Exception as exc:
        logger.warning("sentinel /analyses error: %s", exc)
        return []


@router.get("/watched-stocks", response_model=List[dict], summary="List current watched stocks")
def get_watched_stocks() -> List[dict]:
    payload = _http_json("/watched-stocks")
    items = _items_payload(payload)
    if items is not None:
        return [item for item in items if isinstance(item, dict)]
    try:
        _, store = _get_store_and_config()
        return store.get_watched_stocks()
    except Exception as exc:
        logger.warning("sentinel /watched-stocks GET error: %s", exc)
        return []


@router.put("/watched-stocks", summary="Set watched stock list for targeted scraping")
def set_watched_stocks(
    stocks: List[WatchedStockItem],
    merge: bool = Query(default=False, description="If true, append to existing list instead of replacing"),
) -> dict:
    payload = _http_json(
        "/watched-stocks",
        query={"merge": str(merge).lower()},
        method="PUT",
        body=[s.dict() for s in stocks],
    )
    if isinstance(payload, dict):
        return payload
    try:
        _, store = _get_store_and_config()
        data = [{"code": s.code.strip(), "name": s.name.strip()} for s in stocks if s.code.strip()]
        if merge:
            count = sum(1 for s in data if store.append_watched_stock(s["code"], s["name"]))
        else:
            count = store.upsert_watched_stocks(data)
        return {"updated": count, "merge": merge}
    except Exception as exc:
        logger.warning("sentinel /watched-stocks PUT error: %s", exc)
        return {"updated": 0, "error": str(exc)}


class FetchNowItem(BaseModel):
    code: str
    name: str = ""


@router.post("/fetch-now", summary="Immediately fetch and classify news for a single stock")
def fetch_now(item: FetchNowItem) -> dict:
    """Fetch, store, and LLM-classify news for one stock right now.

    Also registers the stock for ongoing targeted fetching in future cycles.
    Useful when a user triggers analysis of a stock not yet in the watchlist.
    """
    code = item.code.strip()
    name = item.name.strip()
    if not code:
        return {"code": "", "fetched": 0, "new": 0, "classified": 0, "error": "code is required"}
    payload = _http_json("/fetch-now", method="POST", body={"code": code, "name": name})
    if isinstance(payload, dict):
        return payload
    try:
        cfg, store = _get_store_and_config()
        from src.services.sentinel.spiders.watched_stocks import WatchedStocksNewsSpider
        from src.services.sentinel.dedup import Deduplicator
        from src.services.sentinel.classifier import LLMClassifier

        spider = WatchedStocksNewsSpider(store)
        articles = spider.fetch_single(code, name)

        deduper = Deduplicator(store)
        new_count = 0
        for article in articles:
            if article.url and deduper.is_new(article):
                if store.upsert(article):
                    new_count += 1

        classified = 0
        if new_count > 0:
            classifier = LLMClassifier(cfg)
            classified = classifier.classify_pending(store)

        # Register for ongoing targeted fetching in future cycles
        store.append_watched_stock(code, name)

        return {"code": code, "fetched": len(articles), "new": new_count, "classified": classified}
    except Exception as exc:
        logger.warning("sentinel /fetch-now error for %s: %s", code, exc)
        return {"code": code, "fetched": 0, "new": 0, "classified": 0, "error": str(exc)}
