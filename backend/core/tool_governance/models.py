"""Tool Governance 的声明模型与生命周期契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Operation(str, Enum):
    """Tool 的确定性操作分类。"""

    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    BATCH_CREATE = "batch_create"
    BATCH_UPDATE = "batch_update"
    BATCH_DELETE = "batch_delete"
    CLEAR = "clear"
    RESET = "reset"
    SEND = "send"
    PUBLISH = "publish"
    EXPORT = "export"
    EXTERNAL_WRITE = "external_write"


class RiskLevel(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ApprovalPolicy(str, Enum):
    NONE = "none"
    USER_CONFIRMATION = "user_confirmation"
    ADMIN_APPROVAL = "admin_approval"
    DUAL = "dual"


class ToolLifecycle(str, Enum):
    CANDIDATED = "CANDIDATED"
    SELECTED = "SELECTED"
    VALIDATING = "VALIDATING"
    BLOCKED = "BLOCKED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    READY = "READY"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    EMPTY = "EMPTY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class ToolSpec:
    """capabilities.yaml 派生的完整运行时 Tool 契约。"""

    capability: str
    version: int
    domains: tuple[str, ...]
    description: str
    params_schema: dict[str, Any]
    output_schema: dict[str, Any]
    operation: Operation
    risk_level: RiskLevel
    confirmation_policy: ApprovalPolicy
    operation_by: dict[str, Operation] = field(default_factory=dict)
    risk_by: dict[str, RiskLevel] = field(default_factory=dict)
    confirmation_by: dict[str, ApprovalPolicy] = field(default_factory=dict)
    required_scopes: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    timeout_ms: int = 15_000
    max_calls_per_request: int = 3
    retry_max_attempts: int = 0
    dedupe_enabled: bool = True
    dedupe_ttl_seconds: int = 30
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    rate_limit_scope: str = "tenant"
    circuit_breaker_enabled: bool = True
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: float = 30.0
    idempotency_required: bool = False
    audit_policy: dict[str, str] = field(default_factory=dict)
    auto_params: tuple[str, ...] = ()

    def effective_operation(self, params: dict[str, Any]) -> Operation:
        """按声明的参数映射解析动态操作，不依赖 Tool 名称猜风险。"""
        field_name = "action"
        value = params.get(field_name)
        if value is not None and str(value) in self.operation_by:
            return self.operation_by[str(value)]
        return self.operation

    def effective_risk(self, params: dict[str, Any]) -> RiskLevel:
        action = str(params.get("action", ""))
        operation = self.effective_operation(params).value
        return self.risk_by.get(action, self.risk_by.get(operation, self.risk_level))

    def effective_confirmation_policy(self, params: dict[str, Any]) -> ApprovalPolicy:
        action = str(params.get("action", ""))
        operation = self.effective_operation(params).value
        return self.confirmation_by.get(
            action,
            self.confirmation_by.get(operation, self.confirmation_policy),
        )

    @property
    def requires_confirmation(self) -> bool:
        return self.risk_level in (RiskLevel.R2, RiskLevel.R3) or (
            self.confirmation_policy
            in (ApprovalPolicy.USER_CONFIRMATION, ApprovalPolicy.DUAL)
        )

    def requires_confirmation_for(self, params: dict[str, Any]) -> bool:
        return self.effective_risk(params) in (RiskLevel.R2, RiskLevel.R3) or (
            self.effective_confirmation_policy(params)
            in (ApprovalPolicy.USER_CONFIRMATION, ApprovalPolicy.DUAL)
        )

    @property
    def requires_admin_approval(self) -> bool:
        return self.confirmation_policy in (
            ApprovalPolicy.ADMIN_APPROVAL,
            ApprovalPolicy.DUAL,
        )

    def requires_admin_approval_for(self, params: dict[str, Any]) -> bool:
        return self.effective_confirmation_policy(params) in (
            ApprovalPolicy.ADMIN_APPROVAL,
            ApprovalPolicy.DUAL,
        )


@dataclass(frozen=True)
class ToolCallRequest:
    """LLM/FC 选择结果与运行时身份的边界对象。"""

    capability: str
    arguments: dict[str, Any]
    candidate_capabilities: tuple[str, ...] = ()
    domain: str = ""
    intent_fit: str = "match"
    selection_reason: str = ""
    runtime_injected: dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    tenant_id: str = ""
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    confirmation_id: str = ""
    idempotency_key: str = ""
    impact: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationPreview:
    """高风险 Tool 的 Prepare 阶段产物。"""

    confirmation_id: str
    capability: str
    operation: str
    reason: str
    target: dict[str, Any]
    impact: dict[str, Any]
    preview: dict[str, Any]
    args_hash: str
    expires_at: float
    tool_spec_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "capability": self.capability,
            "operation": self.operation,
            "reason": self.reason,
            "target": self.target,
            "impact": self.impact,
            "preview": self.preview,
            "args_hash": self.args_hash,
            "expires_at": self.expires_at,
            "tool_spec_version": self.tool_spec_version,
        }
