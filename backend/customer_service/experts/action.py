"""customer_service/experts/action.py — ActionExpert

业务操作 Expert：退款、退货、换货、地址修改等写操作 + 确认状态机。
从 graph/nodes.py cs_business_action 提取核心逻辑。

设计参考: docs/domains/customer-service.md §6.3
"""
from __future__ import annotations

import re
from typing import Any

from backend.customer_service.errors import (
    AddressChangeUnavailableError,
    DatabaseError,
    OrderNotEligibleError,
    OrderNotFoundError,
    PriceMatchUnavailableError,
    RefundRequestUnavailableError,
)
from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger

_INTENT_ACTION_MAP = {
    "as_refund":   "refund",
    "as_refund_reason_update": "refund_update",
    "as_return":   "return",
    "as_exchange": "exchange",
    "a_address":   "address",
    "a_password":  "password",
    "t_order_cancel": "order_cancel",
    "t_invoice_request": "invoice",
    "t_change_address_in_transit": "address_change",
    "t_price_match": "price_match",
    "t_urge_shipping": "urge_request",
    "t_urge_delivery": "urge_request",
}

# 缺槽位需要追问的动作意图（缺陷6.2）：refund/return/exchange 以订单为
# 操作对象，缺 order_id 时绝不允许替用户猜目标（有副作用动作）。
_SLOT_ORDER_INTENTS = frozenset({
    "as_refund", "as_return", "as_exchange", "t_order_cancel",
    "t_invoice_request",
    "t_change_address_in_transit",
    "t_price_match",
    "t_urge_shipping", "t_urge_delivery",
})

_ACTION_TYPE_LABELS = {
    "refund_request": "退款申请",
    "return_request": "退货申请",
    "exchange_request": "换货申请",
    "address_update": "地址修改",
    "password_reset": "密码重置",
    "order_cancel": "取消订单",
    "invoice_request": "模拟开票申请",
    "address_change": "订单改址",
    "address_change_request": "订单改址",
    "price_match": "价保退差价",
    "urge_request": "物流催办",
}

# 语义槽位解析失败的空结果占位已移除——异常路径统一走 _new_empty_resolution()


def _new_empty_resolution() -> Any:
    from backend.customer_service.context.semantic_slots import (
        SemanticSlotResolution,
    )

    return SemanticSlotResolution()


def _inject_order_id(cs_route: dict, order_id: str) -> dict:
    """语义解析唯一绑定 → 写入 metadata.order_id 权威 referent。"""
    cs_route = dict(cs_route or {})
    metadata = dict(cs_route.get("metadata") or {})
    metadata["order_id"] = order_id
    cs_route["metadata"] = metadata
    return cs_route


def _resolve_tenant(state: dict[str, Any]) -> str:
    """可信租户解析（STOP D §67）：cs_context（runner 身份链注入）优先，
    回退请求上下文；绝不信任请求体自行携带的租户字段。"""
    from backend.core.request_context import get_tool_tenant_id

    ctx_tenant = str(((state.get("cs_context") or {}).get("tenant_id")) or "")
    return ctx_tenant or get_tool_tenant_id() or "default"


