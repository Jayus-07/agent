"""travel/commerce/graph_node.py — Main Graph ↔ Commerce Graph 适配器（STOP K6）

与 cs_graph_node/travel_graph_node 同职责：**仅 state 转换**，不含业务逻辑。
无 checkpointer / 无 resume 语义（单发查询）；tenant/user 仅随 trace 传播
（缓存键与结果不携带身份——K0 §10 租户隔离依据）。

Phase 5 / D2 增补：**跨轮挂起读写在本层**（与 booking 适配器同款）。子图无
checkpointer，澄清期的参数（「在等入住/退房日期」）不在 PG —— 没人记住时，
用户答「10月3日到5日」会掉域重说。挂起由 ConversationContext 承载，读写
都在适配器完成，子图保持「零 IO、纯计算」纪律不变。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.commerce.extract import REQUIRED_SLOTS_BY_KIND
from backend.travel.commerce.graph_builder import get_commerce_graph
from backend.travel.commerce.graph_state import new_commerce_graph_input

_FALLBACK_ANSWER = (
    "酒店/机票查询服务暂时不可用，请稍后再试。（已如实停止，"
    "不会提供未经核实的库存信息）"
)
_DOMAIN = "travel_commerce"


def travel_commerce_graph_node(state: dict) -> dict:
    """Main Graph → Commerce Graph → Main Graph 适配器。"""
    conversation_id = state.get("session_id", "")
    pending = _read_pending(state, conversation_id)
    commerce_input = new_commerce_graph_input(
        user_message=state.get("question", ""),
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
        conversation_id=conversation_id,
        tenant_id=state.get("tenant_id", ""),
        pending_intent=pending,
    )
    try:
        final_state = get_commerce_graph().invoke(commerce_input)
    except Exception:
        # 主链保护（G19）：Commerce 任何异常不得 500 穿透主图
        logger.exception("[travel_commerce_graph_node] 执行异常，降级兜底")
        return {"final_answer": _FALLBACK_ANSWER}

    _stamp_execution_tags(final_state, state)
    _write_pending(state, final_state, conversation_id, pending)
    return {
        "final_answer": final_state.get("final_answer", ""),
        "commerce_status": final_state.get("commerce_status", ""),
        "commerce_offer_count": final_state.get("commerce_offer_count", 0),
    }


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
        # 只接本域挂起：预订挂起（travel_booking）不由比价子图消费
        return intent if intent.get("route_mode") == _DOMAIN else {}
    except Exception:
        logger.debug("[travel_commerce_graph_node] 挂起读取失败（软降级）",
                     exc_info=True)
        return {}


def _write_pending(state: dict, final_state: dict, conversation_id: str,
                   pending_in: dict) -> None:
    """执行结果 → 挂起与路由上下文（软失败，绝不影响主链）。

    - ``clarify`` 且 kind 合法：写/更新挂起（collected 累计）+ pending_question；
    - 其他终态（success/empty/disabled/error）：本域已收尾 → 清挂起
      （question_id CAS）并清 pending_question。

    ``commerce_missing == ["intent"]``（图内兜底：未识别到意图）**不写挂起**
    —— 那不是「在等某个槽位」，写进去会把用户下一句正常提问误拉回本域。
    """
    if not conversation_id:
        return
    status = final_state.get("commerce_status") or ""
    kind = final_state.get("commerce_type") or ""
    missing = list(final_state.get("commerce_missing") or [])
    collected = dict(final_state.get("commerce_collected") or {})
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
        if (status == "clarify" and kind in REQUIRED_SLOTS_BY_KIND
                and missing and "intent" not in missing):
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
        logger.debug("[travel_commerce_graph_node] 挂起回写失败（软降级）",
                     exc_info=True)


def _stamp_execution_tags(final_state: dict, main_state: dict) -> None:
    """运行事实入 trace（低基数标签；无 query/城市原文，§99 红线）。"""
    try:
        from backend.observability.tracer import trace_collector

        t = trace_collector.current()
        if t is not None:
            t.tags["commerce.type"] = final_state.get("commerce_type", "")
            t.tags["commerce.status"] = final_state.get("commerce_status", "")
            t.tags["commerce.session_id"] = main_state.get("session_id", "")
    except Exception:
        logger.debug("[travel_commerce_graph_node] trace 标签写入失败",
                     exc_info=True)
