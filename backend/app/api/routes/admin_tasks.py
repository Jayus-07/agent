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


def _audit(actor: str, action: str, task_id: str, result: str, *,
           reason: str = "", before_status: str = "", after_status: str = "",
           new_task_id: str = "") -> None:
    """审计双写：结构化日志（原有）+ ai.task_operation_audits（M10 补，软失败）。"""
    logger.warning("[AdminTaskAudit] actor=%s action=%s task=%s result=%s",
                   actor, action, task_id, result)
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            conn.cursor().execute(
                """
                INSERT INTO ai.task_operation_audits (
                    task_id, new_task_id, operation, actor, reason,
                    before_status, after_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (task_id, new_task_id or None, action, actor, reason[:500],
                 before_status, after_status),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001 — 审计软失败不阻断操作
        logger.warning(f"[AdminTaskAudit] DB 审计写入失败（日志已在）: {e}")


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
# GET /admin/tasks/queues — 队列 backlog（M10；须在 /{task_id} 前注册）
# ═══════════════════════════════════════════════════

@router.get("/queues")
async def admin_task_queues(request: Request):
    """五队列等待深度 + worker 存活（Redis LLEN 直读，本机自包含）。"""
    await require_admin_operator(request)
    from backend.config.tasks import (
        CELERY_AGENT_QUEUE,
        CELERY_MAINTENANCE_QUEUE,
        CELERY_METADATA_SHADOW_QUEUE,
        CELERY_RAG_INDEX_QUEUE,
        CELERY_REPORT_QUEUE,
    )

    queues = {
        "agent": CELERY_AGENT_QUEUE,
        "rag_index": CELERY_RAG_INDEX_QUEUE,
        "metadata_shadow": CELERY_METADATA_SHADOW_QUEUE,
        "report": CELERY_REPORT_QUEUE,
        "maintenance": CELERY_MAINTENANCE_QUEUE,
    }
    depths: dict[str, int | None] = {}
    try:
        from backend.infra.redis.client import get_redis

        r = get_redis()
        for logical, physical in queues.items():
            try:
                depths[logical] = int(r.llen(physical))
            except Exception:
                depths[logical] = None
    except Exception as e:  # noqa: BLE001 — Redis 不可达时仍返回骨架
        logger.warning(f"[AdminTasksAPI] 队列深度读取失败: {e}")
        depths = {logical: None for logical in queues}
    workers: list[str] = []
    try:
        workers = list(task_manager.celery_app.control.ping(timeout=2.0) or []) \
            if hasattr(task_manager, "celery_app") else []
    except Exception:
        workers = []
    return {
        "queues": [{"logical": logical, "physical": physical,
                    "waiting": depths.get(logical)} for logical, physical in queues.items()],
        "workers_online": len(workers),
    }


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
        # Step3：admin retry 与 resume 共用同一派发链（QueueRouter 决定
        # 队列），仅 dispatch_type 区分观测口径；Admin API 不指定 queue
        task_manager.resume_task(task_id, "", allow_failed=True,
                                 dispatch_type="admin_retry")
    except LookupError:
        raise HTTPException(status_code=404, detail="任务不存在")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    _audit(actor, "retry", task_id, f"reason={body.reason or '-'}",
           reason=body.reason, before_status=record.status.value,
           after_status="PENDING")
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
    # B5（2026-09-21 高并发审查）：RUNNING 纳入强制取消范围——活 Worker 走
    # 取消标志（节点边界生效）；心跳已停更超阈值的僵尸 RUNNING 直接收尸为
    # CANCELLED，不等一个永远不会回来的 Worker。
    result = task_manager.force_cancel_task(task_id)
    if not result.get("flag"):
        raise HTTPException(status_code=503, detail="控制通道（Redis）不可用，无法撤销")
    message = ("僵尸任务已强制收尸（RUNNING 心跳超时，直接置 CANCELLED）"
               if result.get("forced")
               else "撤销请求已下发（节点边界生效 / 队列内直接 revoke）")
    _audit(actor, "revoke", task_id,
           f"reason={body.reason or '-'} forced={result.get('forced')}",
           reason=body.reason, before_status=record.status.value,
           after_status=result.get("status", ""))
    return {"task_id": task_id, "status": result.get("status", ""),
            "forced": result.get("forced", False), "message": message}


# ═══════════════════════════════════════════════════
# POST /admin/tasks/{task_id}/reexecute — 克隆重执行（M10）
# ═══════════════════════════════════════════════════

@router.post("/{task_id}/reexecute")
async def admin_reexecute_task(task_id: str, body: AdminOpRequest, request: Request):
    """从头重跑：以原任务 input 克隆新任务（parent_task_id 关联源任务）。

    与 retry（checkpoint 续跑）的区别：SUCCESS/CANCELLED 终态封闭是冻结
    语义（models/task.py 状态机白名单），本端点**不解锁状态机**——克隆是
    新任务新 thread_id，从头执行。任何状态都可克隆。
    """
    await require_admin_operator(request)
    actor = request.state.actor if hasattr(request.state, "actor") else "admin"
    if not body.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true（二次确认）")
    record = task_service.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _cooldown(task_id)
    source_input = record.input if isinstance(record.input, dict) else {}
    query = str(source_input.get("query", ""))
    extra = {k: v for k, v in source_input.items() if k != "query"}
    from backend.tasks.queue_router import QueueRoutingError

    try:
        clone = task_service.create_task(
            record.user_id, query,
            tenant_id=record.tenant_id,
            graph_name=record.graph_name,
            conversation_id=record.conversation_id or "",
            biz_type=record.biz_type or "",
            biz_id=record.biz_id or "",
            parent_task_id=record.id,
            extra_input=extra or None,
        )
        task_manager.enqueue_task(clone)
    except QueueRoutingError:
        raise HTTPException(status_code=400, detail="源任务 workflow 未登记队列路由，无法克隆")
    except HTTPException:
        raise
    except Exception:
        logger.error("[AdminTasksAPI] reexecute 克隆失败: %s", task_id, exc_info=True)
        raise HTTPException(status_code=503, detail="任务队列暂不可用，克隆失败")
    _audit(actor, "reexecute", task_id,
           f"reason={body.reason or '-'} new_task={clone.id}",
           reason=body.reason, before_status=record.status.value,
           after_status="PENDING", new_task_id=clone.id)
    return {"task_id": clone.id, "source_task_id": task_id,
            "status": "PENDING", "message": "已克隆新任务从头执行"}


# ═══════════════════════════════════════════════════
# GET /admin/tasks/{task_id}/operations — 操作审计历史（M10）
# ═══════════════════════════════════════════════════

@router.get("/{task_id}/operations")
async def admin_task_operations(task_id: str, request: Request):
    await require_admin_operator(request)
    if task_service.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT operation, actor, reason, before_status, after_status, "
                "new_task_id, created_at FROM ai.task_operation_audits "
                "WHERE task_id = %s ORDER BY id DESC LIMIT 100", (task_id,))
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — 审计查询软失败
        logger.warning(f"[AdminTasksAPI] 操作审计查询失败: {e}")
        rows = []
    return {"operations": [
        {"operation": r[0], "actor": r[1], "reason": r[2],
         "before_status": r[3], "after_status": r[4],
         "new_task_id": r[5] or "", "created_at": str(r[6])}
        for r in rows
    ]}



