"""确定性 Selection/Schema/Risk Guard 与 SafeToolExecutor 适配器。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Any, Awaitable, Callable

from backend.core.tool_governance.budget import RequestToolBudget, get_tool_budget
from backend.core.tool_governance.fingerprint import operation_fingerprint
from backend.core.tool_governance.models import (
    ApprovalPolicy,
    OperationPreview,
    RiskLevel,
    ToolCallRequest,
    ToolLifecycle,
)
from backend.core.tool_governance.rate_limiter import RateLimitConfig, rate_limiter
from backend.core.tool_governance.registry import get_tool_spec
from backend.core.tool_governance.result_validator import validate_tool_result
from backend.core.tool_governance.schema_validator import (
    SchemaValidationError,
    legacy_params_to_schema,
    validate_json_schema,
)
from backend.core.tool_runtime.models import OperationType, ToolResult, ToolStatus
from backend.core.tool_runtime.policy import ToolPolicy


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    code: str
    reason: str
    lifecycle: ToolLifecycle
    capability: str
    arguments: dict[str, Any]
    fingerprint: str = ""
    preview: OperationPreview | None = None


class _ConfirmationStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[OperationPreview, bool]] = {}
        self._lock = Lock()

    def put(self, preview: OperationPreview) -> None:
        with self._lock:
            self._items[preview.confirmation_id] = (preview, False)

    def confirm(self, confirmation_id: str) -> OperationPreview | None:
        with self._lock:
            item = self._items.get(confirmation_id)
            if item is None or item[0].expires_at <= time.time():
                return None
            self._items[confirmation_id] = (item[0], True)
            return item[0]

    def consume(self, confirmation_id: str, fingerprint: str) -> bool:
        with self._lock:
            item = self._items.get(confirmation_id)
            if item is None or item[0].expires_at <= time.time() or not item[1]:
                return False
            if item[0].args_hash != fingerprint:
                return False
            del self._items[confirmation_id]
            return True

    def is_confirmed(self, confirmation_id: str, fingerprint: str) -> bool:
        with self._lock:
            item = self._items.get(confirmation_id)
            return bool(
                item
                and item[1]
                and item[0].expires_at > time.time()
                and item[0].args_hash == fingerprint
            )

    def reset(self) -> None:
        with self._lock:
            self._items.clear()


class GovernanceRuntime:
    """所有带 ToolSpec 的执行都应通过此适配器进入 SafeToolExecutor。"""

    def __init__(self) -> None:
        self._confirmations = _ConfirmationStore()
        self._dedupe: dict[str, tuple[float, ToolResult]] = {}
        self._dedupe_lock = Lock()

    def _request_context(self, request: ToolCallRequest) -> ToolCallRequest:
        if request.user_id and request.tenant_id:
            return request
        from backend.core.request_context import get_tool_tenant_id, get_tool_user_id

        return ToolCallRequest(
            **{
                **request.__dict__,
                "user_id": request.user_id or get_tool_user_id(),
                "tenant_id": request.tenant_id or get_tool_tenant_id(),
            }
        )

    def evaluate(self, request: ToolCallRequest, *, require_confirmation: bool = False) -> GovernanceDecision:
        request = self._request_context(request)
        spec = get_tool_spec(request.capability)
        if spec is None:
            return GovernanceDecision(False, "tool_not_registered", "ToolSpec 不存在", ToolLifecycle.BLOCKED, request.capability, {})
        if request.candidate_capabilities and request.capability not in request.candidate_capabilities:
            return GovernanceDecision(False, "tool_not_candidate", "所选 Tool 不在候选集合", ToolLifecycle.BLOCKED, request.capability, {})
        if request.domain and spec.domains and request.domain not in spec.domains:
            return GovernanceDecision(False, "tool_domain_mismatch", "Tool 与当前域冲突", ToolLifecycle.BLOCKED, request.capability, {})
        if request.intent_fit not in {"match", "no_match"}:
            return GovernanceDecision(False, "invalid_intent_fit", "fit 只能为 match 或 no_match", ToolLifecycle.BLOCKED, request.capability, {})
        if request.intent_fit == "no_match":
            return GovernanceDecision(False, "tool_intent_mismatch", request.selection_reason or "Tool 与真实意图不匹配", ToolLifecycle.CLARIFICATION_REQUIRED, request.capability, {})

        llm_arguments = dict(request.arguments)
        forbidden_auto = sorted(set(llm_arguments).intersection(spec.auto_params))
        if forbidden_auto:
            return GovernanceDecision(False, "runtime_argument_supplied_by_llm", f"系统参数不可由模型提供: {forbidden_auto}", ToolLifecycle.BLOCKED, request.capability, {})
        try:
            validate_json_schema(llm_arguments, legacy_params_to_schema({
                key: value for key, value in spec.params_schema["properties"].items()
                if key not in spec.auto_params
            }))
            arguments = {**llm_arguments, **request.runtime_injected}
            validate_json_schema(arguments, spec.params_schema)
        except SchemaValidationError as exc:
            return GovernanceDecision(False, "invalid_param", str(exc), ToolLifecycle.BLOCKED, request.capability, {})

        operation = spec.effective_operation(arguments)
        effective_risk = spec.effective_risk(arguments)
        fingerprint = operation_fingerprint(
            request.capability,
            operation.value,
            arguments,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            impact=request.impact,
        )
        if spec.required_roles and not set(spec.required_roles).intersection(request.roles):
            return GovernanceDecision(False, "permission_denied", "当前角色无权执行该 Tool", ToolLifecycle.BLOCKED, request.capability, arguments, fingerprint)
        if spec.required_scopes and not set(spec.required_scopes).issubset(request.scopes):
            return GovernanceDecision(False, "permission_denied", "当前权限范围不足", ToolLifecycle.BLOCKED, request.capability, arguments, fingerprint)

        # 仅本地测试/调试的既有 auto 模式允许沿用旧工具内部审批门；生产
        # required 模式仍由本 Guard 强制 Prepare → Confirm。
        approval_auto = False
        try:
            from backend.security import tool_approval

            approval_auto = tool_approval.TOOL_APPROVAL_MODE == "auto"
        except (ImportError, AttributeError):
            approval_auto = False
        needs_confirmation = (
            require_confirmation or spec.requires_confirmation_for(arguments)
        ) and not approval_auto
        if needs_confirmation and not request.confirmation_id:
            preview = self._make_preview(request, spec, operation.value, arguments, fingerprint)
            self._confirmations.put(preview)
            return GovernanceDecision(False, "confirmation_required", "高风险操作必须先确认", ToolLifecycle.PENDING_CONFIRMATION, request.capability, arguments, fingerprint, preview)
        if needs_confirmation and not self._confirmations.is_confirmed(request.confirmation_id, fingerprint):
            return GovernanceDecision(False, "confirmation_invalid", "确认已过期或操作参数已变化", ToolLifecycle.BLOCKED, request.capability, arguments, fingerprint)

        if spec.requires_admin_approval_for(arguments):
            try:
                from backend.security.tool_approval import ensure_approved

                receipt = ensure_approved(
                    request.capability,
                    operation.value,
                    user_id=request.user_id,
                    detail={"arguments": arguments, "impact": request.impact},
                )
            except (ImportError, RuntimeError) as exc:
                return GovernanceDecision(False, "approval_required", str(exc), ToolLifecycle.PENDING_APPROVAL, request.capability, arguments, fingerprint)
            if receipt:
                return GovernanceDecision(False, "approval_required", receipt, ToolLifecycle.PENDING_APPROVAL, request.capability, arguments, fingerprint)

        if needs_confirmation and not self._confirmations.consume(request.confirmation_id, fingerprint):
            return GovernanceDecision(False, "confirmation_invalid", "确认已过期或操作参数已变化", ToolLifecycle.BLOCKED, request.capability, arguments, fingerprint)

        return GovernanceDecision(True, "", "governance_pass", ToolLifecycle.READY, request.capability, arguments, fingerprint)

    def _make_preview(self, request: ToolCallRequest, spec: Any, operation: str, arguments: dict[str, Any], fingerprint: str) -> OperationPreview:
        return OperationPreview(
            confirmation_id=uuid.uuid4().hex,
            capability=request.capability,
            operation=operation,
            reason=request.selection_reason or "用户请求的 Tool 操作",
            target={"capability": request.capability},
            impact=request.impact or {
                "scope": "tenant_current",
                "irreversible": spec.effective_risk(arguments) == RiskLevel.R3,
            },
            preview={"arguments": arguments},
            args_hash=fingerprint,
            expires_at=time.time() + 600,
            tool_spec_version=spec.version,
        )

    def confirm(self, confirmation_id: str) -> OperationPreview | None:
        return self._confirmations.confirm(confirmation_id)

    def prepare(self, request: ToolCallRequest) -> GovernanceDecision:
        """显式执行 Prepare 阶段，供 API/SSE 确认卡消费。"""

        return self.evaluate(request)

    async def execute(
        self,
        request: ToolCallRequest,
        call: Callable[[], Any],
        *,
        normalize_output: bool = True,
        policy: ToolPolicy | None = None,
        deadline: Any | None = None,
        domain: str = "",
        on_event: Callable[[str, dict], None] | None = None,
        tool_name: str = "",
        trace_span: Any | None = None,
        trace_agent: str = "",
        trace_capability: str = "",
    ) -> ToolResult:
        request = self._request_context(request)
        decision = self.evaluate(request)
        if not decision.allowed:
            status = ToolStatus.UNAUTHORIZED if decision.code == "permission_denied" else ToolStatus.INVALID_REQUEST
            return ToolResult(status=status, tool_name=request.capability, error_code=decision.code, error_message=decision.reason)
        spec = get_tool_spec(request.capability)
        assert spec is not None
        effective_risk = spec.effective_risk(decision.arguments)
        budget = get_tool_budget() or RequestToolBudget()
        budget_error = budget.consume(
            request.capability,
            high_risk=effective_risk in (RiskLevel.R2, RiskLevel.R3),
            fingerprint=decision.fingerprint,
        )
        if budget_error:
            return ToolResult(status=ToolStatus.RATE_LIMITED, tool_name=request.capability, error_code=budget_error, error_message="Tool 调用预算已耗尽")
        if spec.idempotency_required and not request.idempotency_key:
            return ToolResult(status=ToolStatus.INVALID_REQUEST, tool_name=request.capability, error_code="idempotency_required", error_message="写操作缺少幂等键")
        if not rate_limiter.allow(
            f"{request.capability}:{request.tenant_id or request.user_id or 'anonymous'}",
            RateLimitConfig(spec.rate_limit_enabled, spec.rate_limit_requests, spec.rate_limit_window_seconds, spec.rate_limit_scope),
        ):
            return ToolResult(status=ToolStatus.RATE_LIMITED, tool_name=request.capability, error_code="tool_rate_limited", error_message="Tool 触发限流")

        dedupe_enabled = spec.dedupe_enabled and bool(request.user_id or request.tenant_id)
        if dedupe_enabled:
            with self._dedupe_lock:
                cached = self._dedupe.get(decision.fingerprint)
                if cached and cached[0] > time.time():
                    if spec.operation is not None and spec.operation.value != "read":
                        return ToolResult(status=ToolStatus.INVALID_REQUEST, tool_name=request.capability, error_code="idempotency_conflict", error_message="相同写操作已执行")
                    result = cached[1]
                    return ToolResult(**{**result.__dict__, "fallback_used": "dedupe_cache"})

        effective_policy = policy or ToolPolicy(
            timeout_ms=spec.timeout_ms,
            retries=spec.retry_max_attempts,
            circuit_breaker=spec.circuit_breaker_enabled,
            cb_failure_threshold=spec.circuit_failure_threshold,
            cb_recovery_seconds=spec.circuit_cooldown_seconds,
            operation_type=OperationType.WRITE if spec.effective_operation(decision.arguments).value != "read" else OperationType.READ,
            idempotent=bool(request.idempotency_key),
        )
        from backend.core.tool_runtime.executor import safe_tool_executor

        result = await safe_tool_executor.run(
            tool_key=request.capability,
            call=call,
            policy=effective_policy,
            operation_type=effective_policy.operation_type,
            deadline=deadline,
            domain=domain or request.capability.split(".", 1)[0],
            on_event=on_event,
            tool_name=tool_name,
            trace_span=trace_span,
            trace_capability=trace_capability or request.capability,
            trace_agent=trace_agent,
            trace_params=decision.arguments,
        )
        if result.status is ToolStatus.SUCCESS and normalize_output:
            envelope = validate_tool_result(result.data, spec)
            if envelope.status == "failed":
                return ToolResult(status=ToolStatus.FAILED, tool_name=request.capability, data=envelope.to_dict(), error_code=(envelope.error or {}).get("code", "output_invalid"), error_message=(envelope.error or {}).get("message", "Tool 输出不合法"))
            result.data = envelope.to_dict()
            if dedupe_enabled:
                with self._dedupe_lock:
                    self._dedupe[decision.fingerprint] = (time.time() + spec.dedupe_ttl_seconds, result)
        elif result.status is ToolStatus.SUCCESS and dedupe_enabled:
            with self._dedupe_lock:
                self._dedupe[decision.fingerprint] = (time.time() + spec.dedupe_ttl_seconds, result)
        return result

    def execute_sync(
        self,
        request: ToolCallRequest,
        call: Callable[[], Any],
        **kwargs: Any,
    ) -> ToolResult:
        """同步适配器，供同步域图/Tool Adapter 复用同一治理链路。

        sync/async 边界契约：本方法用 asyncio.run 驱动治理门 + 执行器
        （它们是 async 实现），**事件循环只属于这层机器**；``call``（用户
        Tool 代码）由 SafeToolExecutor 统一挪到工作线程执行——同步 Tool
        内部再起 asyncio.run（MCP 同步桥）不会撞上运行中的 loop
        （2026-10-06 12306 全挂事故的根因）。因此本方法要求调用线程自身
        没有正在运行的事件循环；异步调用方请直接 await :meth:`execute`。
        """

        return asyncio.run(self.execute(request, call, **kwargs))

    def reset(self) -> None:
        self._confirmations.reset()
        with self._dedupe_lock:
            self._dedupe.clear()


governance_runtime = GovernanceRuntime()

__all__ = [
    "GovernanceDecision",
    "GovernanceRuntime",
    "OperationPreview",
    "ToolCallRequest",
    "governance_runtime",
]
