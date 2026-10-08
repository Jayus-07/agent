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

from backend.config.checkpointer import degrade_or_raise
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
from backend.customer_service.trace import wrap_cs_node
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

    _register_request_pins(snapshot, state, user_id, session_id)

    return {
        "conversation_status": snapshot.get("conversation_status", "open"),
        "handling_mode": snapshot.get("handling_mode", "ai"),
        "handoff_state": snapshot.get("handoff_state", "ai_active"),
        "confirmation_state": snapshot.get("confirmation_state", "not_required"),
        "pending_action": snapshot.get("pending_action"),
        # 只读分流是单轮策略，不得沿 checkpoint 泄漏到下一条用户消息。
        "pending_turn_decision": None,
        "pending_turn_expert_consumed": False,
        "pending_turn_slot_fill": False,
        "expert_history": [],
        "last_expert_result": {},
        "expert_loop_count": 0,
        "supervisor_decision": {},
        "current_expert": "",
        "task_cursor": 0,
        "task_results": [],
        "current_task": None,
    }



def _register_request_pins(
    snapshot: dict, state: dict[str, Any],
    user_id: str, session_id: str,
) -> None:
    """Hook C（Context Budget 生产收口 B4）：登记请求级业务 pin。

    「当前请求正确执行必须保留」的结构化状态——确认中的动作实体
    （pending_action.target_id：订单/退款/售后单号）与 ContextResolver
    的 last_order_id——注册为内容锚定 pin：L2 裁剪永不丢包含该实体的
    消息；L5 摘要把同一批值作为 critical 业务事实保护（manager._run_l5
    消费 request_pin_values）。生命周期 = 请求级 ContextVar：order A→B
    由下一轮重新注册自然替换，不跨轮累积。失败只降级（无 pin），绝不
    阻断 CS 主流程。
    """
    try:
        from backend.context_budget.pin import (
            PIN_CONFIRMATION,
            PIN_ENTITY,
            register_request_pin,
        )

        pending = snapshot.get("pending_action") or {}
        target = str(pending.get("target_id") or "").strip()
        if target:
            register_request_pin(target, PIN_CONFIRMATION)

        tenant_id = str(
            (state.get("cs_context") or {}).get("tenant_id") or "default")
        try:
            from backend.customer_service.context_resolver import (
                get_recent_business_context,
            )
            recent = get_recent_business_context(
                tenant_id, user_id, session_id) or {}
            last_order = str(recent.get("last_order_id") or "").strip()
            if last_order and last_order != target:
                register_request_pin(last_order, PIN_ENTITY)
        except Exception:  # noqa: BLE001 — resolver 不可用时跳过该来源
            logger.debug("[CS StateLoader] context resolver pin 读取失败",
                         exc_info=True)
    except Exception:  # noqa: BLE001 — pin 注册失败不影响 CS 主流程
        logger.debug("[CS StateLoader] 请求级 pin 注册失败", exc_info=True)


def build_cs_graph(checkpointer: Any = None) -> StateGraph:
    """构建 CS Graph

    Phase 5 拓扑 (Command 路由 + Pending Handler):
      START → cs_state_loader → cs_pending_handler
        → (无 pending) → cs_supervisor → Command(goto=...) → experts → cs_supervisor (loop)
                                                              → cs_reporter → END
        → (有 pending) → 处理确认/取消/超时 → cs_reporter → END
    """
    wf = StateGraph(CSGraphState)

    # 四个编排节点统一产出客服 Span。专家节点不在此包装，避免与
    # CsExpertHooks 的专家 Span 重复。
    wf.add_node(
        CS_STATE_LOADER,
        wrap_cs_node(CS_STATE_LOADER, cs_state_loader_node),
    )
    wf.add_node(
        CS_PENDING_HANDLER,
        wrap_cs_node(CS_PENDING_HANDLER, cs_pending_handler_node),
    )
    wf.add_node(CS_SUPERVISOR, wrap_cs_node(CS_SUPERVISOR, cs_supervisor_node))
    wf.add_node(CS_REPORTER, wrap_cs_node(CS_REPORTER, cs_reporter_node))

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

    Phase 5: 当 CS_CHECKPOINTER_ENABLED=true 时注入 checkpointer（Postgres 优先，
    不可用则按 config/checkpointer.py 的判据降级或硬失败）。
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

    降级判据（结构病审查 P2-10）：本地开发降级 + 告警；生产环境 fail-loud
    （除非 CHECKPOINTER_ALLOW_DEGRADE=true）——原实现是「一条 warning 就地吞掉」，
    生产上会静默失去跨轮上下文，而日志里的 enabled 让人误以为已持久化。
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
            # TTL 清理守护：checkpoint 按轮累积，无清理会无限膨胀（软失败）
            from backend.customer_service.checkpointer_cleanup import (
                start_cleanup_daemon,
            )
            start_cleanup_daemon()
            return checkpointer
        except Exception:
            # 降级判据统一走 config.checkpointer（结构病审查 P2-10）：不再一条
            # warning 就地吞掉 —— 开发环境降级 + 告警；生产环境 fail-loud。
            degrade_or_raise(
                "CS Graph",
                "PostgresSaver 初始化失败（多为缺 psycopg v3 / "
                "langgraph-checkpoint-postgres，或 PG 连不上 / setup 建表失败）",
                exc_info=True,
            )

    try:
        from langgraph.checkpoint.memory import MemorySaver
        logger.info("[CS Graph] checkpointer enabled (MemorySaver)")
        return MemorySaver()
    except Exception:
        degrade_or_raise("CS Graph", "MemorySaver 也不可用（langgraph 安装不完整）")
        return None
