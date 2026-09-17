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
