"""
orchestration/graph/cs_graph_node.py — Main Graph ↔ CS Graph 适配器

职责 (仅 State 转换，不含业务逻辑):
  1. 从 OrchestratorState 构建 CS Graph 输入 (new_cs_graph_input)
  2. 调用 get_cs_graph().invoke(cs_input)
  3. 通过 build_cs_graph_result() 将 CSGraphState → CSGraphResult
  4. 将 CSGraphResult 映射回 Main State 字段

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7.3
"""
from __future__ import annotations

import re

from backend.customer_service.context import merge_cs_context
from backend.customer_service.graph_builder import get_cs_graph
from backend.customer_service.graph_state import new_cs_graph_input
from backend.customer_service.models.graph_result import build_cs_graph_result
from backend.shared.logger import logger

_FALLBACK_ANSWER = "抱歉，客服系统暂时不可用，请稍后再试。"


def cs_graph_node(state: dict) -> dict:
    """Main Graph → CS Graph → Main Graph 适配器

    输入: OrchestratorState (Main Graph 状态)
    输出: dict — 写回 Main State 的字段子集
    """
    cs_context = state.get("cs_context", {})
    conversation_id = cs_context.get("conversation_id", "")

    cs_input = new_cs_graph_input(
        user_message=state.get("question", ""),
        user_id=cs_context.get("authenticated_user_id", ""),
        session_id=cs_context.get("session_id", ""),
        conversation_id=conversation_id,
        cs_route=cs_context.get("cs_route", {}),
    )

    try:
        graph = get_cs_graph()
        invoke_config = _build_invoke_config(conversation_id)
        final_state = graph.invoke(cs_input, config=invoke_config)
        result = build_cs_graph_result(final_state)
    except Exception:
        logger.exception("[cs_graph_node] CS Graph 执行异常，降级返回兜底回复")
        return _fallback_update(state)

    _stamp_execution_tags(final_state)
    return _build_main_state_update(state, result)


def _stamp_execution_tags(final_state: dict) -> None:
    """把 CS Graph 内部执行结果写进 trace tags（路由一致率/转人工率数据源）。

    - cs_expert_final: supervisor 最终派发的 expert（与 tags.cs_target 对照）
    - cs_expert_visited: 会话中访问过的全部 expert（路由翻转分析）
    - cs_handoff_state: 最终 handoff 状态（转人工率）
    """
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is None:
            return
        visited = [
            (e.get("node") if isinstance(e, dict) else str(e))
            for e in (final_state.get("expert_history") or [])
        ]
        final_expert = final_state.get("current_expert") or (
            visited[-1] if visited else ""
        )
        if final_expert:
            trace.tags["cs_expert_final"] = final_expert
        if visited:
            trace.tags["cs_expert_visited"] = ",".join(dict.fromkeys(visited))
        handoff = final_state.get("handoff_state") or ""
        if handoff:
            trace.tags["cs_handoff_state"] = handoff
            # 转人工 trigger 序列 → metadata（转人工率三桶拆分的数据源；
            # audit entries 本身不进 trace record，故在此提取）
            triggers = []
            for entry in final_state.get("cs_audit_entries") or []:
                detail = (entry or {}).get("detail", "") if isinstance(entry, dict) else ""
                m = re.search(r"trigger=([a-z_]+)", detail or "")
                if m:
                    triggers.append(m.group(1))
            if triggers:
                trace.metadata["cs_handoff_triggers"] = triggers
    except Exception:
        logger.debug("[cs_graph_node] 执行标签写入失败", exc_info=True)


def _build_main_state_update(original_state: dict, result: dict) -> dict:
    """将 CSGraphResult 映射为 Main State 字段更新

    cs_context 合并策略:
    - 保留: authenticated_user_id, session_id, cs_target (来自原始 state)
    - 覆盖: conversation_id, handoff_state, confirmation_state (来自 CS Graph)
    """
    original_ctx = original_state.get("cs_context", {})

    return {
        "final_answer": result.get("final_answer", _FALLBACK_ANSWER),
        "cs_context": merge_cs_context(original_ctx, result),
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


def _build_invoke_config(conversation_id: str) -> dict:
    """构建 CS Graph invoke config。

    Phase 5: 当 checkpointer 启用时，thread_id 用于多轮对话状态持久化。
    """
    from backend.config.customer_service import CS_GRAPH_RECURSION_LIMIT

    config: dict = {
        "recursion_limit": CS_GRAPH_RECURSION_LIMIT,
    }
    if conversation_id:
        config["configurable"] = {"thread_id": conversation_id}
    return config