def _invoice_request_slots(
    cs_route: dict, user_message: str,
) -> tuple[str, str]:
    """提取补槽期间需要保留的发票抬头选择和发票类型。"""
    metadata = cs_route.get("metadata") or {}
    title_selector = str(
        metadata.get("invoice_title_id")
        or metadata.get("invoice_title_name")
        or metadata.get("invoice_title_selector")
        or ""
    ).strip()
    if not title_selector:
        for slot in metadata.get("semantic_slots") or []:
            if isinstance(slot, dict) and slot.get("name") in {
                "invoice_title_id", "invoice_title_name", "invoice_title",
            }:
                title_selector = str(slot.get("value") or "").strip()
                if title_selector:
                    break
    if not title_selector and any(
        word in user_message for word in ("个人", "自然人", "企业", "公司", "单位")
    ):
        title_selector = user_message

    invoice_type = str(metadata.get("invoice_type") or "electronic").strip()
    if "纸质" in user_message:
        invoice_type = "paper"
    elif "电子" in user_message:
        invoice_type = "electronic"
    return title_selector, invoice_type


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
    tenant_id = _resolve_tenant(state)

    pending_action = state.get("pending_action") or store.load(user_id, session_id)

    # 缺陷6.3（2026-09-23）：need_info 型 pending（等待订单号补槽）优先于
    # 确认流程 —— 它还没有 proposal，「确认/取消」语义不适用。
    if pending_action and pending_action.get("status") == "need_info":
        return _handle_slot_fill(
            pending_action, user_message, user_id, session_id, store, cs_route,
            tenant_id=tenant_id,
        )

    if pending_action:
        if (
            pending_action.get("action_type") == "refund_request"
            and intent == "as_refund_reason_update"
        ):
            return _revise_pending_refund_reason(
                pending_action, user_message, user_id, session_id, store,
                tenant_id=tenant_id,
            )
        return _handle_pending_confirmation(
            pending_action, user_message, user_id, session_id,
            tenant_id=tenant_id,
        )

    current_task = state.get("current_task") or {}
    if current_task.get("capability") == "propose_refund":
        return _execute_conditional_refund_task(
            user_message, user_id, session_id, store, tenant_id, state,
        )
    if state.get("task_plan"):
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft="当前任务不允许办理写操作，没有发起任何申请。",
            data={"task_status": "skipped"},
        )

    # 缺陷6.2（2026-09-23）：副作用动作缺订单号时必须结构化追问，
    # 禁止 fallback "latest"（最近一单）替用户决定操作对象。
    # 语义槽位解析（2026-10-08）：追问前先用 LLM 语义候选（product/time
    # 引用）按真实业务数据解析 —— 唯一匹配自动绑定落穿 proposal（资格
    # 校验照常），多候选进点选，无匹配保持既有追问。业务服务故障如实
    # 告知不可用（与 proposal 阶段同口径），不伪装成「没有匹配」。
    collected_slots = None
    new_address = ""
    if intent == "t_change_address_in_transit":
        from backend.customer_service.service.address_change_service import (
            extract_new_address,
        )
        new_address = extract_new_address(cs_route, user_message)
        if new_address:
            collected_slots = {"new_address": new_address}
    elif intent == "t_invoice_request":
        title_selector, invoice_type = _invoice_request_slots(
            cs_route, user_message,
        )
        collected_slots = {
            "invoice_title_selector": title_selector,
            "invoice_type": invoice_type,
        }

    if intent in _SLOT_ORDER_INTENTS and not _resolve_order_id(cs_route, user_message):
        try:
            from backend.customer_service.context.semantic_slots import (
                resolve_from_metadata,
            )

            resolution = resolve_from_metadata(tenant_id, user_id, cs_route)
        except DatabaseError:
            logger.warning("[ActionExpert] semantic slot resolve: service unavailable")
            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=(
                    "业务服务暂时不可用，请稍后重试；"
                    "您也可以回复「转人工」由人工客服协助。"
                ),
                data={},
            )
        except Exception:
            # 语义解析自身异常 = 增强不可用，保持既有追问路径
            logger.warning(
                "[ActionExpert] semantic slot resolve failed", exc_info=True,
            )
            resolution = _new_empty_resolution()
        if resolution.resolved:
            cs_route = _inject_order_id(cs_route, resolution.order_id)
            logger.info(
                "[ActionExpert] semantic slot bound: order=%s product=%r",
                resolution.order_id, resolution.matched_product,
            )
        elif resolution.ambiguous:
            return _ask_missing_slot(
                user_id, intent, session_id, store,
                tenant_id=tenant_id,
                candidates_override=resolution.candidates,
                collected_slots=collected_slots,
            )
        else:
            return _ask_missing_slot(
                user_id, intent, session_id, store, tenant_id=tenant_id,
                collected_slots=collected_slots,
            )

    if intent == "t_change_address_in_transit" and not new_address:
        return _ask_missing_address_slot(
            user_id, intent, session_id, store,
            order_id=_resolve_order_id(cs_route, user_message),
            tenant_id=tenant_id,
        )

    if intent in {"t_urge_shipping", "t_urge_delivery"}:
        return _execute_urge_request(
            user_id, intent, cs_route, user_message, session_id, state,
            tenant_id=tenant_id,
        )

    try:
        return _build_new_proposal(user_id, intent, cs_route, session_id, store,
                                   user_message, tenant_id=tenant_id)
    except AddressChangeUnavailableError as exc:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft=exc.user_message,
            data={},
        )
    except RefundRequestUnavailableError as exc:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft=exc.user_message,
            data={},
        )
    except PriceMatchUnavailableError as exc:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft=exc.user_message,
            data={},
        )
    except DatabaseError:
        # 业务网关不可用（http 模式下订单事实源连接失败/超时/5xx）：显式
        # 告知暂不可用 —— 绝不 fallback 本地演示库（缺陷6.5 红线）；
        # need_info/pending 状态保持，网关恢复后用户重发订单号即可继续。
        logger.warning("[ActionExpert] business service unavailable for proposal")
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "业务服务暂时不可用，请稍后重试；"
                "您也可以回复「转人工」由人工客服协助。"
            ),
            data={},
        )
    except OrderNotEligibleError as e:
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
        logger.info("[ActionExpert] order not found for proposal")
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "暂时没有找到可用于该申请的订单。请告诉我订单号"
                "（订单号可在「我的订单」页查看，形如 MO-XXXX），"
                "或回复「查我的所有订单」先查看订单。"
            ),
            data={},
        )


def _execute_urge_request(
    user_id: str,
    intent: str,
    cs_route: dict,
    user_message: str,
    session_id: str,
    state: dict[str, Any],
    *,
    tenant_id: str,
) -> ExpertResult:
    """在 business-mock 登记低风险物流催办；异常转交 Supervisor 升级人工。"""
    import uuid

    from backend.customer_service.action import ActionType, AgentActionRecord
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.service.urge_service import (
        UrgeServiceError,
        get_urge_service,
    )

    order_id = _resolve_order_id(cs_route, user_message)
    request_type = "shipping" if intent == "t_urge_shipping" else "delivery"
    conversation_id = str(
        state.get("conversation_id")
        or (state.get("cs_context") or {}).get("conversation_id")
        or session_id
    )

    escalation = _urge_escalation_reason(user_message)
    if escalation:
        return _urge_handoff_signal(escalation)

    try:
        outcome = get_urge_service().register_urge(
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            order_no=order_id,
            request_type=request_type,
        )
    except UrgeServiceError as exc:
        if exc.handoff_required:
            return _urge_handoff_signal(exc.handoff_reason)
        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=ActionType.URGE_REQUEST,
            result="failure",
            target_type="order",
            target_id=order_id,
            detail="mock urge request rejected",
            conversation_id=conversation_id,
        )
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=exc.user_message,
            data={
                "audit_entry": audit_entry,
                "action_result": {
                    "action_type": ActionType.URGE_REQUEST,
                    "status": "failed",
                    "user_message": exc.user_message,
                },
            },
        )

    if outcome.get("handoff_required"):
        return _urge_handoff_signal(str(
            outcome.get("handoff_reason") or "物流状态异常，需人工核实",
        ))

    request = outcome["urge_request"]
    logistics = outcome["logistics"]
    already_registered = bool(outcome.get("already_registered"))
    request_label = "催发货" if request_type == "shipping" else "催派送"
    status_display = str(logistics.get("status_display") or "状态未知")
    if already_registered:
        answer = (
            f"该订单的{request_label}已在处理中（模拟环境已登记）；"
            f"当前状态：{status_display}。"
            "这是演示记录，没有向真实商家或物流系统发送请求，"
            "也不承诺具体发货或送达时间。"
        )
    else:
        answer = (
            f"已在模拟环境为该订单登记{request_label}；"
            f"当前状态：{status_display}。"
            "这是演示记录，没有向真实商家或物流系统发送请求，"
            "也不承诺具体发货或送达时间。"
        )

    record = AgentActionRecord(
        action_id=str(uuid.uuid4()),
        action_type=ActionType.URGE_REQUEST,
        agent_type="ai",
        target_type="order",
        target_id=str(request.get("order_no") or order_id),
        data={
            "before_state": {"status_display": status_display},
            "after_state": {
                "request_id": str(request.get("request_id") or ""),
                "request_type": request_type,
                "already_registered": already_registered,
                "simulated": True,
            },
        },
        status="simulated",
    )
    record_dict = {**record.to_dict(), "confirmation_state": "not_required"}
    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type=ActionType.URGE_REQUEST,
        result="success",
        target_type="order",
        target_id=record.target_id,
        detail=(
            f"business_mock request_id={request.get('request_id')}; "
            f"request_type={request_type}; already_registered={already_registered}"
        ),
        conversation_id=conversation_id,
    )
    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=answer,
        data={
            "audit_entry": audit_entry,
            "action_result": {
                "action_type": ActionType.URGE_REQUEST,
                "status": "success",
                "user_message": answer,
                "action_record": record_dict,
            },
        },
    )


