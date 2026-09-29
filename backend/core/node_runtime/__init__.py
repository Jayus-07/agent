"""core/node_runtime — 节点级公共执行生命周期（STOP F）

与 core/tool_runtime 平级的横切运行时：只服务「专家/域内节点」的六段
生命周期（log_start → timer → invoke → exception policy → wrap → log_done）。
不是第二套 Tool 治理（retry/breaker/bulkhead 属 tool_runtime）；
业务字段禁入 NodeResult，域 TypedDict 各自保留。
设计审计与冻结边界：docs/architecture/STOP_F_Preparation_Audit.md
"""
from backend.core.node_runtime.context import ExecutionContext
from backend.core.node_runtime.error_policy import ErrorPolicy, TimeoutStrategy
from backend.core.node_runtime.hooks import (
    CsExpertHooks,
    ObservabilityHooks,
    TravelExpertHooks,
)
from backend.core.node_runtime.models import NodeResult, NodeStatus
from backend.core.node_runtime.runner import NodeRunner

__all__ = [
    "CsExpertHooks",
    "ErrorPolicy",
    "ExecutionContext",
    "NodeResult",
    "NodeRunner",
    "NodeStatus",
    "ObservabilityHooks",
    "TimeoutStrategy",
    "TravelExpertHooks",
]
