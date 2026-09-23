"""tasks/side_effect.py — 任务运行时副作用幂等边界（Phase2 Step6）。

把 shared.idempotency 的 PG durable ledger 与任务运行时的身份/租约语义对齐：

  - logical key 稳定：tenant + actor + operation + client_key（业务身份），
    Celery retry / Step1 recovery / resume / admin retry 全程不变——
    task_id 是任务身份可进 key 组合，execution_id 每次拾取换发
    只能当 owner（Step6 G2/§十七）；
  - fencing 与幂等职责分离：租约校验只回答「谁有权执行」，ledger 回答
    「副作用是否已发生」；执行副作用前先校验租约，失权 execution
    快速失败（Step6 §十八）；
  - stale RUNNING claim 的接管必须确认 owner execution 已死（tasks 行的
    execution_id 已换发或任务已离开 RUNNING），绝不按时间盲目重执行
    （Step6 §二十）。
"""
from __future__ import annotations

from typing import Any

from backend.shared.idempotency import run_idempotent_side_effect

__all__ = ["execution_is_dead", "run_task_side_effect"]


def execution_is_dead(task_id: str, owner_execution_id: str) -> bool:
    """判断 owner execution 是否已确认死亡（stale claim 接管判定依据）。

    tasks 行的 execution_id 不再是 owner（被新拾取换发 / recovery 认领时
    清空），或任务已离开 RUNNING——两种情况下旧执行者都不可能还在跑，
    其遗留的 stale claim 才允许被接管。
    """
    from backend.models.task import TaskStatus
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        return True
    if record.status != TaskStatus.RUNNING:
        return True
    return (record.execution_id or "") != owner_execution_id


def run_task_side_effect(
    *,
    task_id: str,
    operation: str,
    client_key: str,
    tenant_id: str,
    actor_id: str,
    payload: Any,
    fn,
    execution_id: str = "",
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """在任务执行上下文里执行一次持久化幂等副作用（PG ledger 权威）。

    client_key 由调用方用稳定业务身份组合（如 f"{task_id}:{动作名}"）；
    execution_id 缺省从 execution_context 取当前租约。副作用执行前校验
    租约（fencing），失权抛 TaskLeaseLost；crash 窗口的 stale claim 只有
    在 owner execution 确认死亡后才允许接管重试。
    """
    from backend.models.task import TaskLeaseLost
    from backend.tasks.execution_context import get_execution

    if not execution_id:
        bound = get_execution()
        execution_id = bound[1] if bound else ""

    def _fencing() -> None:
        if not execution_id:
            return
        from backend.services import task_service

        if not task_service.check_lease_active(task_id, execution_id):
            raise TaskLeaseLost(task_id, execution_id)

    def _takeover_allowed(info: dict) -> bool:
        return execution_is_dead(task_id, str(info.get("owner_execution_id", "")))

    return run_idempotent_side_effect(
        operation,
        payload,
        fn,
        tenant_id=tenant_id,
        actor_id=actor_id,
        client_key=client_key,
        owner_execution_id=execution_id,
        lease_seconds=lease_seconds,
        takeover_allowed=_takeover_allowed,
        pre_execute=_fencing if execution_id else None,
    )
