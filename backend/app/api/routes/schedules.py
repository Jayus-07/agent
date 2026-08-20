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


@router.post("/{workflow_name}/run")
async def run_schedule_now(workflow_name: str) -> dict:
    """立即运行指定定时任务（2026-08-11）。

    - workflow 类型：调 scheduler.run_now
    - weekly_eval：直接调评测脚本
    """
    sched = get_workflow_scheduler()
    job = sched.get_job(workflow_name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"schedule {workflow_name} not found")

    # 区分任务类型
    if workflow_name == "weekly_eval":
        # 2026-08-21 P1-2: 迁移到 backend.evaluation（旧 backend.eval 已删除）
        try:
            from backend.evaluation.weekly import run_weekly_rag_eval
            summary = run_weekly_rag_eval()
            if not summary.get("ok"):
                return {"ok": False, "error": summary.get("error", "no summary")}
            logger.info(
                f"[Schedules API] weekly_eval 手动触发完成: "
                f"pass={summary['pass_rate']:.1%} top1={summary['top1_accuracy']:.1%}"
            )
            return {
                "ok": True,
                "schedule": workflow_name,
                "triggered_at": "now",
                "summary": {
                    "total": summary["total"],
                    "passed": summary["passed"],
                    "pass_rate": summary["pass_rate"],
                    "top1_accuracy": summary["top1_accuracy"],
                    "reject_accuracy": summary["reject_accuracy"],
                },
            }
        except Exception as e:
            logger.error(f"[Schedules API] weekly_eval 手动触发失败: {e}")
            return {"ok": False, "error": str(e)}

    # workflow 类型
    try:
        job_id = await sched.run_now(workflow_name)
        logger.info(f"[Schedules API] {workflow_name} 手动触发完成: {job_id}")
        return {"ok": True, "schedule": workflow_name, "triggered_at": "now", "job_id": job_id}
    except Exception as e:
        logger.error(f"[Schedules API] {workflow_name} 手动触发失败: {e}")
        return {"ok": False, "error": str(e)}