def _urge_escalation_reason(user_message: str) -> str:
    """用户明确反馈物流异常时，禁止自动登记并转人工核实。"""
    if any(term in user_message for term in ("丢件", "丢失", "丢了", "破损", "损坏")):
        return "用户反馈包裹可能丢失或破损，需人工核实并处理"
    return ""


def _urge_handoff_signal(reason: str) -> ExpertResult:
    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft="该物流问题需要人工核实，正在为您转接客服。",
        data={"handoff_required": True, "handoff_reason": reason},
    )


def _execute_conditional_refund_task(
    user_message: str,
    user_id: str,
    session_id: str,
    store: Any,
    tenant_id: str,
    state: dict[str, Any],
) -> ExpertResult:
    """只把已核实的唯一物流结果转成现有退款提案流程。"""
    from backend.customer_service.understanding.task_plan import (
        CSTask,
        CSTaskPlan,
        CSTaskResult,
        build_rule_task_plan,
        evaluate_task_condition,
    )

    safe_reply = "支付或订单事实尚未满足退款条件，这次没有发起退款申请。"
    current = state.get("current_task") or {}
    task_id = str(current.get("task_id") or "")
    query_result = None
    facts: dict[str, Any] = {}

    def task_result(status: str, error_type: str | None = None) -> dict:
        source = query_result.source if query_result is not None else "unknown"
        task_facts = {
            key: facts[key] for key in (
                "shipping_status", "order_count", "order_id", "order_no",
                "success_count", "duplicate_payment", "refund_candidate",
                "total_amount", "currency",
            ) if key in facts
        }
        return CSTaskResult(
            task_id=task_id, status=status, facts=task_facts,
            source=source, error_type=error_type,
        ).model_dump()

    try:
        plan = CSTaskPlan.model_validate(state.get("task_plan") or {})
        expected = build_rule_task_plan(user_message)
        task = next(
            item for item in plan.tasks
            if item.task_id == current.get("task_id")
        )
        expected_plan = (
            CSTaskPlan.model_validate(expected).model_dump() if expected else None
        )
        current_task = CSTask.model_validate(current).model_dump()
        if (
            expected is None
            or plan.model_dump() != expected_plan
            or task.capability != "propose_refund"
            or task.model_dump() != current_task
        ):
            raise ValueError("request does not match the constrained refund plan")

        results = {
            item.get("task_id"): CSTaskResult.model_validate(item)
            for item in (state.get("task_results") or [])
            if isinstance(item, dict)
        }
        dependencies = [results.get(dep) for dep in task.depends_on]
        if not dependencies or any(item is None or item.status != "success"
                                   for item in dependencies):
            raise ValueError("query dependency did not succeed")
        query_result = next(
            (
                item for item in dependencies
                if item.source in (
                    "sandbox_logistics_service", "business_logistics_service",
                    "sandbox_payment_service", "business_payment_service",
                )
            ),
            None,
        )
        if query_result is None:
            raise ValueError("authoritative order/payment facts are missing")
        facts = {}
        for item in dependencies:
            facts.update(item.facts)
        if (
            facts.get("order_count") != 1
            or not facts.get("order_id")
            or task.condition is None
            or not evaluate_task_condition(task.condition, facts)
        ):
            raise ValueError("refund condition is not satisfied by unique facts")
    except Exception:
        logger.info("[ActionExpert] conditional refund task rejected closed")
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft=safe_reply,
            data={"task_status": "skipped", "task_result": task_result("skipped")},
        )

    trusted_route = _inject_order_id(
        {**(state.get("cs_route") or {}), "intent": "as_refund"},
        str(facts["order_id"]),
    )
    if query_result.source in (
        "sandbox_payment_service", "business_payment_service",
    ):
        trusted_metadata = dict(trusted_route.get("metadata") or {})
        trusted_metadata["reason"] = "重复扣款"
        trusted_route = {**trusted_route, "metadata": trusted_metadata}

    from backend.customer_service.service.payment_service import (
        auto_duplicate_refund_simulation_eligible,
    )

    if auto_duplicate_refund_simulation_eligible(
        facts, source=query_result.source,
    ):
        from backend.customer_service.action import ActionType
        from backend.customer_service.audit import build_audit_entry
        from backend.customer_service.service.refund_service import RefundService
        from backend.shared.idempotency import SideEffectOutcomeUnknown

        audit_result = "error"
        try:
            record = RefundService().simulate_duplicate_payment_refund(
                user_id=user_id,
                tenant_id=tenant_id,
                order_no=str(facts["order_no"]),
            )
            refund = record.data["refund"]
            refund_id = str(refund["refund_id"])
            amount = float(refund["amount"])
            audit_result = "success"
            audit_entry = build_audit_entry(
                user_id=user_id,
                action_type=ActionType.REFUND_EXECUTE,
                result=audit_result,
                target_type=record.target_type,
                target_id=record.target_id,
                detail=(
                    "execution_mode=simulation; real_funds_affected=false; "
                    f"refund_id={refund_id}; amount={amount:.2f}; currency=CNY"
                ),
                conversation_id=str(
                    state.get("conversation_id") or session_id,
                ),
            )
            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=(
                    f"已在演示环境完成重复扣款的模拟退款，金额 ¥{amount:.2f}，"
                    f"模拟编号 `{refund_id}`。这是 mock 模拟，真实支付平台和资金"
                    "没有发生变更，也没有创建真实退款申请。"
                ),
                data={
                    "task_status": "success",
                    "task_result": task_result("success"),
                    "action_result": {
                        "action_type": ActionType.REFUND_EXECUTE,
                        "status": "simulated",
                        "action_record": record.to_dict(),
                    },
                    "audit_entry": audit_entry,
                },
            )
        except SideEffectOutcomeUnknown:
            error_type = "provider_error"
            reply = (
                "Mock 未能确认模拟退款结果，已停止自动重试并提示人工核查；"
                "本次没有调用真实支付系统。请勿重复提交。"
            )
        except OrderNotEligibleError as exc:
            audit_result = "denied"
            error_type = "business_error"
            reply = f"{exc}\n\n本次未执行模拟退款，请转人工核实。"
        except (DatabaseError, OrderNotFoundError, RefundRequestUnavailableError):
            error_type = "provider_error"
            reply = "模拟退款服务暂时不可用，未创建真实退款申请，请转人工核实。"

        audit_entry = build_audit_entry(
            user_id=user_id,
            action_type=ActionType.REFUND_EXECUTE,
            result=audit_result,
            target_type="order",
            target_id=str(facts["order_id"]),
            detail=(
                "execution_mode=simulation; result=not_confirmed; "
                "real_funds_affected=false"
            ),
            conversation_id=str(state.get("conversation_id") or session_id),
        )
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=reply,
            data={
                "task_status": "failed",
                "task_result": task_result("failed", error_type),
                "audit_entry": audit_entry,
            },
        )

    try:
        proposal_result = _build_new_proposal(
            user_id, "as_refund", trusted_route, session_id, store,
            user_message, tenant_id=tenant_id,
        )
        proposal_data = dict(proposal_result.get("data") or {})
        proposal_data["task_result"] = task_result("success")
        return {**proposal_result, "data": proposal_data}
    except DatabaseError:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft="业务服务暂时不可用，未发起退款申请，请稍后重试。",
            data={"task_result": task_result("failed", "provider_error")},
        )
    except OrderNotEligibleError as exc:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft=f"{exc}\n\n本次没有发起退款申请。",
            data={"task_result": task_result("failed", "business_error")},
        )
    except OrderNotFoundError:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft="未能核实该订单，未发起退款申请。",
            data={"task_result": task_result("failed", "business_error")},
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
    # 缺陷6.3（2026-09-23）：pending_action/confirmation_state 同步写平铺
    # state 字段 —— build_cs_graph_result（done 帧 cs_pending_action → 前端
    # 确认卡）与 reporter 的确认文案都读平铺键，只写 cs_context 会让确认卡
    # 拿不到 proposal、reporter 落回通用问句。
    if "pending_action" in data:
        update["pending_action"] = data["pending_action"]
    if "confirmation_state" in data:
        update["confirmation_state"] = data["confirmation_state"]

    return update


