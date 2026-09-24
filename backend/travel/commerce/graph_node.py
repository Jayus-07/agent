"""travel/commerce/graph_node.py — Main Graph ↔ Commerce Graph 适配器（STOP K6）

与 cs_graph_node/travel_graph_node 同职责：**仅 state 转换**，不含业务逻辑。
无 checkpointer / 无 resume 语义（单发查询）；tenant/user 仅随 trace 传播
（缓存键与结果不携带身份——K0 §10 租户隔离依据）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.commerce.graph_builder import get_commerce_graph
from backend.travel.commerce.graph_state import new_commerce_graph_input

_FALLBACK_ANSWER = (
    "酒店/机票查询服务暂时不可用，请稍后再试。（已如实停止，"
    "不会提供未经核实的库存信息）"
)


def travel_commerce_graph_node(state: dict) -> dict:
    """Main Graph → Commerce Graph → Main Graph 适配器。"""
    commerce_input = new_commerce_graph_input(
        user_message=state.get("question", ""),
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
        conversation_id=state.get("session_id", ""),
        tenant_id=state.get("tenant_id", ""),
    )
    try:
        final_state = get_commerce_graph().invoke(commerce_input)
    except Exception:
        # 主链保护（G19）：Commerce 任何异常不得 500 穿透主图
        logger.exception("[travel_commerce_graph_node] 执行异常，降级兜底")
        return {"final_answer": _FALLBACK_ANSWER}

    _stamp_execution_tags(final_state, state)
    return {
        "final_answer": final_state.get("final_answer", ""),
        "commerce_status": final_state.get("commerce_status", ""),
        "commerce_offer_count": final_state.get("commerce_offer_count", 0),
    }


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
