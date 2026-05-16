# -*- coding: utf-8 -*-
"""
===================================
自选股 API 接口
===================================

职责：
1. GET    /api/v1/watchlist/          — 查询自选股列表
2. POST   /api/v1/watchlist/          — 添加自选股
3. DELETE /api/v1/watchlist/{code}   — 删除自选股
4. PATCH  /api/v1/watchlist/{code}   — 更新自选股信息
5. POST   /api/v1/watchlist/analyze  — 触发分析（选中或全部）
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.services.watchlist_service import WatchlistService

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# Pydantic 模型
# ============================================================

class WatchlistAddRequest(BaseModel):
    code: str = Field(..., description="股票代码")
    name: str = Field("", description="股票名称")
    notes: str = Field("", description="备注")


class WatchlistPatchRequest(BaseModel):
    name: Optional[str] = Field(None, description="股票名称")
    notes: Optional[str] = Field(None, description="备注")


class WatchlistAnalyzeRequest(BaseModel):
    codes: Optional[List[str]] = Field(None, description="指定股票代码列表；为空则分析全部")


class WatchlistItem(BaseModel):
    code: str
    name: str
    added_at: Optional[str]
    notes: str
    last_analyzed_at: Optional[str]


class WatchlistListResponse(BaseModel):
    items: List[WatchlistItem]
    total: int


class WatchlistAnalyzeResponse(BaseModel):
    submitted: int
    codes: List[str]


# ============================================================
# Helper
# ============================================================

def _not_found(code: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": f"股票代码 {code} 不在自选股列表中"},
    )


def _internal_error(msg: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", msg, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{msg}: {exc}"},
    )


# ============================================================
# GET / — 列表
# ============================================================

@router.get(
    "/",
    response_model=WatchlistListResponse,
    summary="获取自选股列表",
)
def list_watchlist() -> WatchlistListResponse:
    service = WatchlistService()
    try:
        items = service.list_all()
        return WatchlistListResponse(
            items=[WatchlistItem(**item) for item in items],
            total=len(items),
        )
    except Exception as exc:
        raise _internal_error("获取自选股列表失败", exc)


# ============================================================
# POST / — 添加
# ============================================================

@router.post(
    "/",
    response_model=WatchlistItem,
    status_code=201,
    summary="添加自选股",
)
def add_watchlist(request: WatchlistAddRequest) -> WatchlistItem:
    code = (request.code or "").strip().upper()
    if not code:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": "股票代码不能为空"},
        )
    service = WatchlistService()
    try:
        item = service.add(code=code, name=request.name or "", notes=request.notes or "")
        return WatchlistItem(**item)
    except Exception as exc:
        raise _internal_error("添加自选股失败", exc)


# ============================================================
# DELETE /{code} — 删除
# ============================================================

@router.delete(
    "/{code}",
    summary="删除自选股",
)
def remove_watchlist(code: str) -> JSONResponse:
    code = (code or "").strip().upper()
    service = WatchlistService()
    try:
        removed = service.remove(code)
        if not removed:
            raise _not_found(code)
        return JSONResponse(
            status_code=200,
            content={"message": f"已从自选股中移除: {code}"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("删除自选股失败", exc)


# ============================================================
# PATCH /{code} — 更新名称/备注
# ============================================================

@router.patch(
    "/{code}",
    response_model=WatchlistItem,
    summary="更新自选股信息",
)
def update_watchlist(code: str, request: WatchlistPatchRequest) -> WatchlistItem:
    code = (code or "").strip().upper()
    service = WatchlistService()
    try:
        existing = service.repo.get(code)
        if existing is None:
            raise _not_found(code)
        name = request.name if request.name is not None else existing["name"]
        notes = request.notes if request.notes is not None else existing["notes"]
        item = service.add(code=code, name=name, notes=notes)
        return WatchlistItem(**item)
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("更新自选股失败", exc)


# ============================================================
# POST /analyze — 触发分析
# ============================================================

@router.post(
    "/analyze",
    response_model=WatchlistAnalyzeResponse,
    summary="触发自选股分析",
)
def analyze_watchlist(request: WatchlistAnalyzeRequest) -> WatchlistAnalyzeResponse:
    """
    触发自选股分析任务。

    - 若 codes 为空则分析全部自选股。
    - 直接向任务队列提交，不阻塞。
    - 跳过重复任务（已在队列中的），只统计实际提交数。
    """
    service = WatchlistService()
    try:
        if request.codes:
            codes = [c.strip().upper() for c in request.codes if c.strip()]
        else:
            items = service.list_all()
            codes = [item["code"] for item in items]

        if not codes:
            return WatchlistAnalyzeResponse(submitted=0, codes=[])

        from src.services.task_queue import get_task_queue, DuplicateTaskError

        task_queue = get_task_queue()
        accepted, _duplicates = task_queue.submit_tasks_batch(
            stock_codes=codes,
            selection_source="manual",
            report_type="detailed",
            notify=True,
        )

        submitted_codes = [task.stock_code for task in accepted]
        return WatchlistAnalyzeResponse(submitted=len(accepted), codes=submitted_codes)
    except Exception as exc:
        raise _internal_error("触发自选股分析失败", exc)
