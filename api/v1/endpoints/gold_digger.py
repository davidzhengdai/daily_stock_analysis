# -*- coding: utf-8 -*-
"""
沙里淘金 (Gold Digger) API endpoints.

POST  /api/v1/gold-digger/dig          Start a dig run (background)
GET   /api/v1/gold-digger/status/{id}  Poll progress
GET   /api/v1/gold-digger/results      Latest completed result
GET   /api/v1/gold-digger/results/{id} Specific result by run_id
GET   /api/v1/gold-digger/history      List of past dig runs
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.schemas.gold_digger import DigConfig
from src.services.gold_digger import get_gold_digger

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class DigRequest(BaseModel):
    top_n: int = Field(default=10, ge=1, le=30, description="Number of gold picks to return")
    markets: List[str] = Field(
        default=["us", "cn"],
        description='Markets to scan: "us" and/or "cn"',
    )
    us_min_market_cap_m: float = Field(
        default=50.0, ge=10, description="US min market cap (USD millions)"
    )
    us_max_market_cap_m: float = Field(
        default=1000.0, ge=100, description="US max market cap (USD millions)"
    )
    min_price_decline_6m_pct: float = Field(
        default=20.0, ge=5, description="Minimum 6-month price decline % to qualify as beaten-down"
    )
    min_pe_discount_pct: float = Field(
        default=10.0, ge=0, description="Minimum PE discount vs sector median % to qualify as cheap"
    )
    max_tier5_per_market: int = Field(
        default=15, ge=5, le=50, description="Max candidates for deep LLM analysis per market"
    )
    theme_count: int = Field(
        default=8, ge=3, le=15, description="Number of macro themes to detect"
    )
    china_policy_weight: float = Field(
        default=0.25,
        ge=0,
        le=1,
        description="Extra ranking weight for China policy and national hot-topic themes",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/dig",
    summary="Start a 沙里淘金 run",
    description=(
        "Scans garbage stocks (small-cap, beaten-down, PE-cheap) in US and A-share markets, "
        "matches them against current macro themes, and ranks hidden gems via LLM. "
        "Returns immediately with a run_id. Poll /status/{run_id} for progress."
    ),
    status_code=202,
)
async def start_dig(req: DigRequest):
    digger = get_gold_digger()
    cfg = DigConfig(
        top_n=req.top_n,
        markets=[m for m in req.markets if m in ("us", "cn")] or ["us"],
        us_min_market_cap_m=req.us_min_market_cap_m,
        us_max_market_cap_m=req.us_max_market_cap_m,
        min_price_decline_6m_pct=req.min_price_decline_6m_pct,
        min_pe_discount_pct=req.min_pe_discount_pct,
        max_tier5_per_market=req.max_tier5_per_market,
        theme_count=req.theme_count,
        china_policy_weight=req.china_policy_weight,
    )
    run_id = digger.start_dig(cfg)
    logger.info("Gold dig started: %s", run_id)
    return JSONResponse(
        status_code=202,
        content={"run_id": run_id, "status": "started", "message": "Dig run launched"},
    )


@router.get(
    "/status/{run_id}",
    summary="Poll dig run progress",
)
async def get_dig_status(run_id: str):
    digger = get_gold_digger()
    status = digger.get_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {
        "run_id": run_id,
        "status": status.get("status", "unknown"),
        "progress": status.get("progress", 0),
        "message": status.get("message", ""),
    }


@router.get(
    "/results",
    summary="Get latest completed dig result",
)
async def get_latest_result():
    digger = get_gold_digger()
    report = digger.get_latest_result()
    if report is None:
        raise HTTPException(status_code=404, detail="No completed dig run found")
    return report.to_dict()


@router.get(
    "/results/{run_id}",
    summary="Get specific dig result by run_id",
)
async def get_result(run_id: str):
    digger = get_gold_digger()
    report = digger.get_result(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Result {run_id} not found")
    return report.to_dict()


@router.get(
    "/history",
    summary="List past dig runs",
)
async def list_history():
    digger = get_gold_digger()
    runs = digger.list_runs()
    return [r.to_dict() for r in runs]