def _build_new_proposal(
    user_id: str,
    intent: str,
    cs_route: dict,
    session_id: str,
    store: Any,
    user_message: str = "",
    tenant_id: str = "",
) -> ExpertResult:
    """构建新 proposal 并保存到 confirmation store。

    Phase3 STOP D：保存即过 Business Operation Unique Guard —— 语义等价
    的 active 操作已存在 / 已成功终结 / 修改冲突时返回稳定业务语义
    （§20），绝不把 IntegrityError 漏成 500。
    """
    from backend.customer_service.action import build_pending_action
    from backend.customer_service.audit import build_audit_entry
    from backend.customer_service.business_guard import (
        BusinessOperationAlreadyActive,
        BusinessOperationAlreadyCompleted,
        BusinessOperationConflict,
    )
    from backend.customer_service.confirmation import ConfirmationState
    from backend.customer_service.risk import RiskLevel, requires_human_review
    from backend.observability.metrics import record_cs_confirmation

    proposal = _build_proposal(
        user_id, intent, cs_route, user_message, tenant_id=tenant_id,
    )
    pending = build_pending_action(proposal)

    try:
        store.save(user_id, session_id, pending, tenant_id=tenant_id)
    except BusinessOperationAlreadyActive as e:
        logger.info(
            "[ActionExpert] business guard duplicate: existing=%s state=%s",
            e.existing_confirmation_id, e.existing_state,
        )
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "该操作已在处理中，请勿重复提交；您可以在会话中回复"
                "「取消」后再重新发起，或回复「转人工」由人工客服协助。"
            ),
            data={"business_guard": "duplicate",
                  "existing_confirmation_id": e.existing_confirmation_id,
                  "existing_state": e.existing_state},
        )
    except BusinessOperationAlreadyCompleted:
        logger.info("[ActionExpert] business guard terminal-duplicate: type=%s",
                    proposal.action_type)
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft="该订单的此项操作此前已提交成功，无需重复发起；如需其他帮助请告诉我。",
            data={"business_guard": "terminal_duplicate"},
        )
    except BusinessOperationConflict as e:
        logger.warning("[ActionExpert] business guard conflict: %s", e.message)
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "您要发起的操作与当前处理中的另一操作冲突，"
                "请先取消当前操作或回复「转人工」由人工客服协助。"
            ),
            data={"business_guard": "conflict"},
        )

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
    tenant_id: str = "",
) -> ExpertResult:
    """处理用户对 pending action 的确认/取消响应。

    P1 重构：状态流转/执行/审计全部委托 confirmation_flow.process_confirmation
    （与 pending_handler 共用唯一实现，含原子认领幂等闸门）——
    此处整段重复实现已删除（audit-report §P0-2）。
    """
    from backend.customer_service.confirmation_flow import process_confirmation

    outcome = process_confirmation(
        pending_action, user_message, user_id, session_id,
        tenant_id=tenant_id,
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
    user_id: str,
    intent: str,
    cs_route: dict,
    user_message: str = "",
    *,
    tenant_id: str = "",
) -> Any:
    """Dispatch to the appropriate service to build an ActionProposal。"""
    action_type = _INTENT_ACTION_MAP.get(intent, "refund")

    if action_type == "refund_update":
        from backend.customer_service.service.refund_service import get_refund_service

        return get_refund_service().build_refund_reason_update_proposal(
            user_id=user_id,
            tenant_id=tenant_id,
            reason=_extract_updated_refund_reason(
                cs_route, user_message,
            ),
        )

    if action_type == "refund":
        from backend.customer_service.service.refund_service import get_refund_service
        order_id = _resolve_order_id(cs_route, user_message)
        reason = cs_route.get("metadata", {}).get("reason", "")
        return get_refund_service().build_refund_proposal(user_id, order_id, reason)

    if action_type == "address_change":
        from backend.customer_service.service.address_change_service import (
            extract_new_address,
            get_address_change_service,
        )
        return get_address_change_service().build_proposal(
            user_id,
            _resolve_order_id(cs_route, user_message),
            extract_new_address(cs_route, user_message),
        )

    if action_type == "order_cancel":
        from backend.customer_service.service.cancel_order_service import (
            get_cancel_order_service,
        )
        order_id = _resolve_order_id(cs_route, user_message)
        reason = cs_route.get("metadata", {}).get("reason", "")
        return get_cancel_order_service().build_cancel_order_proposal(
            user_id, order_id, reason,
        )

    if action_type == "invoice":
        from backend.customer_service.service.invoice_service import (
            get_invoice_service,
        )

        title_selector, invoice_type = _invoice_request_slots(
            cs_route, user_message,
        )
        order_id = _resolve_order_id(cs_route, user_message)
        return get_invoice_service().build_invoice_proposal(
            user_id=user_id,
            tenant_id=tenant_id,
            order_no=order_id,
            title_selector=title_selector,
            invoice_type=invoice_type,
        )

    if action_type == "price_match":
        from backend.customer_service.service.price_match_service import (
            get_price_match_service,
        )

        order_id = _resolve_order_id(cs_route, user_message)
        return get_price_match_service().build_proposal(
            user_id, tenant_id, order_id,
        )

    if action_type == "return":
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        order_id = _resolve_order_id(cs_route, user_message)
        reason = cs_route.get("metadata", {}).get("reason", "")
        return get_after_sales_service().build_return_proposal(user_id, order_id, reason)

    if action_type == "exchange":
        from backend.customer_service.service.after_sales_service import get_after_sales_service
        order_id = _resolve_order_id(cs_route, user_message)
        reason = cs_route.get("metadata", {}).get("reason", "")
        return get_after_sales_service().build_exchange_proposal(user_id, order_id, reason)

    if action_type == "address":
        from backend.customer_service.service.account_action_service import get_account_action_service
        new_address = cs_route.get("metadata", {}).get("new_address", "")
        # 订单级改址以订单为操作实体：语义守卫按 (tenant, action, target,
        # payload) 去重，塌缩到 user 会让不同订单的改址互相误挡（Q3 实测
        # 三单改址共用同一指纹）；无订单号才是账户级默认地址修改。
        order_id = _resolve_order_id(cs_route, user_message)
        return get_account_action_service().build_address_update_proposal(
            user_id, order_id or user_id, new_address,
        )

    if action_type == "password":
        from backend.customer_service.service.account_action_service import get_account_action_service
        return get_account_action_service().build_password_reset_proposal(user_id)

    from backend.customer_service.errors import ValidationError
    raise ValidationError(f"不支持的操作类型: {intent}")


