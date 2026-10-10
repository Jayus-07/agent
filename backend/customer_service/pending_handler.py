"""customer_service/pending_handler.py — CS Pending Handler 节点

Phase 5: 多轮确认流程处理器。
位于 cs_state_loader 与 cs_supervisor 之间，校验人工接管状态并分类 Pending
期间的当前消息：确认/取消、单轮只读问题、冲突写入、补槽或歧义追问。

拓扑:
  START → cs_state_loader → cs_pending_handler
    → (无 pending) → cs_supervisor
    → (确认/取消) → confirmation_flow → cs_reporter
    → (只读/转人工) → cs_supervisor → 白名单 Expert（最多一个）
    → (歧义/冲突/人工接管) → cs_reporter

P1 重构（2026-09-17）：核心逻辑收敛到 confirmation_flow.process_confirmation
（与 ActionExpert 共用单一实现，含原子认领幂等闸门），本模块只做
Command 输出映射。

设计参考: docs/domains/customer-service.md §6.3
"""
from __future__ import annotations

import re
from typing import Any

from langgraph.types import Command

from backend.shared.logger import logger
from backend.customer_service.graph_state import PendingTurnDecision

_PENDING_STATES = frozenset({"pending", "pending_confirmation"})


def cs_pending_handler_node(state: dict[str, Any]) -> Command:
    """CS Pending Handler 节点 — 多轮确认流程入口。

    入口先按租户和会话读取 PG 活跃 Handoff；只有明确不存在活跃工单时，
    才按当前消息与本轮路由处理 Pending。
    """
    # STOP CS-A P0-3（F2）：pending 处理前的取消检查（确认消费是写操作）
    from backend.core.request_context import raise_if_cancelled

    raise_if_cancelled("cs_pending")

    pending_action = state.get("pending_action")
    if not pending_action:
        return Command(goto="cs_supervisor", update={})

    # 先从 PG 读取活跃工单。状态快照/L1 可能落后于人工接管；读取失败
    # 也不能当作「没有工单」，否则会在人工处理期间继续查单或执行确认。
    handoff_update = _read_authoritative_handoff(pending_action, state)
    if handoff_update is not None:
        return handoff_update
    # PG 明确无活跃 Handoff 时，覆盖可能过期的 checkpoint 快照。
    state = {**state, "handoff_state": "ai_active", "handling_mode": "ai"}

    # need_info 只接受明确槽位回答继续 ActionExpert；普通知识/查询仍可
    # 只读答一次，其余内容追问，不把任意新消息塞进补槽或副作用链。
    if pending_action.get("status") == "need_info":
        decision = _classify_pending_turn(state)
        if decision == PendingTurnDecision.CANCEL:
            return _cancel_need_info(pending_action, state)

        if decision == PendingTurnDecision.HANDOFF:
            return _release_need_info_for_handoff(pending_action, state)

        confirmation_state = state.get("confirmation_state", "")
        if confirmation_state not in _PENDING_STATES:
            return Command(goto="cs_supervisor", update={})

        if decision == PendingTurnDecision.READ_ONLY_QUERY:
            return _allow_readonly_turn(pending_action, decision, state)

        if _looks_like_need_info_slot_answer(state):
            return Command(goto="cs_action_expert", update={
                "pending_turn_decision": PendingTurnDecision.AMBIGUOUS.value,
                "pending_turn_slot_fill": True,
                "pending_action": pending_action,
            })
        return _pending_clarification(
            pending_action, decision=PendingTurnDecision.AMBIGUOUS,
            state=state, need_info=True,
        )

    # 过期是当前 Proposal 的终态，优先于只读旁路；流程仍复用唯一
    # ConfirmationFlow，按原有审计与持久化路径清理。
    from backend.customer_service.confirmation import is_expired

    if is_expired(pending_action):
        return _process_pending(
            pending_action,
            str(state.get("user_message", "")),
            str(state.get("user_id", "")),
            str(state.get("session_id", "")),
            state,
        )

    confirmation_state = state.get("confirmation_state", "")
    if confirmation_state not in _PENDING_STATES:
        return Command(goto="cs_supervisor", update={})

    decision = _classify_pending_turn(state)

    if decision == PendingTurnDecision.READ_ONLY_QUERY:
        return _allow_readonly_turn(pending_action, decision, state)
    if decision == PendingTurnDecision.HANDOFF:
        return Command(goto="cs_supervisor", update={
            "pending_turn_decision": decision.value,
            "pending_turn_expert_consumed": False,
            "pending_action": pending_action,
        })
    if decision == PendingTurnDecision.NEW_WRITE_CONFLICT:
        return _pending_clarification(
            pending_action, decision=decision, state=state,
        )
    if decision == PendingTurnDecision.AMBIGUOUS:
        return _pending_clarification(
            pending_action, decision=decision, state=state,
        )

    user_id = str(state.get("user_id", ""))
    session_id = str(state.get("session_id", ""))
    user_message = str(state.get("user_message", ""))

    logger.info(
        "[CS PendingHandler] pending_action found: type=%s user=%s",
        pending_action.get("action_type", "unknown"), user_id,
    )

    return _process_pending(
        pending_action, user_message, user_id, session_id, state,
    )


