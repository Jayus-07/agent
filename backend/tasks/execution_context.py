"""tasks/execution_context.py — 当前消息绑定的执行租约上下文（Phase2 Step1）。

用途：Celery signals（postrun/failure/retry）没有 execution_id 入参，无法
自行做 fencing。impl 在租约认领后通过本模块登记 (task_id, execution_id)，
signals 收尾前校验 DB 行的 execution_id 是否仍是自己——租约被接管的旧
Worker 的收尾写（盲 SUCCESS / 终审 FAILED）被拒绝，不污染新 owner 的状态。

prefork 子进程一次执行一个任务，ContextVar 单值即可；eager 测试嵌套时
以 token set/reset 保证正确性。
"""
from __future__ import annotations

import contextvars

# (task_id, execution_id)；未认领租约的消息为 None
_current: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "task_execution_lease", default=None)


def set_execution(task_id: str, execution_id: str):
    """登记当前消息的执行租约（返回 token 供 finally 恢复）。"""
    return _current.set((str(task_id), str(execution_id)))


def clear_execution(token) -> None:
    """恢复登记前的上下文（finally 调用；token 为 None 时无害跳过）。"""
    if token is not None:
        _current.reset(token)


def get_execution() -> tuple[str, str] | None:
    """读取当前消息的 (task_id, execution_id)；未认领租约返回 None。"""
    return _current.get()
