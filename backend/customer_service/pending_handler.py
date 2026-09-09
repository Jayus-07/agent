"""customer_service/pending_handler.py — CS Pending Handler 节点

Phase 5: 多轮确认流程处理器。
位于 cs_state_loader 与 cs_supervisor 之间，拦截有 pending_action 的场景，
直接处理用户确认/取消/超时，无需经过 Supervisor → ActionExpert 链路。

拓扑:
  START → cs_state_loader → cs_pending_handler
    → (无 pending) → cs_supervisor
    → (有 pending)  → 处理确认/取消/超时 → cs_reporter

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.3
"""
from __future__ import annotations

from typing import Any

from langgraph.types import Command

from backend.shared.logger import logger

_PENDING_STATES = frozenset({"pending", "pending_confirmation"})


def cs_pending_handler_node(state: dict[str, Any]) -> Command:
    """CS Pending Handler 节点 — 多轮确认流程入口。

    三种结果:
    1. 无 pending_action → 直通 cs_supervisor
    2. pending 已过期 → 清理 + 超时回复 → cs_reporter
    3. pending 有效 → 检测用户意图 → 执行/取消/追问 → cs_reporter
    """
    pending_action = state.get("pending_action")
    if not pending_action:
        return Command(goto="cs_supervisor", update={})

    confirmation_state = state.get("confirmation_state", "")
    if confirmation_state not in _PENDING_STATES:
        return Command(goto="cs_supervisor", update={})

    user_id = state.get("user_id", "")
    session_id = state.get("session_id", "")
    user_message = state.get("user_message", "")

    logger.info(
        "[CS PendingHandler] pending_action found: type=%s user=%s",
        pending_action.get("action_type", "unknown"), user_id,
    )

    return _process_pending(
        pending_action, user_message, user_id, session_id, state,
    )


def _process_pending(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
    state: dict[str, Any],
) -> Command:
    """处理 pending action — 过期检查 → 意图检测 → 状态流转。"""
    from backend.customer_service.confirmation import (
        ConfirmationIntent,
        detect_confirmation_intent,
        is_expired,
    )

    if is_expired(pending_action):
        return _handle_expired(
            pending_action, user_id, session_id,
        )

    intent = detect_confirmation_intent(user_message)

    if intent == ConfirmationIntent.CONFIRM:
        return _handle_confirm(
            pending_action, user_id, session_id, state,
        )

    if intent == ConfirmationIntent.CANCEL:
        return _handle_cancel(
            pending_action, user_id, session_id,
        )

    proposal_text = pending_action.get("proposal_text", "")
    return Command(
        goto="cs_reporter",
        update={
            "supervisor_decision": {
                "next_action": "pending",
                "decision_layer": 2,
                "reason": "pending_handler — 用户意图不明确，重新追问",
            },
            "last_expert_result": {
                "response_draft": f"您有一个待确认的操作：\n\n{proposal_text}\n\n请回复「确认」继续，或「取消」放弃。",
            },
        },
    )


def _handle_expired(
    pending_action: dict,
    user_id: str,
    session_id: str,
) -> Command:
    """pending 已超时 → 清理 store → 返回超时回复。"""
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation import (
        ConfirmationState,
        transition,
    )
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.observability.metrics import record_cs_confirmation

    transition(
        ConfirmationState.PENDING_CONFIRMATION,
        ConfirmationState.EXPIRED,
    )
    get_confirmation_store().clear(user_id, session_id)
    record_cs_confirmation("expired")

    action_type = pending_action.get("action_type", "unknown")
    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type=action_type,
        result="denied",
        detail="confirmation expired",
    )

    logger.info("[CS PendingHandler] expired: type=%s user=%s", action_type, user_id)

    return Command(
        goto="cs_reporter",
        update={
            "supervisor_decision": {
                "next_action": "finish",
                "decision_layer": 2,
                "reason": "pending_handler — 确认超时",
                "is_finished": True,
            },
            "last_expert_result": {
                "response_draft": "操作确认已超时，请重新发起。",
            },
            "confirmation_state": ConfirmationState.EXPIRED.value,
            "cs_audit_entries": [audit_entry],
        },
    )