def _classify_pending_turn(state: dict[str, Any]) -> PendingTurnDecision:
    """按当前消息与当前轮路由分类，不以词面相似推断用户已确认。"""
    from backend.customer_service.confirmation import (
        ConfirmationIntent,
        detect_confirmation_intent,
    )
    from backend.customer_service.handoff import detect_handoff_trigger

    message = str(state.get("user_message", ""))
    if detect_handoff_trigger(message):
        return PendingTurnDecision.HANDOFF

    intent = detect_confirmation_intent(message)
    if intent == ConfirmationIntent.CONFIRM:
        return PendingTurnDecision.CONFIRM
    if intent == ConfirmationIntent.CANCEL:
        return PendingTurnDecision.CANCEL

    from backend.customer_service.graph_state import route_path_value

    route_path = route_path_value(
        (state.get("cs_route") or {}).get("route_path")
    )
    if route_path in {"knowledge_query", "business_query"}:
        return PendingTurnDecision.READ_ONLY_QUERY

    # 疑问句即使被上游路由到 action，也不能当成确认或新写入指令。
    from backend.customer_service.confirmation import _is_question_form
    if _is_question_form(message.strip().lower()):
        return PendingTurnDecision.AMBIGUOUS

    if route_path == "business_action":
        return PendingTurnDecision.NEW_WRITE_CONFLICT
    return PendingTurnDecision.AMBIGUOUS


def _read_authoritative_handoff(
    pending_action: dict, state: dict[str, Any],
) -> Command | None:
    """读取活跃 Handoff；有接管态或读取错误时封闭 Pending 执行链。"""
    tenant_id = str(state.get("tenant_id") or "").strip()
    conversation_id = str(state.get("conversation_id") or "").strip()
    if not tenant_id or not conversation_id:
        return _handoff_read_failed(pending_action)

    from backend.customer_service.handoff import HandoffState, should_intercept
    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    try:
        active = load_active_handoff_sync(
            tenant_id, conversation_id, raise_on_error=True,
        )
    except Exception:
        logger.warning(
            "[CS PendingHandler] 权威 Handoff 状态读取失败，停止 Pending 分流",
            exc_info=True,
        )
        return _handoff_read_failed(pending_action)

    if active is None:
        return None

    handoff_state = str(active.get("handoff_state") or "")
    try:
        state_value = HandoffState(handoff_state)
    except ValueError:
        logger.error("[CS PendingHandler] 未知 Handoff 状态=%r", handoff_state)
        return _handoff_read_failed(pending_action)
    if not should_intercept(state_value):
        return None

    answer = (
        "您好，当前已有客服人员正在处理您的问题，请耐心等待。"
        if state_value == HandoffState.HUMAN_ACTIVE
        else "您好，已为您转接人工客服，正在排队中，请稍候。"
    )
    return Command(goto="cs_reporter", update={
        "handoff_state": state_value.value,
        "pending_turn_decision": PendingTurnDecision.HANDOFF.value,
        "pending_action": pending_action,
        "supervisor_decision": {
            "next_action": "handoff", "decision_layer": 1,
            "reason": "pending_handler — PG 权威 Handoff 状态优先",
            "requires_handoff": True, "is_finished": True,
            "direct_reply": answer,
        },
        "last_expert_result": {"response_draft": answer},
    })


