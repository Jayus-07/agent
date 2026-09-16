"""app/api/routes/tasks.py — 异步 Agent 任务 API。

多用户隔离：所有读写强制 user_id 绑定（resolve_identity 统一身份入口；
tenant_id 取 X-Tenant-Id 头，默认 default）。用户 A 永远查不到/操作不了
用户 B 的任务（task_service 层 WHERE user_id 过滤，本层转 HTTP 语义）。

网关映射：APISIX 剥 /api 前缀 → 对外 /api/tasks/*，后端 /tasks/*。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.api.identity import resolve_identity
from backend.config.tasks import TASKS_LIST_DEFAULT_LIMIT
from backend.models.task import TaskStatus
from backend.shared.logger import logger
from backend.services import task_service
from backend.tasks import task_manager

router = APIRouter(prefix="/tasks", tags=["异步任务"])

_SSE_HEARTBEAT_INTERVAL = 15.0  # 秒：空闲心跳，防网关/代理断流


# ── 请求/响应模型 ────────────────────────────────────────

class TaskCreateRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Agent 任务指令")
    user_id: str = Field("", description="legacy 身份模式下的用户标识（header 模式忽略）")
    tenant_id: str = Field("", description="租户标识（缺省取 X-Tenant-Id 头 / default）")


class TaskResumeRequest(BaseModel):
    user_input: str = Field("", description="WAITING_USER 场景下注入的用户确认/补充输入")


def _identity(request: Request, body_user_id: str | None = None):
    """统一身份解析；未认证直接 401（任务必须绑定用户）。"""
    ident = resolve_identity(request, body_user_id)
    if not ident.user_id:
        raise HTTPException(status_code=401,
                            detail="未认证：任务接口要求用户身份（网关身份头或 body.user_id）")
    return ident


def _tenant(request: Request, explicit: str = "") -> str:
    return explicit or request.headers.get("X-Tenant-Id", "") or "default"


def _get_owned_task(task_id: str, user_id: str, tenant_id: str):
    record = task_service.get_task_for_user(task_id, user_id, tenant_id=tenant_id)
    if record is None:
        # 不区分 404/403：避免任务 id 枚举探测（隔离即不暴露存在性）
        raise HTTPException(status_code=404, detail="任务不存在")
    return record


# ═══════════════════════════════════════════════════
# POST /tasks — 创建 Agent 任务（立即返回 task_id）
# ═══════════════════════════════════════════════════

@router.post("")
async def create_task(body: TaskCreateRequest, request: Request):
    ident = _identity(request, body.user_id or None)
    record = task_service.create_task(
        ident.user_id, body.query, tenant_id=_tenant(request, body.tenant_id))
    try:
        task_manager.enqueue_task(record)
    except Exception as e:
        logger.error("[TasksAPI] enqueue failed: %s (%s)", record.id, e)
        task_service.update_status(record.id, __import__(
            "backend.models.task", fromlist=["TaskStatus"]).TaskStatus.FAILED,
            error_message="任务队列不可用（broker 连接失败）")
        raise HTTPException(status_code=503, detail="任务队列暂不可用，请稍后重试")
    return {"task_id": record.id, "status": "PENDING"}


# ═══════════════════════════════════════════════════
# GET /tasks/{task_id} — 查询任务状态
# ═══════════════════════════════════════════════════

@router.get("/{task_id}")
async def get_task_status(task_id: str, request: Request,
                          user_id: str = Query("", description="legacy 身份")):
    ident = _identity(request, user_id or None)
    record = _get_owned_task(task_id, ident.user_id, _tenant(request))
    return record.to_public_dict()


# ═══════════════════════════════════════════════════
# GET /tasks — 用户任务历史
# ═══════════════════════════════════════════════════

@router.get("")
async def list_tasks(request: Request,
                     user_id: str = Query("", description="legacy 身份"),
                     status: str = Query("", description="按状态过滤"),
                     limit: int = Query(TASKS_LIST_DEFAULT_LIMIT, ge=1, le=100)):
    ident = _identity(request, user_id or None)
    records = task_service.list_tasks_for_user(
        ident.user_id, tenant_id=_tenant(request), status=status, limit=limit)
    return {"tasks": [r.to_public_dict() for r in records], "count": len(records)}


# ═══════════════════════════════════════════════════
# POST /tasks/{task_id}/cancel — 取消任务
# ═══════════════════════════════════════════════════

@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    ident = _identity(request)
    record = _get_owned_task(task_id, ident.user_id, _tenant(request))
    if record.status.is_terminal():
        return {"task_id": task_id, "status": record.status.value,
                "message": "任务已结束，无需取消"}
    if not task_manager.request_cancel(task_id):
        raise HTTPException(status_code=503, detail="控制通道（Redis）不可用，无法取消")
    return {"task_id": task_id, "status": record.status.value,
            "message": "取消请求已下发（节点边界生效）"}


# ═══════════════════════════════════════════════════
# POST /tasks/{task_id}/pause — 暂停任务（节点边界生效）
# ═══════════════════════════════════════════════════

@router.post("/{task_id}/pause")
async def pause_task(task_id: str, request: Request):
    ident = _identity(request)
    record = _get_owned_task(task_id, ident.user_id, _tenant(request))
    if record.status != "RUNNING" and record.status != TaskStatusRef.PENDING:
        raise HTTPException(status_code=409,
                            detail=f"仅 RUNNING/PENDING 任务可暂停（当前 {record.status.value}）")
    if not task_manager.request_pause(task_id):
        raise HTTPException(status_code=503, detail="控制通道（Redis）不可用，无法暂停")
    return {"task_id": task_id, "status": record.status.value,
            "message": "暂停请求已下发（下一节点边界生效）"}


# ═══════════════════════════════════════════════════
# POST /tasks/{task_id}/resume — 恢复暂停/等待用户输入的任务
# ═══════════════════════════════════════════════════

@router.post("/{task_id}/resume")
async def resume_task(task_id: str, body: TaskResumeRequest, request: Request):
    ident = _identity(request)
    _get_owned_task(task_id, ident.user_id, _tenant(request))
    try:
        record = task_manager.resume_task(task_id, body.user_input or "")
    except LookupError:
        raise HTTPException(status_code=404, detail="任务不存在")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"task_id": task_id, "status": record.status.value,
            "message": "已重新入队（从 checkpoint 恢复）"}


# ═══════════════════════════════════════════════════
# GET /tasks/{task_id}/stream — SSE 实时状态推送
# ═══════════════════════════════════════════════════

_SSE_TERMINAL_EVENTS = {"completed", "failed", "cancelled"}


@router.get("/{task_id}/stream")
async def stream_task_events(task_id: str, request: Request,
                             user_id: str = Query("", description="legacy 身份")):
    ident = _identity(request, user_id or None)
    record = _get_owned_task(task_id, ident.user_id, _tenant(request))

    async def event_stream():
        from starlette.concurrency import run_in_threadpool

        # 初始快照：订阅前已发生的事件以 status 快照补齐
        yield _sse_frame("snapshot", record.to_public_dict())
        if record.status.is_terminal():
            yield _sse_frame("completed" if record.status == TaskStatus.SUCCESS
                             else record.status.value.lower(), {})
            return

        pubsub = task_manager.subscribe_events(task_id)
        if pubsub is None:
            yield _sse_frame("error", {"message": "事件通道（Redis）不可用"})
            return

        try:
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    break
                message = await run_in_threadpool(pubsub.get_message, 1.0)
                if message is None:
                    idle += 1.0
                    if idle >= _SSE_HEARTBEAT_INTERVAL:
                        idle = 0.0
                        yield _sse_frame("ping", {"ts": ""})
                    continue
                idle = 0.0
                payload = json.loads(message["data"])
                yield f"event: {payload.get('event', 'message')}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if payload.get("event") in _SSE_TERMINAL_EVENTS:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — SSE 链路异常不能 500，降级为 error 帧
            logger.debug("[TasksAPI] stream aborted: %s", e, exc_info=True)
            yield _sse_frame("error", {"message": str(e)})
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
