"""app/api/routes/schedules.py — 定时任务配置 API

端点：
- GET  /api/schedules              列出所有定时任务
- GET  /api/schedules/{workflow}    单个任务详情
- PATCH /api/schedules/{workflow}   修改定时（hour/min/enabled）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.orchestration.workflow.scheduler import get_workflow_scheduler
from backend.orchestration.workflow.registry import get_workflow_registry
from backend.shared.logger import logger

router = APIRouter(prefix="/schedules", tags=["定时任务"])


@router.get("")
async def list_schedules() -> dict[str, Any]:
    """列出所有定时任务（含下次执行时间）"""
    sched = get_workflow_scheduler()
    jobs = sched.list_jobs()
    # 补 workflow metadata（名称、描述）
    reg = get_workflow_registry()
    for job in jobs:
        meta = reg.get_meta(job.get("workflow", ""))
        if meta:
            job["description"] = meta.description
    return {"schedules": jobs, "total": len(jobs)}


@router.get("/{workflow_name}")
async def get_schedule(workflow_name: str) -> dict[str, Any]:
    """单个定时任务详情"""
    sched = get_workflow_scheduler()
    job = sched.get_job(workflow_name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"schedule {workflow_name} not found")
    reg = get_workflow_registry()
    meta = reg.get_meta(workflow_name)
    if meta:
        job["description"] = meta.description
    return {"schedule": job}


@router.patch("/{workflow_name}")
async def update_schedule(
    workflow_name: str,
    body: dict = Body(...),
) -> dict[str, Any]:
    """修改定时任务（hour / minute）

    body:
    - hour: int (0-23)
    - minute: int (0-59)
    """
    sched = get_workflow_scheduler()
    existing = sched.get_job(workflow_name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"schedule {workflow_name} not found")

    hour = body.get("hour")
    minute = body.get("minute", 0)

    if hour is None:
        raise HTTPException(status_code=400, detail="missing hour")
    if not (0 <= hour <= 23):
        raise HTTPException(status_code=400, detail="hour must be 0-23")
    if not (0 <= minute <= 59):
        raise HTTPException(status_code=400, detail="minute must be 0-59")

    job_id = sched.reschedule_daily(workflow_name, hour=hour, minute=minute)
    logger.info(f"[Schedules API] {workflow_name} 已重新调度: {hour:02d}:{minute:02d}")

    updated = sched.get_job(workflow_name)
    return {"updated": True, "job_id": job_id, "schedule": updated}
