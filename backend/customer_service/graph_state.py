"""
customer_service/graph_state.py — CS Graph 独立状态定义

独立于 Main Graph 的 OrchestratorState，CS Graph 内部所有节点读写此状态。
通过 cs_graph_node 适配器 + CSGraphResult 契约与 Main Graph 通信。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §7.2
"""
from __future__ import annotations

from typing import Any, TypedDict

# ============================================================
# CS Graph 节点名常量
# ============================================================
CS_STATE_LOADER = "cs_state_loader"
CS_SUPERVISOR = "cs_supervisor"
CS_REPORTER = "cs_reporter"
CS_PENDING_HANDLER = "cs_pending_handler"
CS_KNOWLEDGE_EXPERT = "cs_knowledge_expert"
CS_QUERY_EXPERT = "cs_query_expert"
CS_ACTION_EXPERT = "cs_action_expert"
CS_COMPLAINT_EXPERT = "cs_complaint_expert"
CS_HANDOFF_EXPERT = "cs_handoff_expert"


class CSGraphState(TypedDict, total=False):
    """CS Graph 独立状态

    设计原则:
    1. 与 Main Graph 的 OrchestratorState 完全分离
    2. 持久化状态 (conversation/confirmation/handoff) 只放引用，不放完整对象
    3. Graph State 只放执行态 — 当前决策需要的上下文
    4. 通过 CSGraphResult 契约与 Main Graph 通信
    """

    # === 输入 (从 cs_graph_node 适配器传入) ===
    user_message: str
    user_id: str
    session_id: str
    conversation_id: str
    cs_route: dict

    # === 执行态 (Supervisor + Expert 读写) ===
    supervisor_decision: dict
    expert_history: list[dict]
    last_expert_result: dict
    current_expert: str
    expert_loop_count: int

    # === 状态快照 (cs_state_loader 从 Store 加载，Expert/Supervisor 可读) ===
    conversation_status: str
    handling_mode: str
    handoff_state: str
    confirmation_state: str
    pending_action: dict | None

    # === 输出 (CS Reporter 生成) ===
    final_answer: str
    cs_context: dict
    cs_audit_entries: list[dict]
    cs_action_result: dict


def new_cs_graph_input(
    user_message: str,
    user_id: str,
    session_id: str,
    conversation_id: str,
    cs_route: dict,
) -> dict[str, Any]:
    """构建 CS Graph 初始输入（含执行态默认值）

    cs_state_loader 节点会在此基础上填充状态快照字段。
    """
    return {
        "user_message": user_message,
        "user_id": user_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "cs_route": cs_route,
        "supervisor_decision": {},
        "expert_history": [],
        "last_expert_result": {},
        "current_expert": "",
        "expert_loop_count": 0,
        "conversation_status": "",
        "handling_mode": "",
        "handoff_state": "",
        "confirmation_state": "",
        "pending_action": None,
        "final_answer": "",
        "cs_context": {},
        "cs_audit_entries": [],
        "cs_action_result": {},
    }
