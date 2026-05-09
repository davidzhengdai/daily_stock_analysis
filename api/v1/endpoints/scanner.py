# -*- coding: utf-8 -*-
"""
Cross-market Scanner API endpoints.

POST  /api/v1/scanner/scan          Start a full market scan (background)
GET   /api/v1/scanner/status/{id}   Poll progress of a scan
GET   /api/v1/scanner/results        Latest completed scan result
GET   /api/v1/scanner/results/{id}  Specific scan result by ID
GET   /api/v1/scanner/history       List of past scans
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.services.market_scanner import get_market_scanner
from src.schemas.scanner import ScanConfig

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas (inline — scanner-specific)
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    top_n: int = Field(default=10, ge=1, le=50, description="Number of top picks to return")
    markets: List[str] = Field(default=["us", "cn"], description='Markets to scan: "us" and/or "cn"')
    min_market_cap_m: float = Field(default=500.0, ge=0, description="Minimum market cap (USD millions)")
    min_avg_volume: int = Field(default=500_000, ge=0, description="Minimum average daily volume")
    min_price: float = Field(default=5.0, ge=0, description="Minimum stock price")
    max_price: float = Field(default=3000.0, ge=0, description="Maximum stock price")
    max_tier5_stocks: int = Field(default=30, ge=5, le=100, description="Max stocks for deep LLM analysis")
    max_cn_stocks: int = Field(default=800, ge=50, le=5000, description="Max A-share stocks to include")
    china_policy_weight: float = Field(default=0.25, ge=0, le=1, description="China policy/hot-topic weighting")
    extra_context: str = Field(default="", description="Additional instructions for the LLM")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/scan",
    summary="Start a full cross-market scan",
    description=(
        "Launches a background 5-tier scan of configured US and/or A-share markets. "
        "Returns immediately with a scan_id. Poll /status/{scan_id} for progress."
    ),
    status_code=202,
)
def start_scan(request: ScanRequest = ScanRequest()) -> JSONResponse:
    try:
        scanner = get_market_scanner()
        cfg = ScanConfig(
            top_n=request.top_n,
            markets=[m for m in request.markets if m in ("us", "cn")] or ["us"],
            min_market_cap_m=request.min_market_cap_m,
            min_avg_volume=request.min_avg_volume,
            min_price=request.min_price,
            max_price=request.max_price,
            max_tier5_stocks=request.max_tier5_stocks,
            max_cn_stocks=request.max_cn_stocks,
            china_policy_weight=request.china_policy_weight,
            extra_context=request.extra_context,
        )
        scan_id = scanner.start_scan(scan_config=cfg)
        return JSONResponse(
            status_code=202,
            content={
                "scan_id": scan_id,
                "status": "running",
                "message": "Market scan started in background. Poll /status/{scan_id} for progress.",
                "progress_url": f"/api/v1/scanner/status/{scan_id}",
                "result_url": f"/api/v1/scanner/results/{scan_id}",
            },
        )
    except Exception as exc:
        logger.exception("Failed to start market scan: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/status/{scan_id}",
    summary="Get scan progress",
)
def get_scan_status(scan_id: str) -> JSONResponse:
    scanner = get_market_scanner()
    entry = scanner.get_status(scan_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return JSONResponse(content={
        "scan_id": scan_id,
        "status": entry.get("status"),
        "progress": entry.get("progress", 0),
        "message": entry.get("message", ""),
        "started_at": entry.get("started_at"),
        "completed_at": entry.get("completed_at"),
        "error": entry.get("error"),
    })


@router.get(
    "/results",
    summary="Get the latest completed scan result",
)
def get_latest_result() -> JSONResponse:
    scanner = get_market_scanner()
    result = scanner.get_latest_result()
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No completed scan found. Start a scan first via POST /api/v1/scanner/scan",
        )
    return JSONResponse(content=result)


@router.get(
    "/results/{scan_id}",
    summary="Get a specific scan result by scan_id",
)
def get_result(scan_id: str) -> JSONResponse:
    scanner = get_market_scanner()
    result = scanner.get_result(scan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Result for scan {scan_id!r} not found or not yet completed",
        )
    return JSONResponse(content=result)


@router.get(
    "/history",
    summary="List all past scans",
)
def list_history() -> JSONResponse:
    scanner = get_market_scanner()
    metas = scanner.list_scans()
    return JSONResponse(content={"scans": [m.to_dict() for m in metas]})
