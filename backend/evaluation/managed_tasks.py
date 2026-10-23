"""业务评测复用 TaskService、既有 Worker、Lease 与恢复机制。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from backend.evaluation.dataset import imports as assets

case_boundary: ContextVar = ContextVar("managed_evaluation_boundary", default=None)


def task_view(task) -> dict:
    """保留任务权威状态，展示时区分等待自动重试与最终失败。"""
    from backend.tasks.retry_policy import is_waiting_retry

    raw_status = task.status.value
    retrying = is_waiting_retry(task)
    return {
        "task_id": task.id, "run_id": task.id,
        "status": "RETRYING" if retrying else raw_status,
        "raw_status": raw_status,
        "progress": task.progress, "error": task.error_message,
        "output": task.output,
    }


@contextmanager
def managed_case_budget(case, run_id):
    """批量评测以题目为请求预算单元，继续执行用户与租户总额度守卫。"""
    if not case.metadata.get("managed_binding_id"):
        yield
        return
    from backend.core.request_context import get_tool_tenant_id, get_tool_user_id
    from backend.infra.llm.budget import scoped_request_budget

    with scoped_request_budget(
        f"eval:{run_id}:case:{case.id}",
        user_id=get_tool_user_id().removeprefix("user:"),
        tenant_id=get_tool_tenant_id() or "default",
    ):
        yield


def submit(binding_id: str, actor: str, client_key: str) -> dict:
    assets.load_binding(binding_id)
    return submit_payload({"binding_id": binding_id, "operation": "evaluate"}, actor, client_key)


def submit_payload(payload: dict, actor: str, client_key: str) -> dict:
    from backend.services import task_service
    from backend.shared.idempotency import IdempotencyKey, execute_idempotent_in_transaction
    from backend.tasks.task_manager import dispatch_task

    task_service.ensure_schema()
    created = False

    def create(conn):
        nonlocal created
        task = task_service.create_task(
            actor.removeprefix("user:"),
            "业务知识库评测",
            graph_name="rag_eval",
            biz_type="evaluation",
            biz_id=payload.get("binding_id") or payload["snapshot_id"],
            extra_input={**payload, "actor": actor},
            connection=conn,
        )
        created = True
        return {"task_id": task.id, "run_id": task.id}

    def transaction_connection():
        conn = task_service._conn()
        conn.autocommit = False
        return conn

    result = execute_idempotent_in_transaction(
        transaction_connection,
        IdempotencyKey("default", actor, "evaluation.run", client_key),
        payload,
        create,
    )
    task = task_service.get_task(result["task_id"])
    # 重复投递由租约仲裁；投递失败保留 PENDING，由统一 pending recovery 重投。
    if created and task and task.status.value == "PENDING":
        dispatch_task(task)
    return {**result, **(task_view(task) if task else {"status": "PENDING", "output": None})}


def execute(record, *, execution_id: str, heartbeat) -> dict:
    from backend.observability.tracer import trace_collector
    from backend.shared.logger import clear_log_context

    # Worker 子进程复用时，不继承上一个评测用例的 Trace 与日志归属。
    trace_collector.bind(None)
    clear_log_context()
    from backend.evaluation.managed_execution import execute_binding as _run_binding
    from backend.evaluation.managed_execution import validate_kb as require_kb
    from backend.models.task import TaskStatus
    from backend.orchestration.checkpoint import TaskCancelled, TaskPaused
    from backend.security.task_authorization import resolve_task_authorization
    from backend.services import task_service
    from backend.tasks.task_manager import is_cancel_requested, is_pause_requested

    authorization = resolve_task_authorization(record.user_id, record.tenant_id)
    if "super_admin" not in authorization.principal.roles:
        raise PermissionError("评测任务操作者已失去共享资产管理权限")

    def boundary(case_id=""):
        heartbeat.raise_if_lost()
        current = resolve_task_authorization(record.user_id, record.tenant_id)
        if "super_admin" not in current.principal.roles:
            raise PermissionError("评测任务操作者已失去共享资产管理权限")
        if is_cancel_requested(record.id):
            raise TaskCancelled(record.id)
        if is_pause_requested(record.id):
            raise TaskPaused(record.id)
        task_service.update_status(
            record.id,
            TaskStatus.RUNNING,
            execution_id=execution_id,
            progress=f"正在评测 {case_id}" if case_id else "正在准备评测",
        )

    boundary()
    if record.input.get("operation") == "synthesize":
        from backend.evaluation.dataset.synthetic import generate

        result = generate({**record.input, "task_id": record.id}, boundary=boundary)
    else:
        binding, _ = assets.load_binding(record.input["binding_id"])
        require_kb(binding["kb_id"])
        result = _run_binding(
            record.input["binding_id"],
            record.input["actor"],
            run_id=record.id,
            boundary=boundary,
        )
    boundary()
    task_service.update_status(
        record.id,
        TaskStatus.SUCCESS,
        execution_id=execution_id,
        output=result,
        progress="评测完成，请检查有效性和逐题结果",
    )
    return {"status": "SUCCESS", **result}