def _extract_updated_refund_reason(cs_route: dict, user_message: str) -> str:
    """只提取用户明确给出的新原因；不能从旧上下文猜测内容。"""
    text = str(user_message or "")
    match = re.search(
        r"(?:改成|改为|更正为|修改为|写成|换成)\s*(.{1,200})$", text,
    )
    if match:
        return match.group(1).strip(" \t\r\n，。；;！!？?")[:500]

    metadata = (cs_route or {}).get("metadata") or {}
    reason = str(metadata.get("reason") or "").strip()
    if not reason:
        for slot in metadata.get("semantic_slots") or []:
            if slot.get("name") == "reason":
                reason = str(slot.get("value") or "").strip()
                break
    return reason[:500]


def _revise_pending_refund_reason(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
    store: Any,
    *,
    tenant_id: str,
) -> ExpertResult:
    """CAS 更新未提交提案的原因，并递增版本使旧确认失效。"""
    reason = _extract_updated_refund_reason({}, user_message)
    if not reason:
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft="请告诉我正确的退款原因，例如「改成商品破损」。",
            data={"pending_action": pending_action},
        )

    revised = dict(pending_action)
    before = dict(revised.get("before_state") or {})
    after = dict(revised.get("after_state") or {})
    operation = str(after.get("operation") or "create")
    after["reason"] = reason
    revised["before_state"] = before
    revised["after_state"] = after

    proposal_text = str(revised.get("proposal_text") or "")
    display_label = "修改为" if operation == "update_reason" else "退款原因"
    line = f"**{display_label}:** {reason}"
    pattern = rf"\*\*{re.escape(display_label)}:\*\*[^\n]*"
    if re.search(pattern, proposal_text):
        proposal_text = re.sub(pattern, line, proposal_text, count=1)
    else:
        proposal_text = f"{proposal_text.rstrip()}\n{line}\n"
    revised["proposal_text"] = proposal_text

    expected_version = int(
        revised.get("version") or revised.get("proposal_version") or 1,
    )
    proposal_id = str(
        revised.get("proposal_id") or revised.get("action_id") or "",
    )
    revised["version"] = expected_version + 1
    if not proposal_id or not store.update_pending_if_version(
        user_id,
        session_id,
        revised,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        expected_version=expected_version,
    ):
        return ExpertResult(
            expert="action", status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "退款提案状态已变化，刚才的修改没有应用；"
                "请刷新后重新查询申请。"
            ),
            data={},
        )

    return ExpertResult(
        expert="action", status=ExpertStatus.SUCCESS.value,
        response_draft=(
            "已更新退款原因，请重新核对后确认：\n\n"
            f"{proposal_text}"
        ),
        data={
            "pending_action": revised,
            "confirmation_state": revised.get("confirmation_state", "pending_confirmation"),
        },
    )


