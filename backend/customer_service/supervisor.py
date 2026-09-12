"""
customer_service/supervisor.py — CS Supervisor 三层决策引擎

职责: 结合状态 + 上下文决定下一步调用哪个 Expert。
不做: 不执行业务逻辑、不操作 DB、不生成最终回复。

三层决策模型:
  Layer 1 — 硬规则: handoff 拦截 / loop guard / confidence 降级
  Layer 2 — 状态组合: confirmation pending / expert history 模式
  Layer 3 — LLM 决策: 低置信度 + 复杂状态时启用（带超时 + 确定性降级）

设计参考: docs/customer-service/langgraph-multi-expert-design.md §5.3
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, TypedDict

from langgraph.types import Command

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


_ROUTE_PATH_TO_EXPERT: dict[str, str] = {
    "knowledge_query": ExpertType.KNOWLEDGE.value,
    "business_query": ExpertType.QUERY.value,
    "business_action": ExpertType.ACTION.value,
    "complaint_flow": ExpertType.COMPLAINT.value,
    "human_handoff": ExpertType.HANDOFF.value,
}

_DOMAIN_TO_EXPERT: dict[str, str] = {
    "KNOWLEDGE": ExpertType.KNOWLEDGE.value,
    "TRANSACTION": ExpertType.QUERY.value,
    "AFTER_SALES": ExpertType.ACTION.value,
    "ACCOUNT": ExpertType.ACTION.value,
    "COMPLAINT": ExpertType.COMPLAINT.value,
    "HUMAN": ExpertType.HANDOFF.value,
}


def _make_decision(
    action: ExpertAction,
    expert: ExpertType | None,
    layer: int,
    reason: str,
    *,
    requires_confirmation: bool = False,
    requires_handoff: bool = False,
    is_finished: bool = False,
) -> CSSupervisorDecision:
    return CSSupervisorDecision(
        next_action=action.value,
        next_expert=expert.value if expert else None,
        decision_layer=layer,
        reason=reason,
        requires_confirmation=requires_confirmation,
        requires_handoff=requires_handoff,
        is_finished=is_finished,
        context_updates={},
    )


def _resolve_expert(cs_route: dict) -> str:
    """从 cs_route 解析目标 expert — route_path 优先，domain 兜底。"""
    route_path = cs_route.get("route_path", "")
    if route_path and route_path in _ROUTE_PATH_TO_EXPERT:
        return _ROUTE_PATH_TO_EXPERT[route_path]

    domain = cs_route.get("domain", "KNOWLEDGE")
    return _DOMAIN_TO_EXPERT.get(domain, ExpertType.KNOWLEDGE.value)


def make_supervisor_decision(state: dict[str, Any]) -> CSSupervisorDecision:
    """三层决策引擎。

    Layer 1 — 硬规则（零延迟、确定性）:
      1a. handoff 拦截 — handoff_state != ai_active 时强制转 handoff expert
      1b. loop guard — expert_loop_count >= MAX 时强制 finish
      1c. confidence 降级 — confidence < 阈值 且无 expert 历史 → 直接 finish

    Layer 2 — 状态组合（零延迟、确定性）:
      2a. confirmation pending → finish（等用户回复）
      2b. expert 重复检测 — 同一 expert 连续执行 2 次 → finish

    Layer 3 — LLM 决策（带超时 + 确定性降级）:
      仅在低置信度 + 复杂状态时启用。
    """
    cs_route = state.get("cs_route", {})
    confidence = cs_route.get("confidence", 0.0)
    handoff_state = state.get("handoff_state", "ai_active")
    confirmation_state = state.get("confirmation_state", "not_required")
    expert_loop_count = state.get("expert_loop_count", 0)
    expert_history = state.get("expert_history", [])

    from backend.config.customer_service import (
        CS_CONFIDENCE_CAUTIOUS,
        CS_EXPERT_MAX_LOOPS,
    )

    # ── Layer 1a: handoff 拦截 ──
    from backend.customer_service.handoff import HandoffState, should_intercept
    try:
        _hs = HandoffState(handoff_state) if handoff_state else HandoffState.AI_ACTIVE
    except ValueError:
        _hs = HandoffState.AI_ACTIVE
    if should_intercept(_hs):
        decision = _make_decision(
            ExpertAction.HANDOFF, ExpertType.HANDOFF, layer=1,
            reason=f"handoff 拦截: state={handoff_state}",
            requires_handoff=True,
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 1b: loop guard ──
    if expert_loop_count >= CS_EXPERT_MAX_LOOPS:
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=f"expert 循环达上限 ({CS_EXPERT_MAX_LOOPS})",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 2a: confirmation pending ──
    if confirmation_state in ("pending", "pending_confirmation"):
        decision = _make_decision(
            ExpertAction.PENDING, None, layer=2,
            reason="confirmation pending — 等待用户确认",
            requires_confirmation=True,
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 2b: expert 重复检测 ──
    if _is_expert_repeating(expert_history):
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=2,
            reason="expert 重复执行 — 终止防止死循环",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 1c: confidence 降级 ──
    if confidence < CS_CONFIDENCE_CAUTIOUS and not expert_history:
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=f"低置信度 ({confidence:.2f} < {CS_CONFIDENCE_CAUTIOUS}) 且无历史 — 降级兜底",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 3: LLM 决策（仅低置信度时启用）──
    if confidence < CS_CONFIDENCE_CAUTIOUS and expert_history:
        llm_decision = _llm_decision(state)
        if llm_decision is not None:
            _record_decision(llm_decision)
            return llm_decision

    # ── 默认: route_path → expert ──
    expert = _resolve_expert(cs_route)
    decision = _make_decision(
        ExpertAction.RUN_EXPERT,
        ExpertType(expert),
        layer=1 if confidence >= CS_CONFIDENCE_CAUTIOUS else 3,
        reason=f"route → expert={expert} (confidence={confidence:.2f})",
    )
    _record_decision(decision)
    return decision


def _is_expert_repeating(expert_history: list[dict]) -> bool:
    """检测同一 expert 是否连续执行 2 次。"""
    if len(expert_history) < 2:
        return False
    return (
        expert_history[-1].get("expert") == expert_history[-2].get("expert")
    )


def _llm_decision(state: dict[str, Any]) -> CSSupervisorDecision | None:
    """Layer 3: LLM 决策（带超时 + 确定性降级）。

    仅在 CS_SUPERVISOR_LLM_ENABLED=true 时启用。
    超时或异常时返回 None，调用方回退到默认路由。
    """
    from backend.config.customer_service import (
        CS_SUPERVISOR_LLM_ENABLED,
        CS_SUPERVISOR_LLM_TIMEOUT_MS,
    )

    if not CS_SUPERVISOR_LLM_ENABLED:
        return None

    cs_route = state.get("cs_route", {})
    user_message = state.get("user_message", "")
    expert_history = state.get("expert_history", [])

    prev_experts = [e.get("expert", "") for e in expert_history]
    prompt = (
        "你是客服系统 Supervisor。根据用户问题和已执行的 Expert 历史，"
        "选择下一个最合适的 Expert。\n\n"
        f"用户问题: {user_message[:200]}\n"
        f"路由意图: {cs_route.get('intent', 'unknown')}\n"
        f"已执行 Expert: {prev_experts}\n\n"
        "可选 Expert: knowledge, query, action, complaint, handoff\n"
        "只回复一个词（expert 名称），不要解释。"
    )

    try:
        from langchain_core.messages import HumanMessage

        from backend.infra.llm import get_llm

        llm = get_llm()
        t0 = time.monotonic()
        timeout_s = CS_SUPERVISOR_LLM_TIMEOUT_MS / 1000.0

        response = llm.invoke(
            [HumanMessage(content=prompt)],
            config={"timeout": timeout_s},
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        chosen = response.content.strip().lower()
        valid_experts = {e.value for e in ExpertType}
        if chosen not in valid_experts:
            logger.warning(
                "[CS Supervisor L3] LLM 返回无效 expert=%s, 降级", chosen,
            )
            return None

        logger.info(
            "[CS Supervisor L3] LLM chose expert=%s elapsed_ms=%d",
            chosen, elapsed_ms,
        )

        return _make_decision(
            ExpertAction.RUN_EXPERT,
            ExpertType(chosen),
            layer=3,
            reason=f"LLM 决策: expert={chosen} ({elapsed_ms}ms)",
        )

    except Exception as e:
        logger.warning("[CS Supervisor L3] LLM 调用失败，降级到默认路由: %s", e)
        return None


def _record_decision(decision: CSSupervisorDecision) -> None:
    """埋点 Supervisor 决策。"""
    try:
        from backend.observability.metrics import record_cs_supervisor_decision
        layer_label = {1: "rule", 2: "combination", 3: "llm"}.get(
            decision["decision_layer"], "unknown"
        )
        record_cs_supervisor_decision(layer_label, decision["next_action"])
    except Exception:
        pass


def _recover_handoff_timeout(state: dict[str, Any]) -> dict[str, Any] | None:
    """人工接入超时回退（CS_HANDOFF_TIMEOUT_SECONDS）。

    handoff_requested / waiting_human 超过时限仍无人工接入 → 关闭本次
    转接（状态机两者均可合法转换到 CLOSED），清 store，handoff_state
    归 ai_active，本 turn 恢复正常专家路由。未超时/无活跃转接返回 None。
    """
    from datetime import datetime, timezone

    from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
    from backend.customer_service.audit import append_audit, build_audit_entry
    from backend.customer_service.handoff_store import get_handoff_store

    handoff_state = state.get("handoff_state", "ai_active")
    if handoff_state not in ("handoff_requested", "waiting_human"):
        return None

    user_id = state.get("user_id", "")
    session_id = state.get("session_id", "default")
    store = get_handoff_store()
    active = store.get_active_handoff(user_id)
    if not active:
        return None

    stamp = active.get("updated_at") or active.get("created_at")
    if not stamp:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    if age.total_seconds() < CS_HANDOFF_TIMEOUT_SECONDS:
        return None

    store.clear(user_id, session_id)
    logger.warning(
        "[CS Supervisor] 人工接入超时 (%.0fs >= %ds)，回退 AI 服务: user=%s",
        age.total_seconds(), CS_HANDOFF_TIMEOUT_SECONDS, user_id,
    )
    audit_entry = build_audit_entry(
        user_id=user_id or "anonymous",
        action_type="handoff_timeout_recovered",
        result="success",
        target_type="handoff",
        target_id=str(active.get("ticket_id", "")),
        detail=f"handoff timeout after {int(age.total_seconds())}s, recovered to ai_active",
        conversation_id=state.get("conversation_id", ""),
    )
    return {
        "handoff_state": "ai_active",
        "cs_audit_entries": append_audit(
            list(state.get("cs_audit_entries", [])), audit_entry,
        ),
        "cs_context": {
            **(state.get("cs_context") or {}),
            "handoff_state": "ai_active",
        },
    }


def cs_supervisor_node(state: dict[str, Any]) -> Command:
    """CS Supervisor 节点函数 — 返回 Command(goto=..., update={...})

    Phase 4: 使用 Command 模式替代 conditional edges。
    """
    from backend.customer_service.graph_state import (
        CS_ACTION_EXPERT,
        CS_COMPLAINT_EXPERT,
        CS_HANDOFF_EXPERT,
        CS_KNOWLEDGE_EXPERT,
        CS_QUERY_EXPERT,
        CS_REPORTER,
    )

    timeout_update = _recover_handoff_timeout(state)
    if timeout_update is not None:
        state = {**state, **timeout_update}

    decision = make_supervisor_decision(state)
    action = decision["next_action"]
    expert = decision.get("next_expert", "")

    if action in (ExpertAction.FINISH.value, ExpertAction.PENDING.value):
        target = CS_REPORTER
    elif action == ExpertAction.HANDOFF.value and decision.get("is_finished"):
        target = CS_REPORTER
    elif action == ExpertAction.HANDOFF.value:
        target = CS_HANDOFF_EXPERT
    else:
        _expert_to_node = {
            ExpertType.KNOWLEDGE.value: CS_KNOWLEDGE_EXPERT,
            ExpertType.QUERY.value: CS_QUERY_EXPERT,
            ExpertType.ACTION.value: CS_ACTION_EXPERT,
            ExpertType.COMPLAINT.value: CS_COMPLAINT_EXPERT,
            ExpertType.HANDOFF.value: CS_HANDOFF_EXPERT,
        }
        target = _expert_to_node.get(expert, CS_KNOWLEDGE_EXPERT)

    logger.info(
        "[CS Supervisor] action=%s expert=%s layer=%d reason=%s → %s",
        action, expert, decision["decision_layer"], decision["reason"], target,
    )

    return Command(
        goto=target,
        update={
            "supervisor_decision": dict(decision),
            "current_expert": expert or "",
            "expert_loop_count": state.get("expert_loop_count", 0) + 1,
            **(timeout_update or {}),
        },
    )
