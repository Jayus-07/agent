"""
customer_service/models/graph_result.py — CS Graph 输出契约

CS Graph 通过此契约与 Main Graph 通信，解耦两个图的内部状态结构。
cs_graph_node 适配器负责将 CSGraphState → CSGraphResult → Main State 转换。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7.4
"""
from __future__ import annotations

from typing import Any, TypedDict


class CSGraphResult(TypedDict, total=False):
    """CS Graph 输出契约

    Main Graph 只依赖此结构，不直接访问 CSGraphState。
    status 字段驱动 Main Graph 的后续行为（如 needs_handoff 触发降级）。
    """

    final_answer: str
    conversation_id: str
    action_result: dict | None
    answer_meta: dict | None
    handoff_state: str | None
    confirmation_state: str | None
    pending_action: dict | None
    audit_entries: list[dict]
    status: str


def build_cs_graph_result(final_state: dict[str, Any]) -> CSGraphResult:
    """从 CS Graph 最终状态构建输出契约

    Args:
        final_state: CS Graph 执行完毕后的 CSGraphState

    Returns:
        CSGraphResult — 适配器将其映射回 Main State
    """
    status = _derive_status(final_state)

    return CSGraphResult(
        final_answer=final_state.get("final_answer", ""),
        conversation_id=final_state.get("conversation_id", ""),
        action_result=final_state.get("cs_action_result") or None,
        answer_meta=_build_answer_meta(final_state),
        handoff_state=final_state.get("handoff_state") or None,
        confirmation_state=final_state.get("confirmation_state") or None,
        # P3.1：pending 透传给 Main State → SSE done 帧 → 前端 CSConfirmCard。
        # 仅在等待确认（pending 态）时有值；终态（success/failed/expired/cancelled）为 None。
        pending_action=(
            final_state.get("pending_action")
            if final_state.get("confirmation_state")
            in ("pending", "pending_confirmation")
            else None
        ),
        audit_entries=final_state.get("cs_audit_entries", []),
        status=status,
    )


def _derive_status(final_state: dict[str, Any]) -> str:
    """从最终状态推导 CSGraphResult.status

    优先级:
    1. handoff_state 非 ai_active → needs_handoff
    2. final_answer 非空 → success
    3. 其他 → failed
    """
    handoff_state = final_state.get("handoff_state", "")
    if handoff_state and handoff_state not in ("ai_active", ""):
        return "needs_handoff"

    if final_state.get("final_answer"):
        return "success"

    return "failed"


def _build_answer_meta(final_state: dict[str, Any]) -> dict | None:
    """构建回复元数据（evidence 等）

    当前从 last_expert_result 中提取 evidence 字段。
    """
    expert_result = final_state.get("last_expert_result", {})
    evidence = expert_result.get("evidence")
    if evidence:
        return {"evidence": evidence}
    return None
