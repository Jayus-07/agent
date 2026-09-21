"""core/tool_runtime — 企业级 Tool 失败治理与降级机制

对外入口：
  - safe_tool_executor.run(...)  统一 Tool 执行（Deadline/Timeout/Retry/熔断/隔离/降级/指标）
  - RequestDeadline              请求级预算
  - get_policy / ToolPolicy      每 Tool 策略
  - ToolResult / ToolStatus / ToolCriticality / OperationType
  - circuit_registry / bulkhead_registry  观测与运维钩子

设计约定：Domain / LangGraph 只判断 ToolResult.status，不感知底层异常。
"""
from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import circuit_registry
from backend.core.tool_runtime.deadline import BudgetExhausted, RequestDeadline
from backend.core.tool_runtime.executor import SafeToolExecutor, safe_tool_executor
from backend.core.tool_runtime.models import (
    OperationType,
    ToolCriticality,
    ToolResult,
    ToolStatus,
)
from backend.core.tool_runtime.policy import ToolPolicy, get_policy

__all__ = [
    "BudgetExhausted",
    "OperationType",
    "RequestDeadline",
    "SafeToolExecutor",
    "ToolCriticality",
    "ToolPolicy",
    "ToolResult",
    "ToolStatus",
    "bulkhead_registry",
    "circuit_registry",
    "get_policy",
    "safe_tool_executor",
]
