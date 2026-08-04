"""backend/app/api/routes/workflows.py — Workflow API

端点：
- GET /api/workflows                 — 列出所有注册的 Workflow
- POST /api/workflows/{name}/trigger — 手动触发
- GET /api/workflows/runs            — 列出历史运行
- GET /api/workflows/runs/{run_id}   — 单次运行详情
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.orchestration.workflow.registry import get_workflow_registry
from backend.orchestration.workflow.scheduler import get_workflow_scheduler
from backend.orchestration.workflow.persistence import get_workflow_run_store
from backend.shared.logger import logger


router = APIRouter(prefix="/workflows", tags=["Workflow"])


@router.get("")
async def list_workflows() -> dict[str, Any]:
    """列出所有注册的 Workflow（含 metadata）"""
    reg = get_workflow_registry()
    scheduler = get_workflow_scheduler()
    metas = reg.list_metas()
    return {
        "workflows": [
            {
                "name": m.name,
                "description": m.description,
                "objects": m.objects,
                "actions": m.actions,
                "examples": m.examples,
                "default_kbs": m.default_kbs,
                "jobs": scheduler.list_jobs(),
            }
            for m in metas
        ],
        "total": len(metas),
    }


@router.get("/runs")
async def list_runs(
    workflow_name: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """列出 workflow run 历史"""
    store = get_workflow_run_store()
    runs = store.list(workflow_name=workflow_name, page=page, page_size=page_size)
    return {
        "runs": runs,
        "page": page,
        "page_size": page_size,
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """获取单次 run 详情（含 outputs）"""
    store = get_workflow_run_store()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


@router.post("/{name}/trigger")
async def trigger_workflow(
    name: str,
    inputs: dict = Body(default_factory=dict),
) -> dict[str, Any]:
    """手动触发 workflow（异步执行，立即返回 run_id）"""
    reg = get_workflow_registry()
    if reg.get(name) is None:
        raise HTTPException(status_code=404, detail=f"workflow {name} not found")

    scheduler = get_workflow_scheduler()
    ctx = await scheduler.run_now(name, inputs)
    return {
        "run_id": ctx.run_id,
        "workflow_name": ctx.workflow_name,
        "status": ctx.status,
        "started_at": ctx.started_at.isoformat(),
        "finished_at": ctx.finished_at.isoformat() if ctx.finished_at else None,
        "duration_ms": ctx.duration_ms,
        "outputs_keys": list(ctx.outputs.keys()),
        "error": ctx.error,
        "skip_steps": list(ctx.skip_steps),
        "trace_id": ctx.trace_id,
    }