def _handoff_read_failed(pending_action: dict) -> Command:
    answer = "暂时无法核实人工客服处理状态，为避免重复办理，请稍后重试。"
    return Command(goto="cs_reporter", update={
        "pending_turn_decision": PendingTurnDecision.HANDOFF.value,
        "pending_action": pending_action,
        "supervisor_decision": {
            "next_action": "finish", "decision_layer": 1,
            "reason": "pending_handler — Handoff 权威状态不可用，fail closed",
            "direct_reply": answer, "is_finished": True,
        },
        "last_expert_result": {"response_draft": answer},
    })


def _allow_readonly_turn(
    pending_action: dict, decision: PendingTurnDecision, state: dict[str, Any],
) -> Command:
    return Command(goto="cs_supervisor", update={
        "pending_turn_decision": decision.value,
        "pending_turn_expert_consumed": False,
        "pending_turn_slot_fill": False,
        "handoff_state": state.get("handoff_state", "ai_active"),
        "handling_mode": state.get("handling_mode", "ai"),
        "pending_action": pending_action,
    })


def _pending_clarification(
    pending_action: dict, *, decision: PendingTurnDecision,
    state: dict[str, Any], need_info: bool = False,
) -> Command:
    proposal = str(pending_action.get("proposal_text") or "当前待处理的申请")
    if decision == PendingTurnDecision.NEW_WRITE_CONFLICT:
        answer = (
            f"您当前有一项待确认操作：{proposal}\n\n"
            "我先保留这项操作，暂不创建新的申请。请回复「确认」继续，"
            "回复「取消」放弃，或回复「转人工」由客服协助。"
        )
    elif need_info:
        missing = "、".join(pending_action.get("missing_slots") or []) or "所需信息"
        answer = f"我们还在处理当前申请，请补充{missing}；如果想了解其他问题，请直接说明。"
    else:
        answer = (
            f"我还不能确定您是在咨询问题还是确认这项操作：{proposal}\n\n"
            "请回复「确认」执行，回复「取消」放弃；如只是咨询，请补充说明。"
        )
    return Command(goto="cs_reporter", update={
        "pending_turn_decision": decision.value,
        "pending_action": pending_action,
        "supervisor_decision": {
            "next_action": "finish", "decision_layer": 2,
            "reason": f"pending_handler — {decision.value}，不进入业务执行",
            "direct_reply": answer, "is_finished": True,
        },
        "last_expert_result": {"response_draft": answer},
    })


def _looks_like_need_info_slot_answer(state: dict[str, Any]) -> bool:
    """只把明确的订单选择/标识当作 need_info 槽位回答。"""
    message = str(state.get("user_message") or "").strip()
    if re.fullmatch(r"第[一二两三四五六七八九十\d]+个?(?:订单|单)?", message):
        return True
    if re.fullmatch(
        r"(?:订单(?:号)?\s*[:：是]?\s*)?[A-Z]{1,5}[-_]?\d{3,}",
        message, re.I,
    ):
        return True
    return False


def _release_need_info_for_handoff(
    pending_action: dict, state: dict[str, Any],
) -> Command:
    """need_info 期间显式转人工：释放缺槽 pending，再由 HandoffExpert 接手。"""
    from backend.customer_service.audit import append_audit, build_audit_entry
    from backend.customer_service.confirmation_store import get_confirmation_store

    user_id = str(state.get("user_id", ""))
    session_id = str(state.get("session_id", ""))
    get_confirmation_store().clear(user_id, session_id, final_state="cancelled")
    audit_entry = build_audit_entry(
        user_id=user_id or "anonymous",
        action_type="need_info_handoff_escape",
        result="success",
        target_type="need_info",
        target_id=str(pending_action.get("intent", "")),
        detail="user requested human during slot-fill; pending released",
        conversation_id=state.get("conversation_id", ""),
    )
    logger.info(
        "[CS PendingHandler] need_info 转人工逃生: user=%s session=%s",
        user_id, session_id,
    )
    return Command(goto="cs_supervisor", update={
        "confirmation_state": "not_required",
        "pending_turn_decision": PendingTurnDecision.HANDOFF.value,
        "pending_turn_expert_consumed": False,
        "pending_turn_slot_fill": False,
        "pending_action": dict(pending_action, status="cancelled"),
        "cs_audit_entries": append_audit(
            list(state.get("cs_audit_entries") or []), audit_entry),
    })


