"""
customer_service/supervisor.py — CS Supervisor 三层决策引擎

职责: 结合状态 + 上下文决定下一步调用哪个 Expert。
不做: 不执行业务逻辑、不操作 DB、不生成最终回复。

Phase 0: 骨架实现 — 仅 route_path→expert 映射表
Phase 2: 完善三层决策（Layer 1 规则 / Layer 2 状态组合 / Layer 3 LLM）

设计参考: docs/customer-service/langgraph-multi-expert-design.md §5.3
"""
from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict

from backend.shared.logger import logger


class ExpertAction(str, Enum):
    """Supervisor 可下发的动作类型"""
    RUN_EXPERT = "run_expert"
    FINISH = "finish"
    HANDOFF = "handoff"
    PENDING = "pending"


class ExpertType(str, Enum):
    """5 个 Expert Agent"""
    KNOWLEDGE = "knowledge"
    QUERY = "query"
    ACTION = "action"
    COMPLAINT = "complaint"
    HANDOFF = "handoff"


class CSSupervisorDecision(TypedDict, total=False):
    """Supervisor 决策输出"""
    next_action: str
    next_expert: str | None
    decision_layer: int
    reason: str
    requires_confirmation: bool
    requires_handoff: bool
    is_finished: bool
    context_updates: dict


_ROUTE_TO_EXPERT: dict[str, str] = {
    "KNOWLEDGE": ExpertType.KNOWLEDGE.value,
    "TRANSACTION": ExpertType.QUERY.value,
    "AFTER_SALES": ExpertType.ACTION.value,
    "ACCOUNT": ExpertType.ACTION.value,
    "COMPLAINT": ExpertType.COMPLAINT.value,
    "HUMAN": ExpertType.HANDOFF.value,
}


def make_supervisor_decision(state: dict[str, Any]) -> CSSupervisorDecision:
    """Phase 0 骨架: 基于 cs_route.domain 的简单映射

    Phase 2 将替换为完整的三层决策模型。
    """
    cs_route = state.get("cs_route", {})
    domain = cs_route.get("domain", "KNOWLEDGE")
    confidence = cs_route.get("confidence", 0.0)
    handoff_state = state.get("handoff_state", "ai_active")
    confirmation_state = state.get("confirmation_state", "not_required")
    expert_loop_count = state.get("expert_loop_count", 0)

    from backend.config.customer_service import CS_EXPERT_MAX_LOOPS

    if handoff_state != "ai_active":
        return CSSupervisorDecision(
            next_action=ExpertAction.HANDOFF.value,
            next_expert=ExpertType.HANDOFF.value,
            decision_layer=1,
            reason=f"handoff 拦截: state={handoff_state}",
            requires_confirmation=False,
            requires_handoff=True,
            is_finished=False,
            context_updates={},
        )

    if confirmation_state == "pending":
        return CSSupervisorDecision(
            next_action=ExpertAction.PENDING.value,
            next_expert=None,
            decision_layer=1,
            reason="confirmation pending — 等待用户确认",
            requires_confirmation=True,
            requires_handoff=False,
            is_finished=True,
            context_updates={},
        )

    if expert_loop_count >= CS_EXPERT_MAX_LOOPS:
        return CSSupervisorDecision(
            next_action=ExpertAction.FINISH.value,
            next_expert=None,
            decision_layer=1,
            reason=f"expert 循环达上限 ({CS_EXPERT_MAX_LOOPS})",
            requires_confirmation=False,
            requires_handoff=False,
            is_finished=True,
            context_updates={},
        )

    expert = _ROUTE_TO_EXPERT.get(domain, ExpertType.KNOWLEDGE.value)

    return CSSupervisorDecision(
        next_action=ExpertAction.RUN_EXPERT.value,
        next_expert=expert,
        decision_layer=1,
        reason=f"domain={domain} → expert={expert} (confidence={confidence:.2f})",
        requires_confirmation=False,
        requires_handoff=False,
        is_finished=False,
        context_updates={},
    )


def cs_supervisor_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Supervisor 节点函数

    调用 make_supervisor_decision，更新 state 中的决策字段 + 递增 loop count。
    """
    decision = make_supervisor_decision(state)
    logger.debug(
        "[CS Supervisor] action=%s expert=%s layer=%d reason=%s",
        decision["next_action"],
        decision.get("next_expert"),
        decision["decision_layer"],
        decision["reason"],
    )

    return {
        "supervisor_decision": dict(decision),
        "current_expert": decision.get("next_expert", "") or "",
        "expert_loop_count": state.get("expert_loop_count", 0) + 1,
    }


def route_after_cs_supervisor(state: dict[str, Any]) -> str:
    """conditional-edge 函数: Supervisor 之后路由到哪个节点

    Phase 4 将替换为 Command(goto=...) 模式。
    """
    from backend.customer_service.graph_state import (
        CS_KNOWLEDGE_EXPERT,
        CS_QUERY_EXPERT,
        CS_ACTION_EXPERT,
        CS_COMPLAINT_EXPERT,
        CS_HANDOFF_EXPERT,
        CS_REPORTER,
    )

    decision = state.get("supervisor_decision", {})
    action = decision.get("next_action", ExpertAction.FINISH.value)
    expert = decision.get("next_expert", "")

    if action == ExpertAction.FINISH.value or action == ExpertAction.PENDING.value:
        return CS_REPORTER

    if action == ExpertAction.HANDOFF.value:
        return CS_HANDOFF_EXPERT

    expert_to_node = {
        ExpertType.KNOWLEDGE.value: CS_KNOWLEDGE_EXPERT,
        ExpertType.QUERY.value: CS_QUERY_EXPERT,
        ExpertType.ACTION.value: CS_ACTION_EXPERT,
        ExpertType.COMPLAINT.value: CS_COMPLAINT_EXPERT,
        ExpertType.HANDOFF.value: CS_HANDOFF_EXPERT,
    }

    return expert_to_node.get(expert, CS_KNOWLEDGE_EXPERT)
