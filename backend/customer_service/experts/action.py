"""customer_service/experts/action.py — ActionExpert

业务操作 Expert：退款、退货、换货、地址修改等写操作 + 确认状态机。
从 graph/nodes.py cs_business_action 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.3
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.errors import (
    DatabaseError,
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

# 缺槽位需要追问的动作意图（缺陷6.2）：refund/return/exchange 以订单为
# 操作对象，缺 order_id 时绝不允许替用户猜目标（有副作用动作）。
_SLOT_ORDER_INTENTS = frozenset({"as_refund", "as_return", "as_exchange"})

_ACTION_TYPE_LABELS = {
    "refund_request": "退款申请",
    "return_request": "退货申请",
    "exchange_request": "换货申请",
    "address_update": "地址修改",
    "password_reset": "密码重置",
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
            return _ask_missing_slot(user_id, intent, session_id, store,
                                     tenant_id=tenant_id,
                                     candidates_override=resolution.candidates)
        else:
            return _ask_missing_slot(user_id, intent, session_id, store,
                                     tenant_id=tenant_id)

    try:
        return _build_new_proposal(user_id, intent, cs_route, session_id, store,
                                   user_message, tenant_id=tenant_id)
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
                "（例如 DEMO-1002），或回复「查我的所有订单」先查看订单。"
            ),
            data={},
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
        CSTaskPlan,
        CSTask,
        CSTaskResult,
        build_rule_task_plan,
        evaluate_task_condition,
    )

    safe_reply = "订单和物流信息尚未满足退款条件，这次没有发起退款申请。"
    current = state.get("current_task") or {}
    task_id = str(current.get("task_id") or "")
    query_result = None
    facts: dict[str, Any] = {}

    def task_result(status: str, error_type: str | None = None) -> dict:
        source = query_result.source if query_result is not None else "unknown"
        task_facts = {
            key: facts[key] for key in (
                "shipping_status", "order_count", "order_id", "order_no",
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
                )
            ),
            None,
        )
        if query_result is None:
            raise ValueError("authoritative logistics facts are missing")
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

    proposal = _build_proposal(user_id, intent, cs_route, user_message)
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
    user_id: str, intent: str, cs_route: dict, user_message: str = ""
) -> Any:
    """Dispatch to the appropriate service to build an ActionProposal。"""
    action_type = _INTENT_ACTION_MAP.get(intent, "refund")

    if action_type == "refund":
        from backend.customer_service.service.refund_service import get_refund_service
        order_id = _resolve_order_id(cs_route, user_message)
        reason = cs_route.get("metadata", {}).get("reason", "")
        return get_refund_service().build_refund_proposal(user_id, order_id, reason)

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
        return get_account_action_service().build_address_update_proposal(
            user_id, user_id, new_address,
        )

    if action_type == "password":
        from backend.customer_service.service.account_action_service import get_account_action_service
        return get_account_action_service().build_password_reset_proposal(user_id)

    from backend.customer_service.errors import ValidationError
    raise ValidationError(f"不支持的操作类型: {intent}")


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
            "collected_slots": {},
            # 点选候选映射：用户回复序号 N → candidates[N-1].order_id
            "candidates": candidates,
            "target_type": "order",
            "target_id": "",
            "risk_level": "high",
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
        "collected_slots": {},
        "target_type": "order",
        "target_id": "",
        "risk_level": "high",
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
            f"请提供需要办理{label}的订单号（例如 DEMO-1002）{window_note}，"
            "我会先为您核对订单，确认无误后再提交申请。"
        ),
        data={
            "pending_action": pending,
            "confirmation_state": pending["confirmation_state"],
        },
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
        cs_route["metadata"] = metadata
        intent = pending_action.get("intent", "as_refund")
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
            f"请提供需要办理{label}的订单号（例如 DEMO-1002），"
            "我会先为您核对订单，确认无误后再提交申请。"
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
