"""customer_service/graph/nodes.py — 客服 LangGraph 节点

cs_knowledge_node:    Phase 2 — 知识问答（RAG）
cs_pending_node:      占位节点（低置信度路径）
cs_business_query:    Phase 3 — 只读业务查询（订单、物流）
cs_business_action:   Phase 4 — 业务操作 + 确认状态机（退款、退货、换货、地址修改）
cs_complaint:         Phase 5 — 投诉处理（检测→安抚→工单→转接）
cs_handoff:           Phase 5 — 人工转接（触发→状态转换→工单）
cs_handoff_intercept: Phase 5 — 转接拦截（非 AI_ACTIVE 时拦截业务请求）
"""
from __future__ import annotations

from backend.shared.logger import logger


def cs_knowledge_node(state: dict) -> dict:
    """客服知识问答节点 — 调用 CSKnowledgeService 执行 RAG 问答。

    输入: state.question, state.cs_context.cs_route
    输出: final_answer, cs_context (含 answer_meta)
    """
    question = state.get("question", "")
    cs_context = dict(state.get("cs_context", {}))
    cs_route = cs_context.get("cs_route", {})

    kb_ids = cs_route.get("kb_ids", [])
    intent = cs_route.get("intent", "k_faq")
    session_id = "default"

    logger.info(
        f"[CSKnowledgeNode] intent={intent} kb_ids={kb_ids} "
        f"question={question[:60]}..."
    )

    from backend.observability.metrics import record_cs_intent, record_cs_rag_status
    record_cs_intent(intent)

    try:
        from backend.customer_service.knowledge import get_knowledge_service

        service = get_knowledge_service()
        result = service.answer(
            question=question,
            kb_ids=kb_ids if kb_ids else None,
            session_id=session_id,
        )

        cs_context["answer_meta"] = {
            "decision": result.decision.value,
            "confidence": result.confidence,
            "kb_ids": result.kb_ids,
        }

        record_cs_rag_status("hit" if result.answer else "miss")

        logger.info(
            f"[CSKnowledgeNode] decision={result.decision.value} "
            f"conf={result.confidence:.2f} answer_len={len(result.answer)}"
        )

        return {
            "final_answer": result.answer,
            "cs_context": cs_context,
        }

    except Exception as e:
        record_cs_rag_status("rejected")
        logger.error(f"[CSKnowledgeNode] 执行异常: {e}", exc_info=True)
        return {
            "final_answer": "系统繁忙，请稍后重试或联系人工客服。",
            "cs_context": cs_context,
        }


def cs_pending_node(state: dict) -> dict:
    """客服待处理节点 — Phase 4-5 的占位节点。

    非知识/非业务查询路径（business_action / complaint_flow / human_handoff）
    暂时由此节点承接，输出提示信息。
    """
    cs_context = dict(state.get("cs_context", {}))
    cs_route = cs_context.get("cs_route", {})
    route_path = cs_route.get("route_path", "knowledge_query")
    intent = cs_route.get("intent", "unknown")

    logger.info(
        f"[CSPendingNode] route_path={route_path} intent={intent} "
        f"(Phase 4-5 stub)"
    )

    placeholder = (
        "## 客服助手\n\n"
        f"您的问题已分类为 **{intent}**，相关功能正在建设中。\n\n"
        "当前可用功能：知识问答（FAQ、政策、产品、售后流程等）、"
        "业务查询（订单、物流、账户信息）。\n"
        "如需人工服务，请输入「转人工」。"
    )

    return {
        "final_answer": placeholder,
        "cs_context": cs_context,
    }


_INTENT_SERVICE_MAP = {
    "t_order_status": "order",
    "t_logistics": "logistics",
    "as_repair": "order",
    "as_quality_issue": "order",
}


