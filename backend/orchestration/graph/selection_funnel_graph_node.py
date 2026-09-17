"""orchestration/graph/selection_funnel_graph_node.py — 主图 ↔ 漏斗域适配器

与 travel_graph_node / cs_graph_node 同一职责：只做状态转换，不含业务逻辑。
异常一律降级为兜底回复，绝不把主图带崩。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.selection_funnel.graph_builder import get_selection_funnel_graph
from backend.selection_funnel.graph_state import new_selection_funnel_graph_input
from backend.selection_funnel.models.funnel_result import build_funnel_result

_FALLBACK_ANSWER = "抱歉，智能选品服务暂时不可用，请稍后再试。"


def selection_funnel_graph_node(state: dict) -> dict:
    """Main Graph → 漏斗域图 → Main Graph 适配器"""
    funnel_context = state.get("funnel_context") or {}
    session_id = state.get("session_id", "")

    graph_input = new_selection_funnel_graph_input(
        user_message=state.get("question") or state.get("query") or "",
        user_id=state.get("user_id", ""),
        session_id=session_id,
        conversation_id=funnel_context.get("conversation_id") or session_id,
        funnel_context={
            "category": funnel_context.get("category") or "",
            "platform": funnel_context.get("platform") or "",
        },
    )

    try:
        from backend.config.selection_funnel import (
            SELECTION_FUNNEL_GRAPH_RECURSION_LIMIT,
        )
        final_state = get_selection_funnel_graph().invoke(
            graph_input,
            config={"recursion_limit": SELECTION_FUNNEL_GRAPH_RECURSION_LIMIT},
        )
        result = build_funnel_result(final_state)
    except Exception:
        logger.exception("[selection_funnel_graph_node] 漏斗域图执行异常，降级返回兜底回复")
        return {
            "final_answer": _FALLBACK_ANSWER,
            "funnel_context": funnel_context or {},
        }

    _stamp_execution_tags(final_state, result)
    return {
        "final_answer": result.get("final_answer") or _FALLBACK_ANSWER,
        "funnel_context": result.get("funnel_context") or {},
    }


def _stamp_execution_tags(final_state: dict, result: dict) -> None:
    """漏斗质量指标埋点（软失败）：各层留存数 / 终态，供后续漏斗转化率统计。"""
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is None:
            return
        trace.tags["funnel_status"] = result.get("status", "")
        brief = final_state.get("brief") or {}
        if brief.get("category"):
            trace.tags["funnel_category"] = brief["category"]
        trace.metadata["funnel_stage_summary"] = (
            result.get("funnel_context") or {}).get("stage_summary", [])
    except Exception:
        logger.debug("[selection_funnel_graph_node] 执行标签写入失败", exc_info=True)
