"""customer_service/experts/action.py — ActionExpert

业务操作 Expert：退款、退货、换货、地址修改等写操作 + 确认状态机。
从 graph/nodes.py cs_business_action 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.3
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger

_INTENT_ACTION_MAP = {
    "as_refund":   "refund",
    "as_return":   "return",
    "as_exchange": "exchange",
    "a_address":   "address",
    "a_password":  "password",
}

_ACTION_TYPE_LABELS = {
    "refund_request": "退款申请",
    "return_request": "退货申请",
    "exchange_request": "换货申请",
    "address_update": "地址修改",
    "password_reset": "密码重置",
}


def execute_action(
    user_message: str,
    cs_route: dict,
    state: dict[str, Any],
) -> ExpertResult:
    """ActionExpert 核心逻辑。

    两条路径:
    1. 有 pending_action → 处理用户确认/取消
    2. 无 pending_action → 构建新 proposal

    Args:
        user_message: 用户原始问题
        cs_route: CS Router 输出（含 intent + metadata）
        state: CSGraphState

    Returns:
        ExpertResult — response_draft + action_result / data
    """
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.customer_service.security.permission import PermissionChecker
    from backend.observability.metrics import record_cs_intent

    user_id = state.get("user_id", "")
    session_id = state.get("session_id", "default")
    intent = cs_route.get("intent", "as_refund")

    record_cs_intent(intent)

    logger.info(
        "[ActionExpert] intent=%s user_id=%s question=%s...",
        intent, user_id, user_message[:60],
    )

    user_id = PermissionChecker.validate_user_identity(state)
    store = get_confirmation_store()

    pending_action = state.get("pending_action") or store.load(user_id, session_id)

    if pending_action:
        return _handle_pending_confirmation(
            pending_action, user_message, user_id, session_id, store,
        )

    return _build_new_proposal(user_id, intent, cs_route, session_id, store)


def action_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph ActionExpert 节点函数。"""
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")
    cs_route = state.get("cs_route", {})

    result = run_expert_safely(
        expert_name="action",
        fn=lambda _state: execute_action(user_message, cs_route, state),
        state=state,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "action",
        "status": result.get("status", "failed"),
        "duration_ms": result.get("duration_ms", 0),
    })

    audit_entries = list(state.get("cs_audit_entries", []))
    if result.get("status") == "success" and result.get("data", {}).get("audit_entry"):
        from backend.customer_service.audit import append_audit
        audit_entries = append_audit(audit_entries, result["data"]["audit_entry"])

    cs_context = dict(state.get("cs_context", {}))
    data = result.get("data", {})
    if "pending_action" in data:
        cs_context["pending_action"] = data["pending_action"]
    if "confirmation_state" in data:
        cs_context["confirmation_state"] = data["confirmation_state"]
    if "action_result" in data:
        cs_context["action_result"] = data["action_result"]

    update: dict[str, Any] = {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
        "cs_audit_entries": audit_entries,
        "cs_context": cs_context,
    }
    if "action_result" in data:
        update["cs_action_result"] = data["action_result"]

    return update


def _build_new_proposal(
    user_id: str,
    intent: str,
    cs_route: dict,
    session_id: str,
    store: Any,
) -> ExpertResult:
    """构建新 proposal 并保存到 confirmation store。"""
    from backend.customer_service.action import build_pending_action
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation import ConfirmationState
    from backend.customer_service.risk import RiskLevel, requires_human_review
    from backend.observability.metrics import record_cs_confirmation

    proposal = _build_proposal(user_id, intent, cs_route)
    pending = build_pending_action(proposal)

    store.save(user_id, session_id, pending)

    record_cs_confirmation("initiated")

    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type=proposal.action_type,
        result="pending",
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        detail="proposal built, awaiting confirmation",
    )

    risk = RiskLevel(proposal.risk_level.value)
    answer = proposal.proposal_text
    if requires_human_review(risk):
        answer += "\n\n*⚠️ 此操作需要人工审核，提交后将由客服主管处理。*"

    logger.info(
        "[ActionExpert] proposal built: type=%s risk=%s user_id=%s",
        proposal.action_type, risk.value, user_id,
    )

    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={
            "pending_action": pending,
            "confirmation_state": ConfirmationState.PENDING_CONFIRMATION.value,
            "audit_entry": audit_entry,
        },
    )