def cs_business_query(state: dict) -> dict:
    """客服业务查询节点 — Phase 3 实现。

    流程: 身份验证 → 按 intent 分发到 Service → Output Guard → 返回结果。
    输入: state.question, state.cs_context (含 cs_route + authenticated_user_id)
    输出: final_answer, cs_context
    """
    cs_context = dict(state.get("cs_context", {}))
    cs_route = cs_context.get("cs_route", {})
    intent = cs_route.get("intent", "t_order_status")
    question = state.get("question", "")

    logger.info(
        f"[CSBusinessQuery] intent={intent} "
        f"question={question[:60]}..."
    )

    from backend.observability.metrics import record_cs_intent, record_cs_permission_violation
    record_cs_intent(intent)

    try:
        from backend.customer_service.security.output_guard import get_output_guard
        from backend.customer_service.security.permission import PermissionChecker

        user_id = PermissionChecker.validate_user_identity(state)

        answer = _dispatch_service(user_id, intent, question, cs_route)

        guard = get_output_guard()
        guard_result = guard.check(answer, cs_context)
        final_answer = guard_result.text

        cs_context["query_meta"] = {
            "intent": intent,
            "user_id": user_id,
            "filtered": guard_result.filtered,
        }

        logger.info(
            f"[CSBusinessQuery] intent={intent} user_id={user_id} "
            f"answer_len={len(final_answer)} filtered={guard_result.filtered}"
        )

        return {
            "final_answer": final_answer,
            "cs_context": cs_context,
        }

    except Exception as e:
        from backend.customer_service.errors import (
            AuthenticationError,
            OrderNotFoundError,
            ValidationError,
        )
        if isinstance(e, AuthenticationError):
            record_cs_permission_violation(intent)
            answer = "请先登录后再查询订单信息。"
        elif isinstance(e, OrderNotFoundError):
            answer = "未找到相关订单信息，请确认订单号是否正确。"
        elif isinstance(e, ValidationError):
            answer = "输入信息有误，请检查后重试。"
        else:
            logger.error(f"[CSBusinessQuery] 执行异常: {e}", exc_info=True)
            answer = "系统繁忙，请稍后重试或联系人工客服。"

        return {
            "final_answer": answer,
            "cs_context": cs_context,
        }


def _dispatch_service(
    user_id: str, intent: str, question: str, cs_route: dict
) -> str:
    """根据 intent 分发到对应的 Service。"""
    service_type = _INTENT_SERVICE_MAP.get(intent, "order")

    if service_type == "order":
        from backend.customer_service.service.order_service import get_order_service
        order_service = get_order_service()

        if intent == "t_order_status":
            result = order_service.query_orders(user_id=user_id)
            return _format_order_list(result.orders)
        return _format_order_list([])

    if service_type == "logistics":
        from backend.customer_service.service.logistics_service import get_logistics_service
        logistics = get_logistics_service()
        orders_result = _get_latest_order_id(user_id)
        if orders_result:
            result = logistics.query_logistics(
                user_id=user_id, order_id=orders_result
            )
            return _format_logistics(result)
        return "暂无物流信息。请先查询您的订单。"

    return "该功能正在建设中，请稍后再试。"


def _get_latest_order_id(user_id: str) -> str | None:
    """获取用户最新订单 ID（用于无指定 order_id 的物流查询）。"""
    try:
        from backend.customer_service.service.order_service import get_order_service
        result = get_order_service().query_orders(user_id=user_id)
        if result.orders:
            return str(result.orders[0].get("id") or result.orders[0].get("order_no"))
    except Exception:
        pass
    return None


def _format_order_list(orders: list[dict]) -> str:
    """格式化订单列表为 Markdown。"""
    if not orders:
        return "您当前没有相关订单记录。"

    lines = ["## 您的订单\n"]
    for i, o in enumerate(orders[:10], 1):
        order_no = o.get("order_no", "N/A")
        status = o.get("status", "未知")
        amount = o.get("total_amount", "0")
        created = o.get("created_at", "")
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d")
        else:
            created = str(created)[:10] if created else ""
        lines.append(
            f"**{i}.** 订单号: `{order_no}` | "
            f"状态: {status} | "
            f"金额: ¥{amount} | "
            f"时间: {created}"
        )

    if len(orders) > 10:
        lines.append(f"\n*仅显示最近 10 条，共 {len(orders)} 条订单。*")

    return "\n".join(lines)