def _cancel_need_info(
    pending_action: dict, state: dict[str, Any]
) -> Command:
    """need_info 阶段用户取消：释放 pending + 取消确认短路到 reporter。

    形态镜像 _process_pending 终态分支（finish 决策 + response_draft +
    审计同轮落库）；store.clear(final_state="cancelled") 与 proposal 阶段
    取消同终态口径（审计失真防线）。
    """
    from backend.customer_service.audit import append_audit, build_audit_entry
    from backend.customer_service.confirmation_store import get_confirmation_store

    user_id = str(state.get("user_id", ""))
    session_id = str(state.get("session_id", ""))
    get_confirmation_store().clear(user_id, session_id, final_state="cancelled")
    logger.info(
        "[CS PendingHandler] need_info cancelled by user: user=%s session=%s",
        user_id, session_id,
    )

    audit_entry = build_audit_entry(
        user_id=user_id or "anonymous",
        action_type="pending_cancelled",
        result="success",
        target_type="need_info",
        target_id=str(pending_action.get("intent", "")),
        detail="need_info pending cancelled by user in slot-fill stage",
        conversation_id=state.get("conversation_id", ""),
    )
    return Command(
        goto="cs_reporter",
        update={
            "confirmation_state": "not_required",
            "pending_turn_decision": PendingTurnDecision.CANCEL.value,
            "pending_action": dict(pending_action, status="cancelled"),
            "supervisor_decision": {
                "next_action": "finish",
                "decision_layer": 2,
                "reason": "pending_handler — need_info 阶段用户取消",
                "is_finished": True,
            },
            "last_expert_result": {
                "response_draft": "好的，已为您取消本次申请。如需再次办理，随时告诉我。",
            },
            "cs_audit_entries": append_audit([], audit_entry),
        },
    )


def _process_pending(
    pending_action: dict,
    user_message: str,
    user_id: str,
    session_id: str,
    state: dict[str, Any],
) -> Command:
    """处理 pending action — 全部状态流转委托 confirmation_flow（唯一实现）。"""
    from backend.customer_service.confirmation_flow import process_confirmation

    outcome = process_confirmation(
        pending_action, user_message, user_id, session_id,
        tenant_id=str(state.get("tenant_id") or ""),
        proposal_id=str(
            pending_action.get("proposal_id") or pending_action.get("action_id") or "",
        ),
        expected_version=int(
            pending_action.get("version") or pending_action.get("proposal_version") or 1,
        ),
    )

    update: dict[str, Any] = {
        "confirmation_state": outcome.confirmation_state,
        "pending_turn_decision": _classify_pending_turn(state).value,
    }

    if outcome.kind == "reask":
        update["supervisor_decision"] = {
            "next_action": "pending",
            "decision_layer": 2,
            "reason": (
                "pending_handler — 用户意图不明确，重新追问"
                f"（第 {outcome.pending_action.get('retry_count', 0)}"
                f" 次追问）"
            ),
        }
        update["last_expert_result"] = {"response_draft": outcome.answer}
        return Command(goto="cs_reporter", update=update)

    # 终态：expired / duplicate / success / failed / cancelled
    finished_reason = {
        "expired": "pending_handler — 确认超时",
        "duplicate": "pending_handler — 重复确认（已处理，幂等跳过）",
        "success": "pending_handler — 用户确认，执行成功",
        "failed": "pending_handler — 用户确认，执行失败",
        "cancelled": "pending_handler — 用户取消",
    }.get(outcome.kind, f"pending_handler — {outcome.kind}")

    update["supervisor_decision"] = {
        "next_action": "finish",
        "decision_layer": 2,
        "reason": finished_reason,
        "is_finished": True,
    }
    update["last_expert_result"] = {"response_draft": outcome.answer}

    if outcome.kind == "success" and outcome.action_result is not None:
        update["cs_action_result"] = outcome.action_result

    if outcome.audit_entry is not None:
        update["cs_audit_entries"] = [outcome.audit_entry]

    logger.info(
        "[CS PendingHandler] outcome=%s type=%s user=%s",
        outcome.kind, outcome.action_type, user_id,
    )
    return Command(goto="cs_reporter", update=update)
