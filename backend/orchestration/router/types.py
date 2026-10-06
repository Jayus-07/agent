"""router/types.py — 路由类型定义（2026-08-11）

核心原则（用户设计 review 后）：
  - execution_mode 只决定 HOW（执行方式），不决定 WHO（哪个 capability）
  - candidates 是带分数的能力候选，由 Planner 决定最终 DAG
  - Router 不承担业务判断（不做 mode + capability 绑定）
"""
from __future__ import annotations

from enum import Enum
from collections.abc import Mapping
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from backend.orchestration.runtime_types import RuntimeType


class ExecutionMode(str, Enum):
    """执行方式（不绑定具体 capability）。

    - DIRECT: 单个 capability 直接执行（无需 Planner 生成 DAG）
    - PLAN: 复杂任务，Planner 用 candidates 生成最终 DAG
    - WORKFLOW: 已注册的工作流（daily_report / inventory_alert）
    """
    DIRECT = "direct"
    PLAN = "plan"
    WORKFLOW = "workflow"


class InteractionMode(str, Enum):
    """请求与用户的交互控制模式。"""

    EXECUTE = "execute"
    GUIDE = "guide"
    CLARIFY = "clarify"
    HANDOFF = "handoff"


class CapabilityScore(BaseModel):
    """单个 capability 的评分（Router 给的 hint，不是最终决定）。"""
    name: str = Field(..., description="capability 名，如 'sql.query'")
    score: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")


class DomainDecisionV2(BaseModel):
    """V2 顶级业务域判断。"""

    name: str = Field(..., min_length=1)
    subflow: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = "unknown"
    reasoning: str = ""


class IntentDecisionV2(BaseModel):
    """V2 用户意图判断。"""

    name: str = Field(..., min_length=1)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = "unknown"
    reasoning: str = ""


class CapabilityDecisionV2(BaseModel):
    """V2 能力候选判断，不代表已经执行。"""

    name: str | None = None
    candidates: list[CapabilityScore] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = "unknown"
    reasoning: str = ""


class ExecutionDecision(BaseModel):
    """主图兼容执行族，只有 direct/plan/workflow。"""

    mode: ExecutionMode


class InteractionDecision(BaseModel):
    """交互控制，不混入执行方式。"""

    mode: InteractionMode


class RuntimeTarget(BaseModel):
    """具体 Runtime 目标。"""

    type: RuntimeType
    id: str = Field(..., min_length=1)
    subflow: str | None = None


class RouteDecisionV2(BaseModel):
    """RouteDecision V2：Domain、Execution、Interaction、Runtime 正交表达。"""

    domain: DomainDecisionV2
    intent: IntentDecisionV2
    execution: ExecutionDecision
    interaction: InteractionDecision
    runtime: RuntimeTarget
    capability: CapabilityDecisionV2 | None = None
    workflow_name: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_runtime_execution_pair(self) -> "RouteDecisionV2":
        """阻止 Runtime 家族与主图执行方式发生不可解释的交叉。"""

        expected_modes = {
            RuntimeType.DIRECT: ExecutionMode.DIRECT,
            RuntimeType.PLAN: ExecutionMode.PLAN,
            RuntimeType.WORKFLOW: ExecutionMode.WORKFLOW,
            RuntimeType.AGENT: ExecutionMode.WORKFLOW,
        }
        expected = expected_modes.get(self.runtime.type)
        if expected is not None and self.execution.mode is not expected:
            raise ValueError(
                f"runtime_type={self.runtime.type.value} 必须使用 "
                f"execution={expected.value}，实际为 {self.execution.mode.value}"
            )
        return self


class RuntimeResult(BaseModel):
    """Runtime 统一结果协议；不承担二次生成。"""

    status: Literal["success", "partial", "clarification", "handoff", "error"] = "success"
    answer: str | None = None
    answer_type: Literal["text", "markdown", "json", "stream"] = "text"
    sources: list[Any] = Field(default_factory=list)
    artifacts: list[Any] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    ui_payload: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    error: dict[str, Any] | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeContext(BaseModel):
    """Runtime 入口上下文，保证可随 checkpoint 序列化。"""

    session_id: str = ""
    tenant_id: str = ""
    user_id: str = ""
    question: str = ""
    route_decision: RouteDecisionV2 | None = None
    state: dict[str, Any] = Field(default_factory=dict)