def _format_logistics(result) -> str:
    """格式化物流信息为 Markdown。"""
    lines = [
        "## 物流信息\n",
        f"**订单号:** `{result.order_no}`",
        f"**物流状态:** {result.status_display}",
    ]
    if result.tracking_info:
        lines.append(f"\n{result.tracking_info}")
    return "\n".join(lines)


_INTENT_ACTION_MAP = {
    "as_refund":   "refund",
    "as_return":   "return",
    "as_exchange": "exchange",
    "a_address":   "address",
    "a_password":  "password",
}


def cs_business_action(state: dict) -> dict:
    """客服业务操作节点 — Phase 4 实现。

    流程: 身份验证 → 确认门控 → 按 intent 分发到 Service → 风险评估
          → Output Guard → 审计日志 → 返回结果。
    """
    cs_context = dict(state.get("cs_context", {}))
    cs_route = cs_context.get("cs_route", {})
    intent = cs_route.get("intent", "as_refund")
    question = state.get("question", "")
    audit_entries = list(state.get("cs_audit_entries", []))

    logger.info(
        f"[CSBusinessAction] intent={intent} "
        f"question={question[:60]}..."
    )

    from backend.observability.metrics import (
        record_cs_action,
        record_cs_confirmation,
        record_cs_intent,
        record_cs_permission_violation,
    )
    record_cs_intent(intent)

    try:
        from backend.customer_service.audit import append_audit, build_audit_entry
        from backend.customer_service.confirmation import (
            ConfirmationState,
        )
        from backend.customer_service.confirmation_store import get_confirmation_store
        from backend.customer_service.security.output_guard import get_output_guard
        from backend.customer_service.security.permission import PermissionChecker

        user_id = PermissionChecker.validate_user_identity(state)
        session_id = cs_context.get("session_id", "default")
        store = get_confirmation_store()

        pending_action = cs_context.get("pending_action") or store.load(user_id, session_id)

        if pending_action:
            return _handle_pending_confirmation(
                pending_action, question, user_id, session_id,
                store, cs_context, audit_entries,
            )

        proposal = _build_proposal(user_id, intent, cs_route)

        from backend.customer_service.action import build_pending_action
        pending = build_pending_action(proposal)

        store.save(user_id, session_id, pending)
        cs_context["pending_action"] = pending
        cs_context["confirmation_state"] = ConfirmationState.PENDING_CONFIRMATION.value
        record_cs_confirmation("initiated")

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=proposal.action_type,
            result="pending",
            target_type=proposal.target_type,
            target_id=proposal.target_id,
            detail="proposal built, awaiting confirmation",
        )
        audit_entries = append_audit(audit_entries, audit_entry)

        from backend.customer_service.risk import RiskLevel, requires_human_review
        risk = RiskLevel(proposal.risk_level.value)
        answer = proposal.proposal_text
        if requires_human_review(risk):
            answer += "\n\n*⚠️ 此操作需要人工审核，提交后将由客服主管处理。*"

        guard = get_output_guard()
        guard_result = guard.check(answer, cs_context)

        logger.info(
            f"[CSBusinessAction] proposal built: type={proposal.action_type} "
            f"risk={risk.value} user_id={user_id}"
        )

        return {
            "final_answer": guard_result.text,
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }

    except Exception as e:
        from backend.customer_service.errors import (
            ActionExecutionError,
            AuthenticationError,
            OrderNotEligibleError,
            OrderNotFoundError,
            ValidationError,
        )
        if isinstance(e, AuthenticationError):
            record_cs_permission_violation(intent)
            answer = "请先登录后再进行操作。"
        elif isinstance(e, OrderNotFoundError):
            answer = "未找到相关订单信息，请确认订单号是否正确。"
        elif isinstance(e, OrderNotEligibleError):
            answer = e.user_message
        elif isinstance(e, ValidationError):
            answer = "输入信息有误，请检查后重试。"
        elif isinstance(e, ActionExecutionError):
            record_cs_action(intent, "failed")
            answer = "操作执行失败，请稍后重试。"
        else:
            logger.error(f"[CSBusinessAction] 执行异常: {e}", exc_info=True)
            answer = "系统繁忙，请稍后重试或联系人工客服。"

        return {
            "final_answer": answer,
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }


