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
    from backend.customer_service.handoff import HandoffState
    from backend.customer_service.handoff_store import get_handoff_store
    from backend.customer_service.service.complaint_service import get_complaint_service
    from backend.observability.metrics import record_cs_handoff

    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id", "default")
    conversation_id = state.get("conversation_id", "")

    logger.info(
        "[ComplaintExpert] user_id=%s question=%s...",
        user_id, user_message[:60],
    )

    service = get_complaint_service()
    detection = service.detect(user_message)

    ticket = service.create_ticket(
        user_id=user_id,
        conversation_id=conversation_id,
        severity=detection.severity,
        summary=user_message,
    )
    service.simulate_execute(ticket)

    answer = service.build_comfort_response(detection, ticket)

    handoff_data = {
        "handoff_state": HandoffState.HANDOFF_REQUESTED.value,
        "trigger_type": "complaint_escalation",
        "trigger_reason": f"投诉升级: severity={detection.severity}",
        "ticket_id": ticket.ticket_id,
    }
    store = get_handoff_store()
    store.save(user_id, session_id, handoff_data)
    record_cs_handoff("complaint")

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
        "[ComplaintExpert] ticket=%s severity=%s handoff=HANDOFF_REQUESTED",
        ticket.ticket_id, detection.severity,
    )

    return ExpertResult(
        expert="complaint",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={
            "ticket_id": ticket.ticket_id,
            "severity": detection.severity,
            "handoff_state": HandoffState.HANDOFF_REQUESTED.value,
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

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
        "cs_audit_entries": audit_entries,
        "cs_context": cs_context,
    }