def _handle_pending_confirmation(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
    store: Any,
) -> ExpertResult:
    """处理用户对 pending action 的确认/取消响应。"""
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation import (
        ConfirmationIntent,
        ConfirmationState,
        detect_confirmation_intent,
        is_expired,
        transition,
    )
    from backend.observability.metrics import record_cs_action, record_cs_confirmation

    action_type = pending_action.get("action_type", "unknown")
    proposal_text = pending_action.get("proposal_text", "")

    if is_expired(pending_action):
        transition(ConfirmationState.PENDING_CONFIRMATION, ConfirmationState.EXPIRED)
        store.clear(user_id, session_id)
        record_cs_confirmation("expired")

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="denied",
            detail="confirmation expired",
        )

        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft="操作确认已超时，请重新发起。",
            data={
                "confirmation_state": ConfirmationState.EXPIRED.value,
                "audit_entry": audit_entry,
            },
        )

    user_intent = detect_confirmation_intent(user_message)

    if user_intent == ConfirmationIntent.CANCEL:
        transition(
            ConfirmationState.PENDING_CONFIRMATION,
            ConfirmationState.USER_CANCELLED,
        )
        store.clear(user_id, session_id)
        record_cs_confirmation("cancelled")

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="denied",
            detail="user cancelled",
        )

        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft="操作已取消。如有其他问题，请随时咨询。",
            data={
                "confirmation_state": ConfirmationState.USER_CANCELLED.value,
                "audit_entry": audit_entry,
            },
        )

    if user_intent == ConfirmationIntent.CONFIRM:
        transition(
            ConfirmationState.PENDING_CONFIRMATION,
            ConfirmationState.USER_CONFIRMED,
        )
        transition(ConfirmationState.USER_CONFIRMED, ConfirmationState.EXECUTING)
        record_cs_confirmation("confirmed")

        try:
            record = _simulate_execute(pending_action)
            transition(ConfirmationState.EXECUTING, ConfirmationState.SUCCESS)
            store.clear(user_id, session_id)
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

            answer = (
                f"✅ 操作已提交成功！\n\n"
                f"**操作类型:** {_action_type_label(action_type)}\n"
                f"*（当前为模拟模式，实际写操作将在 Phase 6 启用）*"
            )

            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=answer,
                data={
                    "confirmation_state": ConfirmationState.SUCCESS.value,
                    "action_result": action_result,
                    "audit_entry": audit_entry,
                },
            )

        except Exception as e:
            transition(ConfirmationState.EXECUTING, ConfirmationState.FAILED)
            store.clear(user_id, session_id)
            record_cs_action(action_type, "failed")

            audit_entry = build_audit_entry(
                user_id=user_id,
                action_type=action_type,
                result="failure",
                detail=str(e),
            )

            logger.error("[ActionExpert] 执行失败: %s", e, exc_info=True)

            return ExpertResult(
                expert="action",
                status=ExpertStatus.FAILED.value,
                response_draft="操作执行失败，请稍后重试或联系人工客服。",
                data={
                    "confirmation_state": ConfirmationState.FAILED.value,
                    "audit_entry": audit_entry,
                },
            )

    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=f"您有一个待确认的操作：\n\n{proposal_text}",
        data={
            "pending_action": pending_action,
            "confirmation_state": ConfirmationState.PENDING_CONFIRMATION.value,
        },
    )