def _handle_pending_confirmation(
    pending_action: dict,
    question: str,
    user_id: str,
    session_id: str,
    store,
    cs_context: dict,
    audit_entries: list[dict],
) -> dict:
    """Handle a user response when there's a pending confirmation."""
    from backend.customer_service.audit import append_audit, build_audit_entry
    from backend.customer_service.confirmation import (
        ConfirmationIntent,
        ConfirmationState,
        detect_confirmation_intent,
        is_expired,
        transition,
    )
    from backend.customer_service.security.output_guard import get_output_guard
    from backend.observability.metrics import record_cs_action, record_cs_confirmation

    action_type = pending_action.get("action_type", "unknown")
    proposal_text = pending_action.get("proposal_text", "")

    if is_expired(pending_action):
        transition(
            ConfirmationState.PENDING_CONFIRMATION,
            ConfirmationState.EXPIRED,
        )
        store.clear(user_id, session_id)
        cs_context.pop("pending_action", None)
        cs_context["confirmation_state"] = ConfirmationState.EXPIRED.value
        record_cs_confirmation("expired")

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="denied",
            detail="confirmation expired",
        )
        audit_entries = append_audit(audit_entries, audit_entry)

        return {
            "final_answer": "操作确认已超时，请重新发起。",
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }

    user_intent = detect_confirmation_intent(question)

    if user_intent == ConfirmationIntent.CANCEL:
        transition(
            ConfirmationState.PENDING_CONFIRMATION,
            ConfirmationState.USER_CANCELLED,
        )
        store.clear(user_id, session_id)
        cs_context.pop("pending_action", None)
        cs_context["confirmation_state"] = ConfirmationState.USER_CANCELLED.value
        record_cs_confirmation("cancelled")

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=action_type,
            result="denied",
            detail="user cancelled",
        )
        audit_entries = append_audit(audit_entries, audit_entry)

        return {
            "final_answer": "操作已取消。如有其他问题，请随时咨询。",
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }

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
            cs_context.pop("pending_action", None)
            cs_context["confirmation_state"] = ConfirmationState.SUCCESS.value
            record_cs_action(action_type, "success")

            cs_context["action_result"] = {
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
            audit_entries = append_audit(audit_entries, audit_entry)

            answer = (
                f"✅ 操作已提交成功！\n\n"
                f"**操作类型:** {_action_type_label(action_type)}\n"
                f"*（当前为模拟模式，实际写操作将在 Phase 6 启用）*"
            )

            guard = get_output_guard()
            guard_result = guard.check(answer, cs_context)

            return {
                "final_answer": guard_result.text,
                "cs_context": cs_context,
                "cs_audit_entries": audit_entries,
            }

        except Exception as e:
            transition(ConfirmationState.EXECUTING, ConfirmationState.FAILED)
            store.clear(user_id, session_id)
            cs_context.pop("pending_action", None)
            cs_context["confirmation_state"] = ConfirmationState.FAILED.value
            record_cs_action(action_type, "failed")

            audit_entry = build_audit_entry(
                user_id=user_id,
                action_type=action_type,
                result="failure",
                detail=str(e),
            )
            audit_entries = append_audit(audit_entries, audit_entry)

            logger.error(f"[CSBusinessAction] 执行失败: {e}", exc_info=True)
            return {
                "final_answer": "操作执行失败，请稍后重试或联系人工客服。",
                "cs_context": cs_context,
                "cs_audit_entries": audit_entries,
            }

    answer = (
        f"您有一个待确认的操作：\n\n{proposal_text}"
    )
    guard = get_output_guard()
    guard_result = guard.check(answer, cs_context)

    return {
        "final_answer": guard_result.text,
        "cs_context": cs_context,
        "cs_audit_entries": audit_entries,
    }


def _build_proposal(user_id: str, intent: str, cs_route: dict):
    """Dispatch to the appropriate service to build an ActionProposal."""
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
            user_id, user_id, new_address
        )

    if action_type == "password":
        from backend.customer_service.service.account_action_service import get_account_action_service
        return get_account_action_service().build_password_reset_proposal(user_id)

    from backend.customer_service.errors import ValidationError
    raise ValidationError(f"不支持的操作类型: {intent}")