def _resolve_order_id(cs_route: dict, user_message: str) -> str:
    """订单槽位解析（B4 收敛：优先级单一实现见 context_manager）。

    router metadata 显式值优先，其次当前轮消息实体。两处都取不到时返回
    空串 —— 由调用方进入缺槽位追问（缺陷6.2），绝不回退 "latest"。
    """
    from backend.customer_service.context_manager import resolve_order_slot

    return resolve_order_slot(cs_route, user_message)


def _extract_order_id_from_message(user_message: str) -> str:
    """订单号提取（B4 收敛：委托 context_manager 单一实现，保留签名供测试）。

    understanding 层在规范化文本上抽取（NFKC/零宽剥离复用 Input Guard
    事实源），支持字母数字混合段（两段式、形近错别字原样认领）与关键词
    纯数字形态。识别不到返回空串 —— 缺槽位走结构化追问，不再注入
    "latest"（2026-09-19 引入的语义化兜底已被缺陷6否决：有副作用的动作
    不能替用户猜操作对象）。
    """
    from backend.customer_service.context_manager import extract_order_entity

    return extract_order_entity(user_message)


def _ask_missing_slot(
    user_id: str, intent: str, session_id: str, store: Any,
    tenant_id: str = "",
    candidates_override: list[dict] | None = None,
    collected_slots: dict[str, str] | None = None,
) -> ExpertResult:
    """缺订单槽位：持久化 need_info 型 pending_action 并结构化追问。

    状态复用现有 pending_action 体系（ConfirmationStore，同一 (user_id,
    session_id) 键），仅追加 need_info 专用字段；confirmation_state 仍为
    pending_confirmation（满足 confirmations.state CHECK 约束，
    pending_handler据此转入 action expert 补槽，不进确认流程）。

    candidates_override（2026-10-08 语义槽位）：语义解析得到的多候选订单
    直接作为点选数据源（order_id/order_no/product_names/amount/status），
    跳过退款政策窗口查询——语义引用（「买耳机的那笔」）的候选集来自
    用户真实订单匹配，不再受退款窗口收窄。仅在动作意图族生效。
    """
    import uuid
    from datetime import datetime, timezone

    from backend.config.customer_service import CS_CONFIRMATION_TTL_SECONDS
    from backend.customer_service.confirmation import (
        ConfirmationState,
        compute_expires_at,
    )

    now = datetime.now(timezone.utc)
    action_type = _INTENT_ACTION_MAP.get(intent, "refund")
    label = _ACTION_TYPE_LABELS.get(f"{action_type}_request", action_type)
    slot_risk_level = "medium" if action_type == "invoice" else "high"

    # 退款点选候选（2026-10-08 拍板）：缺订单号时不再让用户手输——按
    # 退款政策窗口列出可退订单（订单号+商品名+金额）供用户点选/回复序号。
    # 候选查询失败或 http 网关模式返回空 → 退回手输路径（现状行为）。
    # 语义槽位多候选（candidates_override）优先：来自用户真实订单的
    # 语义匹配集，适用于全部订单型动作意图（资格校验由 proposal 阶段
    # 的 service 把关，点选不跳过任何红线）。
    candidates: list[dict] = []
    window_days = 0
    if candidates_override:
        candidates = list(candidates_override)
    elif action_type == "refund":
        try:
            from backend.customer_service.service.refund_service import (
                get_refund_service,
            )

            bundle = get_refund_service().list_refund_candidates(user_id)
            candidates = bundle.get("candidates") or []
            window_days = int(bundle.get("window_days") or 0)
        except Exception as e:
            logger.warning("[ActionExpert] 退款候选查询失败，退回手输: %s", e)

    if candidates:
        options: list[str] = []
        lines: list[str] = []
        action_label = _ACTION_TYPE_LABELS.get(
            f"{action_type}_request", action_type,
        )
        for idx, c in enumerate(candidates, start=1):
            product = c.get("product_names") or "商品信息缺失"
            label_text = (
                f"{c['order_no']} ¥{c['amount']:.2f} {product}"
            )
            options.append(f"{action_label}：{label_text}")
            lines.append(f"**{idx}.** `{c['order_no']}` — {product}（¥{c['amount']:.2f}，{c['status']}）")
        if candidates_override:
            lead = "为您找到多个相关订单，请点选或回复序号选择要办理的订单："
        else:
            lead = f"为您找到近 {window_days} 天内符合退款政策的订单，请点选或回复序号："
        response_draft = (
            lead + "\n\n"
            + "\n\n".join(lines)
            + f"\n\n点击上方选项即可发起该订单的{action_label}。"
        )
        pending = {
            "action_id": str(uuid.uuid4()),
            "action_type": f"{action_type}_request",
            "intent": intent,
            "status": "need_info",
            "missing_slots": ["order_id"],
            "collected_slots": dict(collected_slots or {}),
            # 点选候选映射：用户回复序号 N → candidates[N-1].order_id
            "candidates": candidates,
            "target_type": "order",
            "target_id": "",
            "risk_level": slot_risk_level,
            "requires_confirmation": False,
            "confirmation_state": ConfirmationState.PENDING_CONFIRMATION.value,
            "retry_count": 0,
            "created_at": now.isoformat(),
            "expires_at": compute_expires_at(
                now, CS_CONFIRMATION_TTL_SECONDS
            ).isoformat(),
        }
        store.save(user_id, session_id, pending, tenant_id=tenant_id)
        logger.info(
            "[ActionExpert] missing slot order_id, %d refund candidates offered: "
            "intent=%s user_id=%s", len(candidates), intent, user_id,
        )
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=response_draft,
            data={
                "pending_action": pending,
                "confirmation_state": pending["confirmation_state"],
                # CS 图透传为 clarification 帧（events.py 发射，前端渲染
                # 可点选项；选项=用户话术，点击即作为消息发送）
                "_clarify": {
                    "source": (
                        "semantic_candidates" if candidates_override
                        else "refund_candidates"
                    ),
                    "question": f"请选择要办理{action_label}的订单：",
                    "options": options,
                    "handoff_available": False,
                },
            },
        )

    pending = {
        "action_id": str(uuid.uuid4()),
        "action_type": f"{action_type}_request",
        "intent": intent,
        "status": "need_info",
        "missing_slots": ["order_id"],
        "collected_slots": dict(collected_slots or {}),
        "target_type": "order",
        "target_id": "",
        "risk_level": slot_risk_level,
        "requires_confirmation": False,
        "confirmation_state": ConfirmationState.PENDING_CONFIRMATION.value,
        "retry_count": 0,
        "created_at": now.isoformat(),
        "expires_at": compute_expires_at(
            now, CS_CONFIRMATION_TTL_SECONDS
        ).isoformat(),
    }
    store.save(user_id, session_id, pending, tenant_id=tenant_id)
    logger.info(
        "[ActionExpert] missing slot order_id, ask user: intent=%s user_id=%s",
        intent, user_id,
    )

    window_note = (
        f"（当前退款政策：下单后 {window_days} 天内可申请）" if window_days else ""
    )
    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=(
            f"请提供需要办理{label}的订单号{window_note}，"
            "订单号可在「我的订单」页查看，也可以回复「查我的所有订单」"
            "由我为您列出；我会先核对订单，确认无误后再提交申请。"
        ),
        data={
            "pending_action": pending,
            "confirmation_state": pending["confirmation_state"],
        },
    )


