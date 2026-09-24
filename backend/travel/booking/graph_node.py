"""travel/booking/graph_node.py — Main Graph ↔ Booking Graph 适配器（STOP L9）

与 cs/travel/commerce 图适配器同职责：仅 state 转换。无 checkpointer
（订单事实在 PG）；tenant/user 从主图 state 显式传入（认证边界已解析）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.booking.graph_builder import get_booking_graph
from backend.travel.booking.graph_state import new_booking_graph_input

_FALLBACK_ANSWER = "预订服务暂时不可用，请稍后再试。"


def travel_booking_graph_node(state: dict) -> dict:
    try:
        final_state = get_booking_graph().invoke(new_booking_graph_input(
            user_message=state.get("question", ""),
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
            tenant_id=state.get("tenant_id", ""),
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
    return {"final_answer": final_state.get("final_answer", "")}