def _extract_order_id_from_context(cs_route: dict) -> str:
    """Try to extract an order_id from the question or route metadata."""
    metadata = cs_route.get("metadata", {})
    order_id = metadata.get("order_id", "")
    if order_id:
        return order_id
    return "latest"


def _simulate_execute(pending_action: dict):
    """Dispatch simulate_execute based on action_type."""
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
    labels = {
        "refund_request": "退款申请",
        "return_request": "退货申请",
        "exchange_request": "换货申请",
        "address_update": "地址修改",
        "password_reset": "密码重置",
    }
    return labels.get(action_type, action_type)


def cs_complaint(state: dict) -> dict:
    """客服投诉处理节点 — Phase 5 实现。

    流程: 投诉检测 → 创建工单 → 安抚响应 → 触发转接 → Output Guard → 审计日志。
    """
    cs_context = dict(state.get("cs_context", {}))
    question = state.get("question", "")
    audit_entries = list(state.get("cs_audit_entries", []))

    logger.info(f"[CSComplaint] question={question[:60]}...")

    from backend.observability.metrics import record_cs_handoff, record_cs_intent
    record_cs_intent("complaint")

    try:
        from backend.customer_service.audit import append_audit, build_audit_entry
        from backend.customer_service.handoff import HandoffState
        from backend.customer_service.handoff_store import get_handoff_store
        from backend.customer_service.security.output_guard import get_output_guard
        from backend.customer_service.service.complaint_service import get_complaint_service

        service = get_complaint_service()
        detection = service.detect(question)

        user_id = cs_context.get("authenticated_user_id", "anonymous")
        conversation_id = cs_context.get("conversation_id", "")
        session_id = cs_context.get("session_id", "default")

        ticket = service.create_ticket(
            user_id=user_id,
            conversation_id=conversation_id,
            severity=detection.severity,
            summary=question,
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
        cs_context["handoff_state"] = HandoffState.HANDOFF_REQUESTED.value
        record_cs_handoff("complaint")

        guard = get_output_guard()
        guard_result = guard.check(answer, cs_context)

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type="complaint_ticket_created",
            result="success",
            target_type="complaint",
            target_id=ticket.ticket_id,
            detail=f"severity={detection.severity}, handoff triggered",
            conversation_id=conversation_id,
        )
        audit_entries = append_audit(audit_entries, audit_entry)

        logger.info(
            f"[CSComplaint] ticket={ticket.ticket_id} severity={detection.severity} "
            f"handoff=HANDOFF_REQUESTED"
        )

        return {
            "final_answer": guard_result.text,
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }

    except Exception as e:
        logger.error(f"[CSComplaint] 执行异常: {e}", exc_info=True)
        return {
            "final_answer": "非常抱歉给您带来不便，我们已记录您的反馈，将尽快安排专人跟进。",
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }


