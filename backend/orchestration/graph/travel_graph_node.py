"""orchestration/graph/travel_graph_node.py — Main Graph ↔ 旅游域图 适配器

与 cs_graph_node 同一职责，只做状态转换，不含业务逻辑：
  1. OrchestratorState → 旅游域图输入（new_travel_graph_input）
  2. 调用 get_travel_graph().invoke(...)
  3. TravelGraphResult → Main State 字段映射
异常一律降级为兜底回复，绝不把主图带崩 —— 与客服域同一条安全带。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.graph_builder import get_travel_graph
from backend.travel.graph_state import new_travel_graph_input
from backend.travel.models.graph_result import build_travel_graph_result

_FALLBACK_ANSWER = "抱歉，旅游规划服务暂时不可用，请稍后再试。"


def travel_graph_node(state: dict) -> dict:
    """Main Graph → 旅游域图 → Main Graph 适配器"""
    travel_context = state.get("travel_context") or {}
    session_id = state.get("session_id", "")
    conversation_id = travel_context.get("conversation_id") or session_id

    graph_input = new_travel_graph_input(
        user_message=state.get("question") or state.get("query") or "",
        user_id=state.get("user_id", ""),
        session_id=session_id,
        conversation_id=conversation_id,
        travel_route=travel_context.get("travel_route") or {},
    )

    try:
        final_state = get_travel_graph().invoke(
            graph_input, config=_build_invoke_config(conversation_id),
        )
        result = build_travel_graph_result(final_state)
    except Exception:
        logger.exception("[travel_graph_node] 旅游域图执行异常，降级返回兜底回复")
        return _fallback_update(state)

    _stamp_execution_tags(final_state, result)
    return _build_main_state_update(result)


def _build_main_state_update(result: dict) -> dict:
    """TravelGraphResult → Main State 字段更新。

    只写两个字段：final_answer 与 travel_context。旅游域大对象（itinerary）
    放在 travel_context 里，避免污染主状态、也避免 checkpointer 把整份行程
    反复序列化。
    """
    return {
        "final_answer": result.get("final_answer") or _FALLBACK_ANSWER,
        "travel_context": result.get("travel_context") or {},
    }


def _fallback_update(state: dict) -> dict:
    original = state.get("travel_context") or {}
    return {
        "final_answer": _FALLBACK_ANSWER,
        "travel_context": {
            "conversation_id": original.get("conversation_id", ""),
            "travel_route": original.get("travel_route", {}),
        },
    }


def _stamp_execution_tags(final_state: dict, result: dict) -> None:
    """把执行结果写进 trace tags —— 旅游域的质量指标数据源。

    status / 校验码 / 修复轮数 / 置信度都打标，后续做「约束违反率」
    「一次通过率」「平均修复轮数」时不需要再回头改埋点。埋点软失败。
    """
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is None:
            return
        trace.tags["travel_status"] = result.get("status", "")
        brief = final_state.get("brief") or {}
        if brief.get("destination"):
            trace.tags["travel_destination"] = brief["destination"]

        validation = final_state.get("validation") or {}
        codes = [v.get("code", "") for v in validation.get("violations", [])]
        trace.metadata["travel_validation"] = {
            "codes": list(dict.fromkeys(c for c in codes if c)),
            "errors": sum(1 for v in validation.get("violations", [])
                          if v.get("level") == "error"),
            "warnings": sum(1 for v in validation.get("violations", [])
                            if v.get("level") == "warning"),
            "repair_rounds": final_state.get("repair_rounds", 0),
            "steps": final_state.get("step_count", 0),
        }
        itinerary = final_state.get("itinerary") or {}
        if itinerary:
            trace.metadata["travel_confidence"] = itinerary.get("confidence")
    except Exception:
        logger.debug("[travel_graph_node] 执行标签写入失败", exc_info=True)


def _build_invoke_config(conversation_id: str) -> dict:
    """构建域图 invoke config。

    thread_id 始终给出：checkpointer 开启时 LangGraph 强制要求，缺失会直接
    抛错。无会话标识时用一次性 id，避免不同请求共享同一份 checkpoint
    （共享比报错更危险 —— 用户会看到别人的行程）。
    """
    from uuid import uuid4

    from backend.config.travel import TRAVEL_GRAPH_RECURSION_LIMIT

    return {
        "recursion_limit": TRAVEL_GRAPH_RECURSION_LIMIT,
        "configurable": {
            "thread_id": conversation_id or f"travel-{uuid4().hex}",
        },
    }
