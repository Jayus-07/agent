"""app/api/routes/reports.py — 报告中心 API

端点：
- GET /api/reports             报告列表（按 type 过滤）
- GET /api/reports/latest      最新报告（含完整 content）
- GET /api/reports/{report_id} 报告详情
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.seed.demo.runner import get_daily_report_store

router = APIRouter(prefix="/reports", tags=["报告中心"])


@router.get("")
async def list_reports(
    type: str = Query(default="daily_report"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """报告列表（按类型 + 分页）"""
    store = get_daily_report_store()
    reports = store.list(report_type=type, page=page, page_size=page_size)
    return {"reports": reports, "page": page, "page_size": page_size}


@router.get("/latest")
async def get_latest_report(
    type: str = Query(default="daily_report"),
) -> dict[str, Any]:
    """最新一条报告（含完整内容 + KPI 摘要）"""
    store = get_daily_report_store()
    report = store.get_latest(report_type=type)
    return {"report": report}


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict[str, Any]:
    """报告详情"""
    store = get_daily_report_store()
    report = store.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")
    return {"report": report}
