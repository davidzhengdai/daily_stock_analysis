# -*- coding: utf-8 -*-
"""
淘金列表 API endpoints

GET  /api/v1/discovery/           — 列出活跃条目（可按 source/market 过滤）
POST /api/v1/discovery/{id}/reject — 标记拒绝
GET  /api/v1/discovery/stats      — 按来源和状态统计
GET  /api/v1/discovery/history    — 淘金列表变更历史
GET  /api/v1/discovery/runs       — HeatRadar 历史运行列表
POST /api/v1/discovery/heat-scan  — 触发一次热点雷达扫描（异步）
GET  /api/v1/discovery/heat-scan/{run_id}/status — 查询扫描进度
GET  /api/v1/discovery/heat-scan/{run_id}/result  — 获取扫描结果
"""

import logging
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class HeatScanRequest(BaseModel):
    top_n: int = Field(default=10, ge=1, le=50)
    markets: List[str] = Field(default=["us", "cn"])
    ttl_days: int = Field(default=5, ge=1, le=30)
    theme_count: int = Field(default=8, ge=3, le=15)
    max_stocks_per_sector: int = Field(default=3, ge=1, le=5)
    model: str = Field(default="")


def _dedupe_discovery_items(items: List[dict]) -> List[dict]:
    """按股票和市场合并活跃候选，保留分数最高、更新时间更近的一条。"""
    deduped: Dict[Tuple[str, str], dict] = {}
    for item in items:
        ticker = str(item.get("ticker") or "").strip().upper()
        market = str(item.get("market") or "").strip().upper()
        key = (ticker, market)
        if not ticker:
            continue
        current = deduped.get(key)
        if current is None:
            deduped[key] = item
            continue
        item_score = float(item.get("score") or 0)
        current_score = float(current.get("score") or 0)
        if item_score > current_score:
            deduped[key] = item
        elif item_score == current_score and str(item.get("added_at") or "") > str(current.get("added_at") or ""):
            deduped[key] = item
    return sorted(
        deduped.values(),
        key=lambda value: (float(value.get("score") or 0), str(value.get("added_at") or "")),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# 淘金列表端点
# ---------------------------------------------------------------------------

@router.get(
    "/",
    summary="列出淘金列表活跃条目",
    description="返回 status=active 且未过期的所有条目，支持按来源和市场过滤。",
)
def list_discovery(
    source: Optional[str] = Query(None, description="来源过滤: scanner | gold_digger | heat_radar"),
    market: Optional[str] = Query(None, description="市场过滤: CN | US"),
    limit: int = Query(50, ge=1, le=200),
    refresh: bool = Query(False, description="返回前刷新实时价格"),
) -> JSONResponse:
    try:
        from src.services.discovery_service import DiscoveryService
        items = DiscoveryService().list_active()
        if source:
            items = [i for i in items if i.get("source") == source]
        if market:
            items = [i for i in items if i.get("market", "").upper() == market.upper()]
        items = _dedupe_discovery_items(items)
        items = items[:limit]
        if refresh and items:
            from src.services.realtime_quote_service import RealtimeQuoteService
            quotes = RealtimeQuoteService().get_quotes(item.get("ticker", "") for item in items)
            for item in items:
                item["quote"] = quotes.get((item.get("ticker") or "").upper())
        return JSONResponse({"items": items, "count": len(items)})
    except Exception as exc:
        logger.exception("列出淘金列表失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/stats",
    summary="淘金列表统计",
    description="返回各来源和状态的条目数量。",
)
def discovery_stats() -> JSONResponse:
    try:
        from src.services.discovery_service import DiscoveryService
        stats = DiscoveryService().stats()
        return JSONResponse({"stats": stats})
    except Exception as exc:
        logger.exception("获取淘金列表统计失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/history",
    summary="淘金列表变更历史",
    description="返回淘金列表加入、过期、拒绝等变更事件，包含原因和来源信息。",
)
def discovery_history(
    ticker: Optional[str] = Query(None, description="按股票代码过滤"),
    item_id: Optional[int] = Query(None, description="按淘金条目 ID 过滤"),
    limit: int = Query(100, ge=1, le=500),
) -> JSONResponse:
    try:
        from src.services.discovery_service import DiscoveryService
        events = DiscoveryService().list_history(
            ticker=ticker,
            item_id=item_id,
            limit=limit,
        )
        return JSONResponse({"events": events, "count": len(events)})
    except Exception as exc:
        logger.exception("获取淘金列表历史失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/{item_id}/reject",
    summary="拒绝淘金列表条目",
    description="将指定条目标记为 rejected，AutoTrade 将不再处理该条目。",
)
def reject_item(item_id: int) -> JSONResponse:
    try:
        from src.services.discovery_service import DiscoveryService
        ok = DiscoveryService().reject(item_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"条目 {item_id} 不存在")
        return JSONResponse({"ok": True, "item_id": item_id})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("拒绝条目 %d 失败: %s", item_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# 热点雷达端点
# ---------------------------------------------------------------------------

@router.get(
    "/runs",
    summary="热点雷达历史运行列表",
)
def list_heat_runs() -> JSONResponse:
    try:
        from src.services.news_heat_radar import get_news_heat_radar
        metas = get_news_heat_radar().list_runs()
        return JSONResponse({"runs": [m.to_dict() for m in metas], "count": len(metas)})
    except Exception as exc:
        logger.exception("获取热点雷达历史失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/heat-scan",
    summary="触发热点雷达扫描",
    description="启动一次后台热点雷达扫描，立即返回 run_id，通过 status 端点轮询进度。",
)
def start_heat_scan(req: HeatScanRequest) -> JSONResponse:
    try:
        from src.config import get_config
        from src.schemas.news_heat_radar import HeatConfig
        from src.services.news_heat_radar import get_news_heat_radar
        if not getattr(get_config(), "heat_radar_enabled", True):
            raise HTTPException(status_code=403, detail="HeatRadar is disabled")
        cfg = HeatConfig(
            top_n=req.top_n,
            markets=req.markets,
            ttl_days=req.ttl_days,
            theme_count=req.theme_count,
            max_stocks_per_sector=req.max_stocks_per_sector,
            model=req.model,
        )
        run_id = get_news_heat_radar().start_heat_scan(cfg)
        return JSONResponse({"run_id": run_id, "status": "running"})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("启动热点雷达失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/heat-scan/{run_id}/status",
    summary="查询热点雷达扫描进度",
)
def get_heat_scan_status(run_id: str) -> JSONResponse:
    try:
        from src.services.news_heat_radar import get_news_heat_radar
        status = get_news_heat_radar().get_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
        return JSONResponse(status)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("查询热点雷达状态失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/heat-scan/{run_id}/result",
    summary="获取热点雷达扫描结果",
)
def get_heat_scan_result(run_id: str) -> JSONResponse:
    try:
        from src.services.news_heat_radar import get_news_heat_radar
        report = get_news_heat_radar().get_result(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id} 结果不存在")
        return JSONResponse(report.to_dict())
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("获取热点雷达结果失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
