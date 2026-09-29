"""node_runtime/context.py — ExecutionContext 节点执行上下文

冻结 dataclass：只有 node_name / domain / deadline / tags 四个标识性字段，
禁止携带业务状态——业务 state 由调用方单独传给 NodeRunner.run。
deadline 单位为秒（与 CS run_expert_safely 的 timeout_s 同语义），None = 不限时。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionContext:
    """节点身份与执行约束（只读、可哈希）。"""

    node_name: str
    domain: str = ""
    deadline: float | None = None
    tags: tuple[str, ...] = ()