def _ask_missing_address_slot(
    user_id: str,
    intent: str,
    session_id: str,
    store: Any,
    *,
    order_id: str,
    tenant_id: str = "",
) -> ExpertResult:
    """订单号已明确但缺新地址时，保存 need_info 状态并追问。"""
    import uuid
    from datetime import datetime, timezone

    from backend.config.customer_service import CS_CONFIRMATION_TTL_SECONDS
    from backend.customer_service.confirmation import (
        ConfirmationState,
        compute_expires_at,
    )

    now = datetime.now(timezone.utc)
    pending = {
        "action_id": str(uuid.uuid4()),
        "action_type": "address_change_request",
        "intent": intent,
        "status": "need_info",
        "missing_slots": ["new_address"],
        "collected_slots": {"order_id": order_id},
        "target_type": "order",
        "target_id": order_id,
        "risk_level": "medium",
        "requires_confirmation": False,
        "confirmation_state": ConfirmationState.PENDING_CONFIRMATION.value,
        "retry_count": 0,
        "created_at": now.isoformat(),
        "expires_at": compute_expires_at(
            now, CS_CONFIRMATION_TTL_SECONDS,
        ).isoformat(),
    }
    store.save(user_id, session_id, pending, tenant_id=tenant_id)
    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=(
            f"请提供订单 {order_id} 的完整新收货地址（省/市/区县和详细门牌），"
            "目前没有提交改址请求。"
        ),
        data={
            "pending_action": pending,
            "confirmation_state": pending["confirmation_state"],
        },
    )


def _handle_new_address_slot_fill(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
    store: Any,
    cs_route: dict,
    tenant_id: str,
) -> ExpertResult:
    from backend.config.customer_service import CS_MAX_CONFIRMATION_RETRIES
    from backend.customer_service.service.address_change_service import (
        extract_new_address,
    )

    address = extract_new_address(
        {"metadata": {"new_address": user_message}}, user_message,
    )
    if not address:
        retries = int(pending_action.get("retry_count", 0)) + 1
        if retries > CS_MAX_CONFIRMATION_RETRIES:
            store.clear(user_id, session_id, final_state="expired")
            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=(
                    "多次未收到有效的新收货地址，已结束本次改址请求；"
                    "如需继续，请重新发起或联系人工客服。"
                ),
                data={},
            )
        updated = {**pending_action, "retry_count": retries}
        store.save(user_id, session_id, updated, tenant_id=tenant_id)
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "地址信息不完整，请提供包含省/市/区县和详细门牌的完整新地址；"
                "目前没有提交改址请求。"
            ),
            data={
                "pending_action": updated,
                "confirmation_state": updated.get(
                    "confirmation_state", "pending_confirmation",
                ),
            },
        )

    order_id = str(
        (pending_action.get("collected_slots") or {}).get("order_id")
        or pending_action.get("target_id")
        or ""
    )
    route = dict(cs_route or {})
    metadata = dict(route.get("metadata") or {})
    metadata["order_id"] = order_id
    metadata["new_address"] = address
    route["metadata"] = metadata
    try:
        return _build_new_proposal(
            user_id,
            pending_action.get("intent", "t_change_address_in_transit"),
            route,
            session_id,
            store,
            user_message,
            tenant_id=tenant_id,
        )
    except AddressChangeUnavailableError as exc:
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=exc.user_message,
            data={},
        )
    except DatabaseError:
        logger.warning("[ActionExpert] address change service unavailable after slot fill")
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "改址模拟服务暂时不可用，未提交改址请求；"
                "您可以稍后重试或联系人工客服。"
            ),
            data={"pending_action": pending_action},
        )
    except OrderNotFoundError:
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "没有找到这笔订单，未提交改址请求；请核对订单号或联系人工客服。"
            ),
            data={},
        )
    except OrderNotEligibleError as exc:
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=str(exc),
            data={},
        )