def _handle_confirm(
    pending_action: dict,
    user_id: str,
    session_id: str,
    state: dict[str, Any],
) -> Command:
    """用户确认 → 状态流转 PENDING→CONFIRMED→EXECUTING → 执行 → SUCCESS/FAILED。"""
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation import (
        ConfirmationState,
        transition,
    )
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.customer_service.experts.action import (
        _ACTION_TYPE_LABELS,
        _simulate_execute,
    )
    from backend.observability.metrics import record_cs_action, record_cs_confirmation

    transition(
        ConfirmationState.PENDING_CONFIRMATION,
        ConfirmationState.USER_CONFIRMED,
    )
    transition(ConfirmationState.USER_CONFIRMED, ConfirmationState.EXECUTING)
    record_cs_confirmation("confirmed")

    action_type = pending_action.get("action_type", "unknown")

    try:
        record = _simulate_execute(pending_action)
        transition(ConfirmationState.EXECUTING, ConfirmationState.SUCCESS)
        get_confirmation_store().clear(user_id, session_id)
        record_cs_action(action_type, "success")

        action_result = {
            "action_type": action_type,
            "status": "success",
            "action_record": record.to_dict(),
        }

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="success",
            target_type=pending_action.get("target_type", ""),
            target_id=pending_action.get("target_id", ""),
            detail=f"simulated execution, action_id={record.action_id}",
        )

        label = _ACTION_TYPE_LABELS.get(action_type, action_type)
        answer = (
            f"✅ 操作已提交成功！\n\n"
            f"**操作类型:** {label}\n"
            f"*（当前为模拟模式，实际写操作将在 Phase 6 启用）*"
        )

        logger.info("[CS PendingHandler] confirmed+success: type=%s", action_type)

        return Command(
            goto="cs_reporter",
            update={
                "supervisor_decision": {
                    "next_action": "finish",
                    "decision_layer": 2,
                    "reason": "pending_handler — 用户确认，执行成功",
                    "is_finished": True,
                },
                "last_expert_result": {"response_draft": answer},
                "confirmation_state": ConfirmationState.SUCCESS.value,
                "cs_action_result": action_result,
                "cs_audit_entries": [audit_entry],
            },
        )

    except Exception as e:
        transition(ConfirmationState.EXECUTING, ConfirmationState.FAILED)
        get_confirmation_store().clear(user_id, session_id)
        record_cs_action(action_type, "failed")

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="failure",
            detail=str(e),
        )

        logger.error("[CS PendingHandler] execution failed: %s", e, exc_info=True)

        return Command(
            goto="cs_reporter",
            update={
                "supervisor_decision": {
                    "next_action": "finish",
                    "decision_layer": 2,
                    "reason": "pending_handler — 用户确认，执行失败",
                    "is_finished": True,
                },
                "last_expert_result": {
                    "response_draft": "操作执行失败，请稍后重试或联系人工客服。",
                },
                "confirmation_state": ConfirmationState.FAILED.value,
                "cs_audit_entries": [audit_entry],
            },
        )


def _handle_cancel(
    pending_action: dict,
    user_id: str,
    session_id: str,
) -> Command:
    """用户取消 → 清理 store → 返回取消回复。"""
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation import (
        ConfirmationState,
        transition,
    )
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.observability.metrics import record_cs_confirmation

    transition(
        ConfirmationState.PENDING_CONFIRMATION,
        ConfirmationState.USER_CANCELLED,
    )
    get_confirmation_store().clear(user_id, session_id)
    record_cs_confirmation("cancelled")

    action_type = pending_action.get("action_type", "unknown")
    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type=action_type,
        result="denied",
        detail="user cancelled",
    )

    logger.info("[CS PendingHandler] cancelled: type=%s user=%s", action_type, user_id)

    return Command(
        goto="cs_reporter",
        update={
            "supervisor_decision": {
                "next_action": "finish",
                "decision_layer": 2,
                "reason": "pending_handler — 用户取消",
                "is_finished": True,
            },
            "last_expert_result": {
                "response_draft": "操作已取消。如有其他问题，请随时咨询。",
            },
            "confirmation_state": ConfirmationState.USER_CANCELLED.value,
            "cs_audit_entries": [audit_entry],
        },
    )
