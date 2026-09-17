"""customer_service/experts/handoff.py — HandoffExpert

人工转接 Expert：检测触发 → 状态转换 → 创建工单 → 返回等待提示。
从 graph/nodes.py cs_handoff 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.5
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger


def execute_handoff(
    user_message: str,
    state: dict[str, Any],
) -> ExpertResult:
    """HandoffExpert 核心逻辑。

    Args:
        user_message: 用户原始问题
        state: CSGraphState（含 user_id / session_id / conversation_id）

    Returns:
        ExpertResult — response_draft 为转接提示，data 含工单 + handoff 信息
    """
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.handoff import (
        HandoffState,
        detect_handoff_trigger,
    )
    from backend.customer_service.handoff import (
        transition as handoff_transition,
    )
    from backend.customer_service.handoff_store import get_handoff_store
    from backend.observability.metrics import record_cs_handoff

    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "default")
    conversation_id = state.get("conversation_id", "")

    logger.info("[HandoffExpert] user_id=%s initiating handoff", user_id)

    explicit_trigger = detect_handoff_trigger(user_message)
    if explicit_trigger:
        trigger_type = explicit_trigger.trigger_type.value
        trigger_reason = explicit_trigger.reason
    else:
        trigger_type = "auto_trigger"
        trigger_reason = "系统自动触发 (低置信度/连续失败)"

    record_cs_handoff(trigger_type)

    store = get_handoff_store()
    existing = store.load(user_id, session_id)
    current_state_str = (existing or {}).get(
        "handoff_state", HandoffState.AI_ACTIVE.value,
    )
    current_state = HandoffState(current_state_str)

    # 幂等保护（2026-09-17）：Supervisor 可能对同一 handoff 意图重复调度
    # 本 expert（实测同一 turn 内 reason=route 连跑两次）。工单已在排队/
    # 人工处理中时复用工单直接成功返回 —— waiting_human → handoff_requested
    # 是非法转换，二次执行绝不能炸（炸了用户会收到兜底报错文案）。
    if current_state in (
        HandoffState.WAITING_HUMAN, HandoffState.HUMAN_ACTIVE,
    ):
        reused_ticket = (existing or {}).get("ticket_id", "")
        # 文案按状态区分（2026-09-17）：人工已接入时仍说"请稍候"会误导。
        if current_state == HandoffState.HUMAN_ACTIVE:
            answer = "人工客服正在为您服务，您的消息已同步给客服人员。"
        else:
            answer = "已为您转接人工客服，请稍候。客服人员将尽快为您服务。"
        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type="handoff_duplicate_skipped",
            result="success",
            target_type="handoff",
            target_id=reused_ticket,
            detail=f"already {current_state.value}, reuse ticket",
            conversation_id=conversation_id,
        )
        logger.info(
            "[HandoffExpert] duplicate trigger, reuse ticket=%s state=%s",
            reused_ticket, current_state.value,
        )
        return ExpertResult(
            expert="handoff",
            status=ExpertStatus.SUCCESS.value,
            response_draft=answer,
            data={
                "ticket_id": reused_ticket,
                "trigger_type": trigger_type,
                "trigger_reason": trigger_reason,
                "handoff_state": current_state.value,
                "handling_mode": "human",
                "duplicate": True,
                "audit_entry": audit_entry,
            },
        )

    handoff_transition(current_state, HandoffState.HANDOFF_REQUESTED)

    ticket_id = f"HANDOFF-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    handoff_data = {
        "handoff_state": HandoffState.HANDOFF_REQUESTED.value,
        "trigger_type": trigger_type,
        "trigger_reason": trigger_reason,
        "ticket_id": ticket_id,
        "created_at": now,
        "updated_at": now,
    }
    store.save(user_id, session_id, handoff_data)

    # 2026-09-17 修复：工单立即进入排队（HANDOFF_REQUESTED → WAITING_HUMAN）。
    # 此前生产代码没有任何位置执行这一步，工单永久卡在 handoff_requested，
    # 坐席认领 409、工作台输入框永远锁定（演示沙盒方案 §七·阶段2）。
    # 拦截语义不变：should_intercept 对两个状态都返回 True。
    handoff_transition(
        HandoffState.HANDOFF_REQUESTED, HandoffState.WAITING_HUMAN,
    )
    handoff_data["handoff_state"] = HandoffState.WAITING_HUMAN.value
    handoff_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    store.save(user_id, session_id, handoff_data)

    # 实时推送：新工单进入坐席待接入队列（WebSocket，无连接时静默丢弃）
    from backend.customer_service.realtime import get_agent_hub
    get_agent_hub().publish(
        "conversation.waiting",
        item={
            "conversation_id": session_id,  # handoff 行的 conversation_id 即 session_id
            "user_id": user_id,
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "trigger_type": trigger_type,
            "trigger_reason": trigger_reason,
            "updated_at": handoff_data["updated_at"],
            "last_message_preview": (user_message[:80] if user_message else None),
        },
    )

    answer = "已为您转接人工客服，请稍候。客服人员将尽快为您服务。"

    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type="handoff_requested",
        result="success",
        target_type="handoff",
        target_id=ticket_id,
        detail=f"trigger={trigger_type}, reason={trigger_reason[:80]}",
        conversation_id=conversation_id,
    )

    logger.info(
        "[HandoffExpert] ticket=%s trigger=%s state=WAITING_HUMAN",
        ticket_id, trigger_type,
    )

    return ExpertResult(
        expert="handoff",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={
            "ticket_id": ticket_id,
            "trigger_type": trigger_type,
            "trigger_reason": trigger_reason,
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "handling_mode": "human",
            "audit_entry": audit_entry,
        },
    )


def handoff_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph HandoffExpert 节点函数。"""
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")

    result = run_expert_safely(
        expert_name="handoff",
        fn=lambda _state: execute_handoff(user_message, state),
        state=state,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "handoff",
        "status": result.get("status", "failed"),
        "duration_ms": result.get("duration_ms", 0),
    })

    audit_entries = list(state.get("cs_audit_entries", []))
    if result.get("status") == "success" and result.get("data", {}).get("audit_entry"):
        from backend.customer_service.audit import append_audit
        audit_entries = append_audit(audit_entries, result["data"]["audit_entry"])

    cs_context = dict(state.get("cs_context", {}))
    if result.get("data", {}).get("handoff_state"):
        cs_context["handoff_state"] = result["data"]["handoff_state"]
    if result.get("data", {}).get("handling_mode"):
        cs_context["handling_mode"] = result["data"]["handling_mode"]

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
        "cs_audit_entries": audit_entries,
        "cs_context": cs_context,
    }
