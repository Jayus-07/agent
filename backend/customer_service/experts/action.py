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

    # 缺陷6.2（2026-09-23）：副作用动作缺订单号时必须结构化追问，
    # 禁止 fallback "latest"（最近一单）替用户决定操作对象。
    if intent in _SLOT_ORDER_INTENTS and not _resolve_order_id(cs_route, user_message):
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
) -> ExpertResult:
    """缺订单槽位：持久化 need_info 型 pending_action 并结构化追问。

    状态复用现有 pending_action 体系（ConfirmationStore，同一 (user_id,
    session_id) 键），仅追加 need_info 专用字段；confirmation_state 仍为
    pending_confirmation（满足 confirmations.state CHECK 约束，
    pending_handler据此转入 action expert 补槽，不进确认流程）。
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

    return ExpertResult(
        expert="action",
        status=ExpertStatus.SUCCESS.value,
        response_draft=(
            f"请提供需要办理{label}的订单号（例如 DEMO-1002），"
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

    order_id = _extract_order_id_from_message(user_message)
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