def _handle_slot_fill(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
    store: Any,
    cs_route: dict,
    tenant_id: str = "",
) -> ExpertResult:
    """need_info 补槽：下一轮消息优先尝试填充缺失的 order_id。

    补槽成功 → 以显式订单号走正常 proposal 流程（eligibility → 确认卡），
    不再进入 KB/RAG；补不上 → 追问（retry 上限后释放，不强行吞掉用户
    的新意图）。pronoun/「那单」等通用上下文改写属 Step 6，此处只认
    当前轮的显式订单号实体。
    """
    from backend.config.customer_service import CS_MAX_CONFIRMATION_RETRIES

    if "new_address" in (pending_action.get("missing_slots") or []):
        return _handle_new_address_slot_fill(
            pending_action,
            user_message,
            user_id,
            session_id,
            store,
            cs_route,
            tenant_id,
        )

    # need_info 阶段用户取消由 pending_handler 在转发前短路处理
    # （设计方案 场景4；取消后不进 supervisor 再路由——B6 实机验证定位），
    # 此处只负责补槽/追问。

    # 点选候选序号回填（2026-10-08）：用户回复「1/2/3」→ 映射到缺槽
    # 追问时下发的候选订单。先于订单实体提取执行（候选数 ≤5，纯 1-2 位
    # 数字不会是合法订单号，避免被实体提取先吞）。
    candidates = pending_action.get("candidates") or []
    stripped = (user_message or "").strip()
    picked_candidate = None
    if (
        candidates and stripped.isdigit()
        and len(stripped) <= 2 and 1 <= int(stripped) <= len(candidates)
    ):
        picked_candidate = candidates[int(stripped) - 1]

    order_id = _extract_order_id_from_message(user_message)
    if picked_candidate and not order_id:
        order_id = str(picked_candidate.get("order_id") or picked_candidate.get("order_no") or "")
        if order_id:
            logger.info(
                "[ActionExpert] candidate #%s picked -> order_id=%s",
                stripped, order_id,
            )
    if not order_id:
        # 语义槽位补槽（2026-10-08）：用户用自然引用回答追问（「就耳机
        # 那个」）时，按当前轮语义候选解析；唯一绑定照常走 proposal，
        # 多候选/无匹配保持追问。解析失败（服务故障/异常）不阻断追问。
        try:
            from backend.customer_service.context.semantic_slots import (
                resolve_from_metadata,
            )

            _resolution = resolve_from_metadata(tenant_id, user_id, cs_route)
            if _resolution.resolved:
                order_id = _resolution.order_id
                logger.info(
                    "[ActionExpert] semantic slot fill: order_id=%s", order_id,
                )
        except Exception:
            logger.warning(
                "[ActionExpert] semantic slot fill failed", exc_info=True,
            )
    if order_id:
        cs_route = dict(cs_route or {})
        metadata = dict(cs_route.get("metadata") or {})
        metadata["order_id"] = order_id
        intent = pending_action.get("intent", "as_refund")
        if intent == "t_change_address_in_transit":
            from backend.customer_service.service.address_change_service import (
                extract_new_address,
            )
            address = (
                (pending_action.get("collected_slots") or {}).get("new_address")
                or extract_new_address(cs_route, user_message)
            )
            if not address:
                return _ask_missing_address_slot(
                    user_id, intent, session_id, store,
                    order_id=order_id, tenant_id=tenant_id,
                )
            metadata["new_address"] = address
        elif intent == "t_invoice_request":
            collected_slots = pending_action.get("collected_slots") or {}
            title_selector, _ = _invoice_request_slots(cs_route, user_message)
            if not title_selector and collected_slots.get("invoice_title_selector"):
                metadata["invoice_title_selector"] = collected_slots[
                    "invoice_title_selector"
                ]
            has_current_invoice_type = (
                metadata.get("invoice_type") == "paper"
                or "纸质" in user_message
                or "电子" in user_message
            )
            if (
                not has_current_invoice_type
                and collected_slots.get("invoice_type")
            ):
                metadata["invoice_type"] = collected_slots["invoice_type"]
        cs_route["metadata"] = metadata
        logger.info(
            "[ActionExpert] slot filled: order_id=%s intent=%s", order_id, intent,
        )
        try:
            return _build_new_proposal(
                user_id, intent, cs_route, session_id, store, user_message,
                tenant_id=tenant_id,
            )
        except OrderNotEligibleError as e:
            logger.info("[ActionExpert] proposal declined after slot fill: %s", e)
            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=(
                    f"{e}\n\n可以告诉我其他订单号让我重新核对，"
                    "或回复「查我的所有订单」查看各订单当前状态。"
                ),
                data={},
            )
        except DatabaseError:
            logger.warning(
                "[ActionExpert] business service unavailable after slot fill"
            )
            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=(
                    "业务服务暂时不可用，请稍后重试；"
                    "您也可以回复「转人工」由人工客服协助。"
                ),
                data={},
            )
        except OrderNotFoundError:
            return ExpertResult(
                expert="action",
                status=ExpertStatus.SUCCESS.value,
                response_draft=(
                    f"没有找到订单 {order_id}，请核对后重新告诉我订单号，"
                    "或回复「查我的所有订单」先查看订单。"
                ),
                data={},
            )

    retries = int(pending_action.get("retry_count", 0)) + 1
    if retries > CS_MAX_CONFIRMATION_RETRIES:
        store.clear(user_id, session_id, final_state="expired")
        logger.info(
            "[ActionExpert] slot fill retries exhausted: user_id=%s", user_id,
        )
        return ExpertResult(
            expert="action",
            status=ExpertStatus.SUCCESS.value,
            response_draft=(
                "多次未收到有效的订单号，已为您结束本次申请。"
                "如需继续办理，请再次告诉我并附上订单号。"
            ),
            data={},
        )

    label = _ACTION_TYPE_LABELS.get(
        pending_action.get("action_type", ""),
        pending_action.get("action_type", "申请"),
    )
    updated = {**pending_action, "retry_count": retries}
    store.save(user_id, session_id, updated)
    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=(
            f"请提供需要办理{label}的订单号，"
            "订单号可在「我的订单」页查看，也可以回复「查我的所有订单」"
            "由我为您列出；我会先核对订单，确认无误后再提交申请。"
        ),
        data={
            "pending_action": updated,
            "confirmation_state": pending_action.get(
                "confirmation_state", "pending_confirmation"
            ),
        },
    )


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