def cs_handoff(state: dict) -> dict:
    """客服人工转接节点 — Phase 5 实现。

    流程: 确定触发原因 → 状态转换 → 创建转接工单 → 返回等待提示 → 审计日志。
    """
    cs_context = dict(state.get("cs_context", {}))
    audit_entries = list(state.get("cs_audit_entries", []))

    logger.info("[CSHandoff] initiating handoff")

    try:
        from backend.customer_service.audit import append_audit, build_audit_entry
        from backend.customer_service.handoff import (
            HandoffState,
            detect_handoff_trigger,
        )
        from backend.customer_service.handoff import (
            transition as handoff_transition,
        )
        from backend.customer_service.handoff_store import get_handoff_store
        from backend.customer_service.security.output_guard import get_output_guard

        question = state.get("question", "")
        user_id = cs_context.get("authenticated_user_id", "anonymous")
        session_id = cs_context.get("session_id", "default")
        conversation_id = cs_context.get("conversation_id", "")

        explicit_trigger = detect_handoff_trigger(question)
        if explicit_trigger:
            trigger_type = explicit_trigger.trigger_type.value
            trigger_reason = explicit_trigger.reason
        else:
            trigger_type = "auto_trigger"
            trigger_reason = "系统自动触发 (低置信度/连续失败)"

        from backend.observability.metrics import record_cs_handoff
        record_cs_handoff(trigger_type)

        store = get_handoff_store()
        existing = store.load(user_id, session_id)
        current_state_str = (existing or {}).get("handoff_state", HandoffState.AI_ACTIVE.value)
        current_state = HandoffState(current_state_str)

        handoff_transition(current_state, HandoffState.HANDOFF_REQUESTED)

        import uuid
        ticket_id = f"HANDOFF-{uuid.uuid4().hex[:8].upper()}"

        from datetime import datetime, timezone
        handoff_data = {
            "handoff_state": HandoffState.HANDOFF_REQUESTED.value,
            "trigger_type": trigger_type,
            "trigger_reason": trigger_reason,
            "ticket_id": ticket_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        store.save(user_id, session_id, handoff_data)
        cs_context["handoff_state"] = HandoffState.HANDOFF_REQUESTED.value

        answer = "已为您转接人工客服，请稍候。客服人员将尽快为您服务。"

        guard = get_output_guard()
        guard_result = guard.check(answer, cs_context)

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type="handoff_requested",
            result="success",
            target_type="handoff",
            target_id=ticket_id,
            detail=f"trigger={trigger_type}, reason={trigger_reason[:80]}",
            conversation_id=conversation_id,
        )
        audit_entries = append_audit(audit_entries, audit_entry)

        logger.info(
            f"[CSHandoff] ticket={ticket_id} trigger={trigger_type} "
            f"state=HANDOFF_REQUESTED"
        )

        return {
            "final_answer": guard_result.text,
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }

    except Exception as e:
        logger.error(f"[CSHandoff] 执行异常: {e}", exc_info=True)
        return {
            "final_answer": "转接人工客服暂时不可用，请稍后重试。",
            "cs_context": cs_context,
            "cs_audit_entries": audit_entries,
        }


def cs_handoff_intercept(state: dict) -> dict:
    """转接拦截节点 — 当 handoff_state != AI_ACTIVE 时拦截业务请求。

    HANDOFF_REQUESTED / WAITING_HUMAN → 正在转接提示
    HUMAN_ACTIVE → 人工服务中提示
    """
    cs_context = dict(state.get("cs_context", {}))
    handoff_state_str = cs_context.get("handoff_state", "ai_active")

    logger.info(f"[CSHandoffIntercept] handoff_state={handoff_state_str}")

    from backend.customer_service.handoff import HandoffState
    from backend.customer_service.security.output_guard import get_output_guard

    try:
        handoff_state = HandoffState(handoff_state_str)
    except ValueError:
        handoff_state = HandoffState.AI_ACTIVE

    if handoff_state == HandoffState.HUMAN_ACTIVE:
        answer = "当前由人工客服为您服务，AI 助手暂不介入。如有其他问题请直接描述。"
    elif handoff_state in (HandoffState.HANDOFF_REQUESTED, HandoffState.WAITING_HUMAN):
        answer = "正在为您转接人工客服，请稍候。在人工客服接入前，您可以继续描述问题。"
    else:
        answer = "当前服务状态特殊，请稍后再试或联系人工客服。"

    guard = get_output_guard()
    guard_result = guard.check(answer, cs_context)

    return {
        "final_answer": guard_result.text,
        "cs_context": cs_context,
    }
