"""Tool Governance Runtime。

该包只负责确定性治理：候选约束、参数/结果契约、风险审批、请求预算和
运行时保护。LLM 的输出只能作为 ``ToolCallRequest`` 的输入，不能直接
绕过这里进入 Provider。
"""

from backend.core.tool_governance.guard import (
    GovernanceDecision,
    GovernanceRuntime,
    OperationPreview,
    ToolCallRequest,
    governance_runtime,
)
from backend.core.tool_governance.models import (
    ApprovalPolicy,
    Operation,
    RiskLevel,
    ToolLifecycle,
    ToolSpec,
)

__all__ = [
    "ApprovalPolicy",
    "GovernanceDecision",
    "GovernanceRuntime",
    "Operation",
    "OperationPreview",
    "RiskLevel",
    "ToolCallRequest",
    "ToolLifecycle",
    "ToolSpec",
    "governance_runtime",
]
