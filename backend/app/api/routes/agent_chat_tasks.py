"""企业助手长任务控制面，复用既有任务服务与执行器。"""
import hashlib
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.identity import require_product_identity
from backend.app.api.routes import tasks
from backend.core.product_context import AGENT_PRODUCT, namespace_session_id
from backend.services import task_service
from backend.tasks import task_manager

router = APIRouter(prefix="/chat/agent/tasks", tags=["企业助手长任务"])
AGENT_LONG_TASK = "agent_chat_long"


def _session(value: str) -> str:
    if not value or len(value) > 200:
        raise HTTPException(status_code=422, detail="会话标识无效")
    return namespace_session_id(AGENT_PRODUCT, value)


@contextmanager
def _conversation_guard(ident, session_id: str):
    """串行创建/替换同会话任务，防止两个标签页互相取消新任务。"""
    redis = task_manager._redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="任务控制通道暂不可用")
    scope = f"{ident.tenant_id}\0{ident.user_id}\0{session_id}"
    key = hashlib.sha256(scope.encode()).hexdigest()
    lock = redis.lock(f"agent:chat-task:{key}", timeout=30, blocking_timeout=2)
    if not lock.acquire():
        raise HTTPException(status_code=409, detail="当前会话正在提交任务，请稍后再试")
    try:
        yield
    finally:
        lock.release()


def _cancel_previous(ident, session_id: str, *, exclude: str = "") -> None:
    """新输入结束同会话旧任务；重复创建命中幂等时不取消自身。"""
    records = task_service.list_tasks_for_user(
        ident.user_id, tenant_id=ident.tenant_id,
        biz_type=AGENT_LONG_TASK, conversation_id=_session(session_id), limit=100,
    )
    for record in records:
        if record.id == exclude or record.status.is_terminal():
            continue
        result = task_manager.cancel_task(record.id)
        if not result.get("ok"):
            raise HTTPException(status_code=503, detail="原任务控制通道不可用，请稍后再试")


def cancel_previous(ident, session_id: str) -> None:
    with _conversation_guard(ident, session_id):
        _cancel_previous(ident, session_id)


def submit_long_task(req, request: Request, ident, *, state=None, node=None) -> dict:
    with _conversation_guard(ident, req.session_id):
        return _submit_long_task(req, request, ident, state=state, node=node)


def _submit_long_task(req, request: Request, ident, *, state=None, node=None) -> dict:
    from backend.app.api.routes.chat import _product_idempotency_key

    def prepare(task_id):
        _cancel_previous(ident, req.session_id, exclude=task_id)
        if state is not None:
            from backend.orchestration.checkpoint.chat_handoff import seed_task

            seed_task(task_id, state, node)

    result = tasks.submit_task(
        req.question, request, ident, ident.tenant_id,
        extra_input={"kb_id": req.kb_id or "*", "model": req.model or "",
                     "product_id": "agent", "chat_session_id": req.session_id},
        conversation_id=_session(req.session_id), biz_type=AGENT_LONG_TASK,
        client_key=_product_idempotency_key(
            AGENT_PRODUCT, request.headers.get("Idempotency-Key"),
            fallback=req.request_id if req.request_id != "default" else "",
        ),
        before_enqueue=prepare,
    )
    return {**result, "session_id": req.session_id}


def _owned(task_id: str, request: Request):
    ident = require_product_identity(request)
    record = tasks._get_owned_task(task_id, ident.user_id, ident.tenant_id)
    if record.biz_type != AGENT_LONG_TASK or (record.input or {}).get("product_id") != "agent":
        raise HTTPException(status_code=404, detail="任务不存在")
    return record


def public_task(record, *, detail: bool = False) -> dict:
    result = record.to_public_dict()
    result.update({"question": (record.input or {}).get("query", ""),
                   "session_id": (record.input or {}).get("chat_session_id", "")})
    if detail:
        from backend.orchestration.graph.builder import _NODE_LABELS

        result["node_labels"] = _NODE_LABELS
        result["steps"] = [
            {"id": row["id"], "node": row["node_name"], "at": str(row["created_at"])}
            for row in task_service.list_checkpoints(record.id, record.user_id)
            if not row["node_name"].startswith("__")
        ]
    return result


@router.get("")
def list_agent_tasks(request: Request, session_id: str = Query("")):
    ident = require_product_identity(request)
    rows = task_service.list_tasks_for_user(
        ident.user_id, tenant_id=ident.tenant_id, biz_type=AGENT_LONG_TASK,
        conversation_id=_session(session_id) if session_id else "", limit=100,
    )
    return {"tasks": [public_task(row) for row in rows]}


@router.get("/{task_id}")
def get_agent_task(task_id: str, request: Request):
    return public_task(_owned(task_id, request), detail=True)


@router.post("/{task_id}/{action}")
async def control_agent_task(task_id: str, action: str, request: Request):
    _owned(task_id, request)
    if action == "pause":
        return await tasks.pause_task(task_id, request)
    if action == "resume":
        return await tasks.resume_task(task_id, tasks.TaskResumeRequest(), request)
    if action == "cancel":
        return await tasks.cancel_task(task_id, request)
    raise HTTPException(status_code=404, detail="操作不存在")