def normalize_runtime_result(value: object, *, runtime_id: str) -> RuntimeResult:
    """把旧域输出归一为 RuntimeResult，不调用 LLM。"""

    if isinstance(value, RuntimeResult):
        if value.metadata.get("runtime_id") == runtime_id:
            return value
        return value.model_copy(
            update={"metadata": {**value.metadata, "runtime_id": runtime_id}}
        )

    if isinstance(value, str):
        return RuntimeResult(
            answer=value,
            metadata={"runtime_id": runtime_id},
        )

    if not isinstance(value, Mapping):
        return RuntimeResult(
            answer=str(value) if value is not None else None,
            answer_type="text",
            metadata={"runtime_id": runtime_id},
        )

    answer = value.get("answer") or value.get("final_answer")
    answer_type = value.get("answer_type") or ("text" if isinstance(answer, str) else "json")
    reserved = {
        "answer", "final_answer", "answer_type", "sources", "artifacts",
        "tool_calls", "ui_payload", "clarification", "handoff", "error",
        "metadata", "status",
    }
    payload = value.get("ui_payload")
    if payload is None and answer is None:
        payload = {key: item for key, item in value.items() if key not in reserved}
    metadata = {**(value.get("metadata") or {}), "runtime_id": runtime_id}
    status = value.get("status") or "success"
    if value.get("clarification"):
        status = "clarification"
    elif value.get("handoff"):
        status = "handoff"
    return RuntimeResult(
        status=status,
        answer=answer,
        answer_type=answer_type,
        sources=list(value.get("sources") or []),
        artifacts=list(value.get("artifacts") or []),
        tool_calls=list(value.get("tool_calls") or []),
        ui_payload=payload,
        clarification=value.get("clarification"),
        handoff=value.get("handoff"),
        error=value.get("error"),
        metadata=metadata,
    )


class RouteDecision(BaseModel):
    """路由决策（Router 输出，Planner 消费）。

    设计原则：
    - candidates 列表提供 hints，**不** 强制选
    - execution_mode 决定执行方式
    - route_mode 决定主图最终归宿，兼容域图与澄清分支
    - Planner 决定最终 DAG（基于 candidates + query）
    """
    execution_mode: ExecutionMode
    # 对外暴露“路由归宿”而不是把域图/澄清硬塞进 execution_mode。
    # execution_mode 继续保持 direct/plan/workflow 的下游兼容契约；
    # route_mode 补齐 domain_graph/clarify/general_chat 的可观测分支。
    route_mode: Optional[str] = Field(
        None,
        description="最终路由归宿：direct/workflow/plan/clarify/domain_graph/general_chat",
    )
    candidates: List[CapabilityScore] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0, description="整体路由置信度")
    reason: Optional[str] = Field(None, description="路由判断依据")
    workflow_name: Optional[str] = Field(None, description="WORKFLOW 模式时指定 workflow 名")
    # 分层路由（hierarchical routing，2026-09-22）决策上下文。
    # router_node 消费它写 state 平铺字段（domain/candidate_tools/...）；
    # 统一路由引擎元数据。dict 可序列化，路由缓存（model_dump）兼容。
    routing_meta: Optional[dict] = Field(None, description="分层路由决策上下文（粗分类/细选择明细）")


# ── Capability / Workflow 名单：由 capabilities.yaml 派生（唯一事实源）──
# 手写清单已成历史：曾因 competitor.analyze 只在 rule_router 硬编码、
# ALL_CAPABILITIES 缺失，被 LLM Router 拒绝（见 manifest.py 模块注释）。
# 新增/修改 capability 一律改 capabilities.yaml，这里不许再手写。
from backend.orchestration.router.manifest import load_manifest

_manifest = load_manifest()
ALL_CAPABILITIES = [c.name for c in _manifest.routed_capabilities]
# 含 routed: false 的内部能力（workflow 内部消费，不对用户问题开放路由）
ALL_DECLARED_CAPABILITIES = [c.name for c in _manifest.capabilities]
WORKFLOW_NAMES = [w.name for w in _manifest.workflows]
