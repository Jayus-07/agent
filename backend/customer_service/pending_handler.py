"""customer_service/pending_handler.py — CS Pending Handler 节点

Phase 5: 多轮确认流程处理器。
位于 cs_state_loader 与 cs_supervisor 之间，拦截有 pending_action 的场景，
直接处理用户确认/取消/超时，无需经过 Supervisor → ActionExpert 链路。

拓扑:
  START → cs_state_loader → cs_pending_handler
    → (无 pending) → cs_supervisor
    → (有 pending)  → 处理确认/取消/超时 → cs_reporter

P1 重构（2026-09-17）：核心逻辑收敛到 confirmation_flow.process_confirmation
（与 ActionExpert 共用单一实现，含原子认领幂等闸门），本模块只做
Command 输出映射。

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

    # 缺陷6.3（2026-09-23）：need_info 型 pending（等待订单号补槽）不走
    # 确认流程 —— 转发 action expert 优先补槽；补到后原地生成 proposal，
    # 补不到按 retry 上限追问/释放。否则「MO-1001」会被当作确认意图
    # reask，或过期后落回 KB/RAG 拒答。
    if pending_action.get("status") == "need_info":
        # 设计方案 场景4（迁移 B6 实机验证补齐）：need_info 阶段用户说
        # 「算了/取消」→ 就地短路取消（与 proposal 阶段取消同层）——
        # 若转发 action expert 处理，取消后消息会回 supervisor 再路由，
        # 低置信时被兜底话术覆盖取消确认（B6 实机复现）。复用 confirmation
        # 单一取消词表源（含疑问句保护）。
        from backend.customer_service.confirmation import (
            ConfirmationIntent,
            detect_confirmation_intent,
        )

        if detect_confirmation_intent(
            str(state.get("user_message", "")),
        ) == ConfirmationIntent.CANCEL:
            return _cancel_need_info(pending_action, state)

        # 转人工逃生（2026-10-05 浏览器走查发现）：need_info 追问期用户
        # 显式转人工必须立即放行——紧急通道优先于补槽追问，否则用户被
        # 追问循环卡死（实机复现：抽屉内「转人工」被问订单号吞掉）。
        # 释放 need_info pending（同取消的终态口径）后回 supervisor 重分诊，
        # 由 handoff 触发链承接。
        from backend.customer_service.handoff import detect_handoff_trigger

        if detect_handoff_trigger(str(state.get("user_message", ""))):
            from backend.customer_service.audit import (
                append_audit,
                build_audit_entry,
            )
            from backend.customer_service.confirmation_store import (
                get_confirmation_store,
            )

            user_id_e = str(state.get("user_id", ""))
            session_id_e = str(state.get("session_id", ""))
            get_confirmation_store().clear(
                user_id_e, session_id_e, final_state="cancelled")
            logger.info(
                "[CS PendingHandler] need_info 转人工逃生: user=%s session=%s",
                user_id_e, session_id_e,
            )
            audit_entry = build_audit_entry(
                user_id=user_id_e or "anonymous",
                action_type="need_info_handoff_escape",
                result="success",
                target_type="need_info",
                target_id=str(pending_action.get("intent", "")),
                detail="user requested human during slot-fill; pending released",
                conversation_id=state.get("conversation_id", ""),
            )
            return Command(goto="cs_supervisor", update={
                "cs_audit_entries": append_audit(
                    list(state.get("cs_audit_entries") or []), audit_entry),
            })

        confirmation_state = state.get("confirmation_state", "")
        if confirmation_state in _PENDING_STATES:
            return Command(goto="cs_action_expert", update={})
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
        pending_action, user_message, user_id, session_id,
    )


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
) -> Command:
    """处理 pending action — 全部状态流转委托 confirmation_flow（唯一实现）。"""
    from backend.customer_service.confirmation_flow import process_confirmation

    outcome = process_confirmation(
        pending_action, user_message, user_id, session_id,
    )

    update: dict[str, Any] = {
        "confirmation_state": outcome.confirmation_state,
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
