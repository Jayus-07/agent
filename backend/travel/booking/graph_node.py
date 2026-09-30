"""travel/booking/graph_node.py — Main Graph ↔ Booking Graph 适配器（STOP L9）

与 cs/travel/commerce 图适配器同职责：仅 state 转换。无 checkpointer
（订单事实在 PG）；tenant/user 从主图 state 显式传入（认证边界已解析）。

Phase 5 / D2 增补：**跨轮挂起读写在本层**。子图无 checkpointer，澄清期的
参数（「在等入住日期」）不在 PG —— 没人记住时用户答「10月3日到5日」会掉域
（实测 D2）。挂起由 ConversationContext 承载，读与回写都在适配器完成，
子图保持「零 IO、纯计算」纪律不变。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.booking.graph_builder import get_booking_graph
from backend.travel.booking.graph_state import new_booking_graph_input

_FALLBACK_ANSWER = "预订服务暂时不可用，请稍后再试。"
_DOMAIN = "travel_booking"


def travel_booking_graph_node(state: dict) -> dict:
    conversation_id = state.get("session_id", "")
    pending = _read_pending(state, conversation_id)
    try:
        final_state = get_booking_graph().invoke(new_booking_graph_input(
            user_message=state.get("question", ""),
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
            tenant_id=state.get("tenant_id", ""),
            pending_intent=pending,
        ))
    except Exception:
        # G19：booking 任何异常不得 500 穿透主图
        logger.exception("[travel_booking_graph_node] 执行异常，降级兜底")
        return {"final_answer": _FALLBACK_ANSWER}

    try:
        from backend.observability.tracer import trace_collector

        t = trace_collector.current()
        if t is not None:
            t.tags["booking.status"] = final_state.get("booking_status", "")
    except Exception:
        logger.debug("[travel_booking_graph_node] trace 标签写入失败",
                     exc_info=True)
    _write_pending(state, final_state, conversation_id, pending)
    return {"final_answer": final_state.get("final_answer", "")}


def _read_pending(state: dict, conversation_id: str) -> dict:
    """读本域挂起（只读 peek；未命中/他域挂起/异常 → 空 dict）。"""
    if not conversation_id:
        return {}
    try:
        from backend.orchestration.context.context_repository import (
            get_conversation_context_repository,
        )

        snap = get_conversation_context_repository().peek(
            state.get("tenant_id") or "", state.get("user_id") or "",
            conversation_id)
        intent = (snap.booking_intent if snap else None) or {}
        # 只接本域挂起：比价挂起（travel_commerce）不由预订子图消费
        return intent if intent.get("route_mode") == _DOMAIN else {}
    except Exception:
        logger.debug("[travel_booking_graph_node] 挂起读取失败（软降级）",
                     exc_info=True)
        return {}


def _write_pending(state: dict, final_state: dict, conversation_id: str,
                   pending_in: dict) -> None:
    """执行结果 → 挂起与路由上下文（软失败，绝不影响主链）。

    - ``clarify``（参数未齐）：写/更新挂起（collected 累计），pending_question
      留追问文案供下一轮 Context Assembler 可见；
    - 其他终态（quoted/booked/status/error/off）：本域已收尾 → 清挂起
      （question_id CAS，避免清掉并发写入的新挂起）并清 pending_question。

    ``mark_domain_turn`` 与 ``_mark_route_from_update``（prefilter 命中回写）
    共用同一会话键口径（``session_id``）——两处必须一致，否则下一轮
    assemble_routing_context 读不到挂起。
    """
    if not conversation_id:
        return
    status = final_state.get("booking_status") or ""
    kind = final_state.get("booking_kind") or ""
    missing = list(final_state.get("booking_missing") or [])
    collected = dict(final_state.get("booking_collected") or {})
    tid = state.get("tenant_id") or ""
    uid = state.get("user_id") or ""
    try:
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
            get_conversation_context_repository,
        )
        from backend.orchestration.context.routing_context import (
            mark_domain_turn,
        )

        repo = get_conversation_context_repository()
        if status == "clarify" and kind and missing:
            repo.mutate(tid, uid, conversation_id, ContextMutation(
                MutationType.SET_BOOKING_INTENT, {
                    "route_mode": _DOMAIN, "kind": kind,
                    "missing_slots": missing, "collected": collected,
                }))
            mark_domain_turn(
                tid, uid, conversation_id, domain=_DOMAIN, intent=_DOMAIN,
                action="clarify",
                pending_question=final_state.get("final_answer") or "")
            return
        # 收尾：清挂起（CAS 用本轮读到的 question_id）
        expected_q = pending_in.get("question_id") or None
        repo.mutate(tid, uid, conversation_id, ContextMutation(
            MutationType.RESOLVE_BOOKING_INTENT,
            {"expected_question_id": expected_q} if expected_q else {}))
        mark_domain_turn(
            tid, uid, conversation_id, domain=_DOMAIN, intent=_DOMAIN,
            action=status or "done", pending_question="")
    except Exception:
        logger.debug("[travel_booking_graph_node] 挂起回写失败（软降级）",
                     exc_info=True)
