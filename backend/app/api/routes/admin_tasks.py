"""app/api/routes/admin_tasks.py — 管理端任务中心 API（跨用户，管理员专用）。

与用户侧 routes/tasks.py 的区别：不做 user_id 隔离（隔离闸换成管理员闸），
暴露全局筛选/统计/重试/撤销/堆栈详情。所有操作端点要求 body.confirm=true
（二次确认约束的后端侧）+ 同任务 60s 操作冷却（防连点重复投递）。

审计：操作一律打 [AdminTaskAudit] 结构化日志（actor/action/task/结果），
网关访问日志（ai.gateway_access_logs）天然记录了 HTTP 层 who/when，
两层合起来即操作审计闭环。

网关映射：APISIX 剥 /api 前缀 → 对外 /api/admin/tasks/*。
"""
from __future__ import annotations

import time
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.api.routes.observability import require_admin_operator
from backend.models.task import TaskStatus
from backend.shared.logger import logger
from backend.services import task_service
from backend.tasks import task_manager

router = APIRouter(prefix="/admin/tasks", tags=["管理端-任务中心"])

# 操作冷却：task_id → 上次操作时刻（防连点；进程级即可，API 多副本时以
# 网关限流兜底——误重试的成本只是多跑一次幂等任务，非资损操作）
_OP_COOLDOWN_S = 60.0
_op_last: dict[str, float] = {}
_op_lock = threading.Lock()


class AdminOpRequest(BaseModel):
    confirm: bool = Field(False, description="二次确认：必须显式传 true")
    reason: str = Field("", max_length=500, description="操作原因（入审计）")


def _cooldown(task_id: str) -> None:
    now = time.monotonic()
    with _op_lock:
        last = _op_last.get(task_id, 0)
        if now - last < _OP_COOLDOWN_S:
            raise HTTPException(
                status_code=429,
                detail=f"同一任务 {int(_OP_COOLDOWN_S)}s 内已操作过，请稍后再试")
        _op_last[task_id] = now


def _audit(actor: str, action: str, task_id: str, result: str) -> None:
    logger.warning("[AdminTaskAudit] actor=%s action=%s task=%s result=%s",
                   actor, action, task_id, result)


# ═══════════════════════════════════════════════════
# GET /admin/tasks — 全局列表（多条件筛选 + 分页）
# ═══════════════════════════════════════════════════

@router.get("")
async def admin_list_tasks(
    request: Request,
    status: str = Query("", description="状态过滤"),
    graph_name: str = Query("", description="任务图/任务名过滤"),
    queue: str = Query("", description="队列过滤"),
    worker: str = Query("", description="执行节点模糊过滤"),
    user_id: str = Query("", description="归属用户过滤"),
    tenant_id: str = Query("", description="租户过滤"),
    biz_type: str = Query("", description="业务类型过滤"),
    biz_id: str = Query("", description="业务对象 id 过滤"),
    trace_id: str = Query("", description="链路追踪 id 过滤"),
    retries_gt: int | None = Query(None, ge=0, description="重试次数 > N（传 0 即『重试过』）"),
    hours: float = Query(24 * 7, gt=0, le=24 * 90, description="时间窗（小时）"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    await require_admin_operator(request)
    records, total = task_service.list_tasks_admin(
        status=status, graph_name=graph_name, queue=queue, worker=worker,
        user_id=user_id, tenant_id=tenant_id, biz_type=biz_type, biz_id=biz_id,
        trace_id=trace_id, retries_gt=retries_gt,
        hours=hours, limit=limit, offset=offset)
    return {"tasks": [r.to_public_dict() for r in records], "total": total,
            "limit": limit, "offset": offset}


# ═══════════════════════════════════════════════════
# GET /admin/tasks/stats — 统计聚合（须在 /{task_id} 前注册）
# ═══════════════════════════════════════════════════

@router.get("/stats")
async def admin_task_stats(request: Request,
                           hours: float = Query(24, gt=0, le=24 * 90)):
    await require_admin_operator(request)
    return task_service.stats_tasks(hours=hours)


# ═══════════════════════════════════════════════════
# GET /admin/tasks/{task_id} — 详情（含 traceback / input）
# ═══════════════════════════════════════════════════

@router.get("/{task_id}")
async def admin_get_task(task_id: str, request: Request):
    await require_admin_operator(request)
    record = task_service.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return record.to_admin_dict()


@router.get("/{task_id}/checkpoints")
async def admin_get_checkpoints(task_id: str, request: Request):
    await require_admin_operator(request)
    if task_service.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"checkpoints": task_service.list_checkpoints_admin(task_id)}


# ═══════════════════════════════════════════════════
# POST /admin/tasks/{task_id}/retry — 重试（复用 resume：checkpoint 续跑）
# ═══════════════════════════════════════════════════

@router.post("/{task_id}/retry")
async def admin_retry_task(task_id: str, body: AdminOpRequest, request: Request):
    await require_admin_operator(request)
    actor = request.state.actor if hasattr(request.state, "actor") else "admin"
    if not body.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true（二次确认）")
    record = task_service.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status not in TaskStatus.resumable():
        raise HTTPException(
            status_code=409,
            detail=f"仅 {', '.join(s.value for s in TaskStatus.resumable())} 状态可重试"
                   f"（当前 {record.status.value}）")
    _cooldown(task_id)
    try:
        task_manager.resume_task(task_id, "")
    except LookupError:
        raise HTTPException(status_code=404, detail="任务不存在")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    _audit(actor, "retry", task_id, f"reason={body.reason or '-'}")
    return {"task_id": task_id, "status": "PENDING", "message": "已重新入队（checkpoint 续跑）"}


# ═══════════════════════════════════════════════════
# POST /admin/tasks/{task_id}/revoke — 撤销（取消标志 + 队列内 revoke）
# ═══════════════════════════════════════════════════

@router.post("/{task_id}/revoke")
async def admin_revoke_task(task_id: str, body: AdminOpRequest, request: Request):
    await require_admin_operator(request)
    actor = request.state.actor if hasattr(request.state, "actor") else "admin"
    if not body.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true（二次确认）")
    record = task_service.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status.is_terminal():
        raise HTTPException(status_code=409,
                            detail=f"任务已终态（{record.status.value}），无需撤销")
    _cooldown(task_id)
    if not task_manager.request_cancel(task_id):
        raise HTTPException(status_code=503, detail="控制通道（Redis）不可用，无法撤销")
    _audit(actor, "revoke", task_id, f"reason={body.reason or '-'}")
    return {"task_id": task_id, "status": record.status.value,
            "message": "撤销请求已下发（节点边界生效 / 队列内直接 revoke）"}