def _build_proposal(user_id: str, intent: str, cs_route: dict) -> Any:
    """Dispatch to the appropriate service to build an ActionProposal。"""
    action_type = _INTENT_ACTION_MAP.get(intent, "refund")

    if action_type == "refund":
        from backend.customer_service.service.refund_service import get_refund_service
        order_id = cs_route.get("metadata", {}).get("order_id", "")
        reason = cs_route.get("metadata", {}).get("reason", "")
        if not order_id:
            order_id = _extract_order_id_from_context(cs_route)
        return get_refund_service().build_refund_proposal(user_id, order_id, reason)

    if action_type == "return":
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        order_id = cs_route.get("metadata", {}).get("order_id", "")
        reason = cs_route.get("metadata", {}).get("reason", "")
        if not order_id:
            order_id = _extract_order_id_from_context(cs_route)
        return get_after_sales_service().build_return_proposal(user_id, order_id, reason)

    if action_type == "exchange":
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        order_id = cs_route.get("metadata", {}).get("order_id", "")
        reason = cs_route.get("metadata", {}).get("reason", "")
        if not order_id:
            order_id = _extract_order_id_from_context(cs_route)
        return get_after_sales_service().build_exchange_proposal(user_id, order_id, reason)

    if action_type == "address":
        from backend.customer_service.service.account_action_service import get_account_action_service
        new_address = cs_route.get("metadata", {}).get("new_address", "")
        return get_account_action_service().build_address_update_proposal(
            user_id, user_id, new_address,
        )

    if action_type == "password":
        from backend.customer_service.service.account_action_service import get_account_action_service
        return get_account_action_service().build_password_reset_proposal(user_id)

    from backend.customer_service.errors import ValidationError
    raise ValidationError(f"不支持的操作类型: {intent}")


def _extract_order_id_from_context(cs_route: dict) -> str:
    """Try to extract an order_id from the route metadata。"""
    metadata = cs_route.get("metadata", {})
    order_id = metadata.get("order_id", "")
    if order_id:
        return order_id
    return "latest"


def _simulate_execute(pending_action: dict) -> Any:
    """Dispatch simulate_execute based on action_type。"""
    action_type = pending_action.get("action_type", "")

    if action_type in ("refund_request",):
        from backend.customer_service.action import ActionProposal
        from backend.customer_service.risk import RiskLevel
        from backend.customer_service.service.refund_service import get_refund_service
        proposal = ActionProposal(
            action_type=action_type,
            target_type=pending_action.get("target_type", "order"),
            target_id=pending_action.get("target_id", ""),
            risk_level=RiskLevel(pending_action.get("risk_level", "high")),
            proposal_text=pending_action.get("proposal_text", ""),
            before_state=pending_action.get("before_state", {}),
            after_state=pending_action.get("after_state", {}),
        )
        return get_refund_service().simulate_execute(proposal)

    if action_type in ("return_request", "exchange_request"):
        from backend.customer_service.action import ActionProposal
        from backend.customer_service.risk import RiskLevel
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        proposal = ActionProposal(
            action_type=action_type,
            target_type=pending_action.get("target_type", "order"),
            target_id=pending_action.get("target_id", ""),
            risk_level=RiskLevel(pending_action.get("risk_level", "medium")),
            proposal_text=pending_action.get("proposal_text", ""),
            before_state=pending_action.get("before_state", {}),
            after_state=pending_action.get("after_state", {}),
        )
        return get_after_sales_service().simulate_execute(proposal)

    if action_type in ("address_update", "password_reset"):
        from backend.customer_service.action import ActionProposal
        from backend.customer_service.risk import RiskLevel
        from backend.customer_service.service.account_action_service import get_account_action_service
        proposal = ActionProposal(
            action_type=action_type,
            target_type=pending_action.get("target_type", "account"),
            target_id=pending_action.get("target_id", ""),
            risk_level=RiskLevel(pending_action.get("risk_level", "medium")),
            proposal_text=pending_action.get("proposal_text", ""),
            before_state=pending_action.get("before_state", {}),
            after_state=pending_action.get("after_state", {}),
        )
        return get_account_action_service().simulate_execute(proposal)

    from backend.customer_service.errors import ActionExecutionError
    raise ActionExecutionError(f"Unknown action type: {action_type}")


def _action_type_label(action_type: str) -> str:
    return _ACTION_TYPE_LABELS.get(action_type, action_type)
