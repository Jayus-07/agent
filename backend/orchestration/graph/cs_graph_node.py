"""
orchestration/graph/cs_graph_node.py — Main Graph ↔ CS Graph 适配器

职责 (仅 State 转换，不含业务逻辑):
  1. 从 CSAgentState 构建 CS Graph 输入 (new_cs_graph_input)
  2. 调用 get_cs_graph().invoke(cs_input)
  3. 通过 build_cs_graph_result() 将 CSGraphState → CSGraphResult
  4. 将 CSGraphResult 映射回 Main State 字段

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7.3
"""
from __future__ import annotations

from backend.customer_service.graph_builder import get_cs_graph
from backend.customer_service.graph_state import new_cs_graph_input
from backend.customer_service.models.graph_result import build_cs_graph_result
from backend.shared.logger import logger

_FALLBACK_ANSWER = "抱歉，客服系统暂时不可用，请稍后再试。"


def cs_graph_node(state: dict) -> dict:
    """Main Graph → CS Graph → Main Graph 适配器

    输入: CSAgentState (Main Graph 状态)
    输出: dict — 写回 Main State 的字段子集
    """
    cs_context = state.get("cs_context", {})

    cs_input = new_cs_graph_input(
        user_message=state.get("question", ""),
        user_id=cs_context.get("authenticated_user_id", ""),
        session_id=cs_context.get("session_id", ""),
        conversation_id=cs_context.get("conversation_id", ""),
        cs_route=cs_context.get("cs_route", {}),
    )

    try:
        graph = get_cs_graph()
        final_state = graph.invoke(cs_input)
        result = build_cs_graph_result(final_state)
    except Exception:
        logger.exception("[cs_graph_node] CS Graph 执行异常，降级返回兜底回复")
        return _fallback_update(state)

    return _build_main_state_update(state, result)


def _build_main_state_update(original_state: dict, result: dict) -> dict:
    """将 CSGraphResult 映射为 Main State 字段更新

    cs_context 合并策略:
    - 保留: authenticated_user_id, session_id, cs_target (来自原始 state)
    - 覆盖: conversation_id, handoff_state, confirmation_state (来自 CS Graph)
    """
    original_ctx = original_state.get("cs_context", {})

    merged_context = {
        "authenticated_user_id": original_ctx.get("authenticated_user_id"),
        "session_id": original_ctx.get("session_id"),
        "cs_target": original_ctx.get("cs_target"),
        "conversation_id": result.get("conversation_id", ""),
        "handoff_state": result.get("handoff_state"),
        "confirmation_state": result.get("confirmation_state"),
    }

    return {
        "final_answer": result.get("final_answer", _FALLBACK_ANSWER),
        "cs_context": merged_context,
        "cs_action_result": result.get("action_result") or {},
        "cs_audit_entries": result.get("audit_entries", []),
    }


def _fallback_update(state: dict) -> dict:
    """CS Graph 异常时的安全网 — 不崩溃 Main Graph"""
    original_ctx = state.get("cs_context", {})
    return {
        "final_answer": _FALLBACK_ANSWER,
        "cs_context": {
            "authenticated_user_id": original_ctx.get("authenticated_user_id"),
            "session_id": original_ctx.get("session_id"),
            "cs_target": original_ctx.get("cs_target"),
        },
        "cs_action_result": {},
        "cs_audit_entries": [],
    }
