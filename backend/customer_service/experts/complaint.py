"""customer_service/experts/complaint.py — ComplaintExpert

投诉处理 Expert：投诉检测 → 创建工单 → 安抚响应 → 触发转接。
从 graph/nodes.py cs_complaint 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.4
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger


def execute_complaint(
    user_message: str,
    state: dict[str, Any],
) -> ExpertResult:
    """ComplaintExpert 核心逻辑。

    Args:
        user_message: 用户原始问题
        state: CSGraphState（含 user_id / session_id / conversation_id）

    Returns:
        ExpertResult — response_draft 为安抚响应，data 含工单 + handoff 信息
    """
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.handoff import HandoffState, transition as handoff_transition
    from backend.customer_service.service.complaint_service import get_complaint_service
    from backend.observability.metrics import record_cs_handoff

    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "default")
    conversation_id = state.get("conversation_id", "")
    cs_context = state.get("cs_context", {}) or {}

    # 防重入：本会话已建过投诉工单则幂等返回，不再重复建单/触发转接
    # （supervisor 的"连续同名专家"检测只能拦相邻重复，拦不住
    #   complaint → knowledge → complaint 的交替重入）
    # 检查两处：① cs_context 内的幂等标记（同 turn）② handoff store 中
    # 本会话由投诉升级产生的活跃转接（跨 turn / 上下文被裁剪后）
    existing_ticket = cs_context.get("complaint_ticket_id")
    if not existing_ticket:
        try:
            from backend.customer_service.handoff_store import get_handoff_store
            active = get_handoff_store().get_active_handoff(user_id)
            if active and active.get("trigger_type") == "complaint_escalation":
                existing_ticket = active.get("ticket_id")
        except Exception:
            logger.warning(
                "[ComplaintExpert] handoff store 查询失败，跳过跨 turn 幂等检查",
                exc_info=True,
            )
    if existing_ticket:
        logger.info(
            "[ComplaintExpert] 幂等返回: ticket=%s（会话已有投诉工单）",
            existing_ticket,
        )
        return ExpertResult(
            expert="complaint",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                f"您的投诉工单（{existing_ticket}）已在处理中，"
                "专人正在跟进，我们会尽快给您答复。"
            ),
            data={
                "ticket_id": existing_ticket,
                "severity": cs_context.get("complaint_severity", "medium"),
                "duplicate": True,
            },
        )

    logger.info(
        "[ComplaintExpert] user_id=%s question=%s...",
        user_id, user_message[:60],
    )

    service = get_complaint_service()
    detection = service.detect_with_llm_fallback(user_message)

    ticket = service.create_ticket(
        user_id=user_id,
        conversation_id=conversation_id,
        severity=detection.severity,
        summary=user_message,
    )
    service.simulate_execute(ticket)

    answer = service.build_comfort_response(detection, ticket)

    # P1 重构（2026-09-17）：投诉升级与显式转人工同流程 ——
    # 状态机内存转换 AI_ACTIVE→REQUESTED→WAITING_HUMAN，单次落盘
    # 最终态 waiting_human（此前卡 handoff_requested，坐席认领 409），
    # 并与 HandoffExpert 一致发布 conversation.waiting 实时事件。
    handoff_transition(HandoffState.AI_ACTIVE, HandoffState.HANDOFF_REQUESTED)
    handoff_transition(
        HandoffState.HANDOFF_REQUESTED, HandoffState.WAITING_HUMAN,
    )
    from datetime import datetime, timezone as _tz

    _now = datetime.now(_tz.utc).isoformat()
    handoff_data = {
        "handoff_state": HandoffState.WAITING_HUMAN.value,
        "trigger_type": "complaint_escalation",
        "trigger_reason": f"投诉升级: severity={detection.severity}",
        "ticket_id": ticket.ticket_id,
        "created_at": _now,
        "updated_at": _now,
    }
    from backend.customer_service.handoff_store import get_handoff_store

    store = get_handoff_store()
    store.save(user_id, session_id, handoff_data)
    record_cs_handoff("complaint")

    # 实时推送：投诉工单进入坐席待接入队列（与显式转人工一致）
    from backend.customer_service.realtime import get_agent_hub

    get_agent_hub().publish(
        "conversation.waiting",
        item={
            "conversation_id": session_id,
            "user_id": user_id,
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "trigger_type": "complaint_escalation",
            "trigger_reason": handoff_data["trigger_reason"],
            "updated_at": _now,
            "last_message_preview": (user_message[:80] if user_message else None),
        },
    )

    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type="complaint_ticket_created",
        result="success",
        target_type="complaint",
        target_id=ticket.ticket_id,
        detail=f"severity={detection.severity}, handoff triggered",
        conversation_id=conversation_id,
    )

    logger.info(
        "[ComplaintExpert] ticket=%s severity=%s handoff=WAITING_HUMAN",
        ticket.ticket_id, detection.severity,
    )

    return ExpertResult(
        expert="complaint",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={
            "ticket_id": ticket.ticket_id,
            "severity": detection.severity,
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "handling_mode": "human",
            "audit_entry": audit_entry,
        },
    )


def complaint_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph ComplaintExpert 节点函数。"""
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")

    result = run_expert_safely(
        expert_name="complaint",
        fn=lambda _state: execute_complaint(user_message, state),
        state=state,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "complaint",
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
    # 记录已建工单：供 complaint 专家幂等防重入（见 execute_complaint）
    if result.get("data", {}).get("ticket_id") and not result.get("data", {}).get("duplicate"):
        cs_context["complaint_ticket_id"] = result["data"]["ticket_id"]
        cs_context["complaint_severity"] = result["data"].get("severity", "medium")

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
        "cs_audit_entries": audit_entries,
        "cs_context": cs_context,
    }
