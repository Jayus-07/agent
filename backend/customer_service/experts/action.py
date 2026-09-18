"""customer_service/experts/action.py — ActionExpert

业务操作 Expert：退款、退货、换货、地址修改等写操作 + 确认状态机。
从 graph/nodes.py cs_business_action 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.3
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.errors import (
    OrderNotEligibleError,
    OrderNotFoundError,
)
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

    session_id = state.get("session_id", "default")
    intent = cs_route.get("intent", "as_refund")

    record_cs_intent(intent)

    # 身份校验先行（权威 user_id），再打日志（P1 修正：消除死赋值）
    user_id = PermissionChecker.validate_user_identity(state)
    logger.info(
        "[ActionExpert] intent=%s user_id=%s question=%s...",
        intent, user_id, user_message[:60],
    )

    store = get_confirmation_store()

    pending_action = state.get("pending_action") or store.load(user_id, session_id)

    if pending_action:
        return _handle_pending_confirmation(
            pending_action, user_message, user_id, session_id,
        )

    try:
        return _build_new_proposal(user_id, intent, cs_route, session_id, store, user_message)
    except OrderNotEligibleError as e:
        # 业务规则拒绝（P0 实测修复 2026-09-19）：资格不满足是正常业务结论，
        # 必须向用户给出可读原因与下一步，而不是当作专家异常降级为通用报错。
        logger.info("[ActionExpert] proposal declined: %s", e)
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                f"{e}\n\n可以告诉我订单号让我重新核对，"
                "或回复「查我的所有订单」查看各订单当前状态。"
            ),
            data={},
        )
    except OrderNotFoundError:
        # 缺槽位兜底 "latest" 也可能无订单可用（新用户/无演示数据）。
        logger.info("[ActionExpert] order not found for proposal")
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "暂时没有找到可用于该申请的订单。请告诉我订单号"
                "（例如 DEMO-1002），或回复「查我的所有订单」先查看订单。"
            ),
            data={},
        )


def action_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph ActionExpert 节点函数。"""
    from backend.config.customer_service import CS_EXPERT_TIMEOUT_S
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")
    cs_route = state.get("cs_route", {})

    result = run_expert_safely(
        expert_name="action",
        fn=lambda _state: execute_action(user_message, cs_route, state),
        state=state,
        timeout_s=CS_EXPERT_TIMEOUT_S,
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
    user_message: str = "",
) -> ExpertResult:
    """构建新 proposal 并保存到 confirmation store。"""
    from backend.customer_service.action import build_pending_action
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.confirmation import ConfirmationState
    from backend.customer_service.risk import RiskLevel, requires_human_review
    from backend.observability.metrics import record_cs_confirmation

    proposal = _build_proposal(user_id, intent, cs_route, user_message)
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
) -> ExpertResult:
    """处理用户对 pending action 的确认/取消响应。

    P1 重构：状态流转/执行/审计全部委托 confirmation_flow.process_confirmation
    （与 pending_handler 共用唯一实现，含原子认领幂等闸门）——
    此处整段重复实现已删除（audit-report §P0-2）。
    """
    from backend.customer_service.confirmation_flow import process_confirmation

    outcome = process_confirmation(
        pending_action, user_message, user_id, session_id,
    )

    data: dict[str, Any] = {
        "confirmation_state": outcome.confirmation_state,
    }
    if outcome.pending_action is not None:
        data["pending_action"] = outcome.pending_action
    if outcome.action_result is not None:
        data["action_result"] = outcome.action_result
    if outcome.audit_entry is not None:
        data["audit_entry"] = outcome.audit_entry

    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=outcome.answer,
        data=data,
    )


def _build_proposal(
    user_id: str, intent: str, cs_route: dict, user_message: str = ""
) -> Any:
    """Dispatch to the appropriate service to build an ActionProposal。"""
    action_type = _INTENT_ACTION_MAP.get(intent, "refund")

    if action_type == "refund":
        from backend.customer_service.service.refund_service import get_refund_service
        order_id = cs_route.get("metadata", {}).get("order_id", "")
        reason = cs_route.get("metadata", {}).get("reason", "")
        if not order_id:
            order_id = _extract_order_id_from_message(user_message)
        return get_refund_service().build_refund_proposal(user_id, order_id, reason)

    if action_type == "return":
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        order_id = cs_route.get("metadata", {}).get("order_id", "")
        reason = cs_route.get("metadata", {}).get("reason", "")
        if not order_id:
            order_id = _extract_order_id_from_message(user_message)
        return get_after_sales_service().build_return_proposal(user_id, order_id, reason)

    if action_type == "exchange":
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        order_id = cs_route.get("metadata", {}).get("order_id", "")
        reason = cs_route.get("metadata", {}).get("reason", "")
        if not order_id:
            order_id = _extract_order_id_from_message(user_message)
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


def _extract_order_id_from_message(user_message: str) -> str:
    """订单号提取（P1 收敛：委托 understanding.entities 单一事实源）。

    understanding 层在规范化文本上抽取（NFKC/零宽剥离复用 Input Guard
    事实源），支持字母数字混合段（两段式、形近错别字原样认领）与关键词
    纯数字形态；识别不到时回退 "latest"（由服务端 _get_order 语义化
    处理为最近一单）。
    """
    from backend.customer_service.understanding.entities import extract_entities
    from backend.customer_service.understanding.types import EntityType
    from backend.security.input_guard.normalize import normalize_query

    for e in extract_entities(normalize_query(user_message or "")):
        if e.type == EntityType.ORDER_ID:
            return e.match()
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
