"""
customer_service/graph_builder.py — CS Graph 构建器

独立于 Main Graph 的 CS Graph，内部由 Supervisor + Expert + Reporter 组成。
Phase 0: 线性拓扑 (state_loader → supervisor → reporter → END)
Phase 2+: 逐步添加 Expert 节点 + conditional edges

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7
"""
from __future__ import annotations

import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.customer_service.graph_state import (
    CSGraphState,
    CS_REPORTER,
    CS_STATE_LOADER,
    CS_SUPERVISOR,
)
from backend.customer_service.reporter import cs_reporter_node
from backend.customer_service.state_transition import get_state_transition_service
from backend.customer_service.supervisor import cs_supervisor_node, route_after_cs_supervisor
from backend.shared.logger import logger


def cs_state_loader_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph 入口节点: 从 PostgreSQL 加载业务状态快照

    职责:
    1. 调用 StateTransitionService.load_snapshot() 获取当前状态
    2. 填充 CSGraphState 中的状态快照字段
    3. 重置执行态字段（防止 Checkpoint 残留影响）
    """
    user_id = state.get("user_id", "")
    session_id = state.get("session_id", "")
    conversation_id = state.get("conversation_id", "")

    snapshot = get_state_transition_service().load_snapshot(
        user_id, session_id, conversation_id,
    )

    logger.debug(
        "[CS StateLoader] loaded snapshot: conv=%s handoff=%s conf=%s",
        snapshot.get("conversation_status"),
        snapshot.get("handoff_state"),
        snapshot.get("confirmation_state"),
    )

    return {
        "conversation_status": snapshot.get("conversation_status", "open"),
        "handling_mode": snapshot.get("handling_mode", "ai"),
        "handoff_state": snapshot.get("handoff_state", "ai_active"),
        "confirmation_state": snapshot.get("confirmation_state", "not_required"),
        "pending_action": snapshot.get("pending_action"),
        "expert_history": [],
        "last_expert_result": {},
        "expert_loop_count": 0,
        "supervisor_decision": {},
        "current_expert": "",
    }


def build_cs_graph(checkpointer: Any = None) -> StateGraph:
    """构建 CS Graph

    Phase 0 拓扑:
      START → cs_state_loader → cs_supervisor → cs_reporter → END

    Phase 2+ 逐步添加 Expert 节点和 conditional edges。
    """
    wf = StateGraph(CSGraphState)

    wf.add_node(CS_STATE_LOADER, cs_state_loader_node)
    wf.add_node(CS_SUPERVISOR, cs_supervisor_node)
    wf.add_node(CS_REPORTER, cs_reporter_node)

    wf.add_edge(START, CS_STATE_LOADER)
    wf.add_edge(CS_STATE_LOADER, CS_SUPERVISOR)
    wf.add_conditional_edges(
        CS_SUPERVISOR,
        route_after_cs_supervisor,
        {
            CS_REPORTER: CS_REPORTER,
        },
    )
    wf.add_edge(CS_REPORTER, END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    return wf.compile(**compile_kwargs)


_cs_graph_instance: Any = None
_cs_graph_lock = threading.Lock()


def get_cs_graph() -> Any:
    """获取 CS Graph 单例（double-checked locking）"""
    global _cs_graph_instance
    if _cs_graph_instance is None:
        with _cs_graph_lock:
            if _cs_graph_instance is None:
                _cs_graph_instance = build_cs_graph()
    return _cs_graph_instance
