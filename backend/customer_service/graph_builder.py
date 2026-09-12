"""
customer_service/graph_builder.py — CS Graph 构建器

独立于 Main Graph 的 CS Graph，内部由 Supervisor + Expert + Reporter 组成。
Phase 4: Supervisor 使用 Command(goto=...) 路由，替代 conditional edges。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7
"""
from __future__ import annotations

import threading
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.customer_service.experts.action import action_expert_node
from backend.customer_service.experts.complaint import complaint_expert_node
from backend.customer_service.experts.handoff import handoff_expert_node
from backend.customer_service.experts.knowledge import knowledge_expert_node
from backend.customer_service.experts.query import query_expert_node
from backend.customer_service.graph_state import (
    CS_ACTION_EXPERT,
    CS_COMPLAINT_EXPERT,
    CS_HANDOFF_EXPERT,
    CS_KNOWLEDGE_EXPERT,
    CS_PENDING_HANDLER,
    CS_QUERY_EXPERT,
    CS_REPORTER,
    CS_STATE_LOADER,
    CS_SUPERVISOR,
    CSGraphState,
)
from backend.customer_service.pending_handler import cs_pending_handler_node
from backend.customer_service.reporter import cs_reporter_node
from backend.customer_service.state_transition import get_state_transition_service
from backend.customer_service.supervisor import cs_supervisor_node
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

    Phase 5 拓扑 (Command 路由 + Pending Handler):
      START → cs_state_loader → cs_pending_handler
        → (无 pending) → cs_supervisor → Command(goto=...) → experts → cs_supervisor (loop)
                                                              → cs_reporter → END
        → (有 pending) → 处理确认/取消/超时 → cs_reporter → END
    """
    wf = StateGraph(CSGraphState)

    wf.add_node(CS_STATE_LOADER, cs_state_loader_node)
    wf.add_node(CS_PENDING_HANDLER, cs_pending_handler_node)
    wf.add_node(CS_SUPERVISOR, cs_supervisor_node)
    wf.add_node(CS_REPORTER, cs_reporter_node)

    wf.add_node(CS_KNOWLEDGE_EXPERT, knowledge_expert_node)
    wf.add_node(CS_QUERY_EXPERT, query_expert_node)
    wf.add_node(CS_ACTION_EXPERT, action_expert_node)
    wf.add_node(CS_COMPLAINT_EXPERT, complaint_expert_node)
    wf.add_node(CS_HANDOFF_EXPERT, handoff_expert_node)

    wf.add_edge(START, CS_STATE_LOADER)
    wf.add_edge(CS_STATE_LOADER, CS_PENDING_HANDLER)

    wf.add_edge(CS_KNOWLEDGE_EXPERT, CS_SUPERVISOR)
    wf.add_edge(CS_QUERY_EXPERT, CS_SUPERVISOR)
    wf.add_edge(CS_ACTION_EXPERT, CS_SUPERVISOR)
    wf.add_edge(CS_COMPLAINT_EXPERT, CS_SUPERVISOR)
    wf.add_edge(CS_HANDOFF_EXPERT, CS_SUPERVISOR)

    wf.add_edge(CS_REPORTER, END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    return wf.compile(**compile_kwargs)


_cs_graph_instance: Any = None
_cs_graph_lock = threading.Lock()


def get_cs_graph() -> Any:
    """获取 CS Graph 单例（double-checked locking）

    Phase 5: 当 CS_CHECKPOINTER_ENABLED=true 时注入 InMemorySaver checkpointer。
    """
    global _cs_graph_instance
    if _cs_graph_instance is None:
        with _cs_graph_lock:
            if _cs_graph_instance is None:
                checkpointer = _build_checkpointer()
                _cs_graph_instance = build_cs_graph(checkpointer=checkpointer)
    return _cs_graph_instance


def _build_checkpointer() -> Any:
    """根据 CS_CHECKPOINTER_ENABLED 构建 checkpointer。

    企业实践：会话状态持久化用 Postgres（跨进程/重启保留，多 worker 共享），
    MemorySaver 仅作初始化失败时的降级兜底。
    通过 CS_CHECKPOINTER_BACKEND=memory 可强制回退内存模式（本地调试用）。
    """
    from backend.config.customer_service import CS_CHECKPOINTER_ENABLED

    if not CS_CHECKPOINTER_ENABLED:
        return None

    from backend.config.customer_service import CS_CHECKPOINTER_BACKEND

    if CS_CHECKPOINTER_BACKEND == "postgres":
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver

            from backend.config.database import MEMORY_DB_CONFIG
            c = MEMORY_DB_CONFIG
            dsn = (f"postgresql://{c['user']}:{c['password']}"
                   f"@{c['host']}:{c['port']}/{c['dbname']}")
            # autocommit：checkpointer 写入需即时提交（官方建议）
            conn = psycopg.Connection.connect(dsn, autocommit=True)
            checkpointer = PostgresSaver(conn)
            checkpointer.setup()  # 首次建表（幂等）
            logger.info("[CS Graph] checkpointer enabled (PostgresSaver: %s/%s)",
                        c["host"], c["dbname"])
            return checkpointer
        except Exception:
            logger.warning("[CS Graph] PostgresSaver init failed, "
                           "falling back to MemorySaver", exc_info=True)

    try:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info("[CS Graph] checkpointer enabled (MemorySaver)")
        return MemorySaver()
    except Exception:
        logger.warning("[CS Graph] checkpointer init failed, running without")
        return None
