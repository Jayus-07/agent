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

# ============================================================
# 路由映射 — 单一事实源（P2.2 映射统一，此前 4 处独立硬编码存在漂移风险）
# ============================================================
# 权威表仅两张：ROUTE_PATH_TO_EXPERT_NAME（route_path → expert 语义名）与
# EXPERT_NAME_TO_NODE（expert 语义名 → 图节点名）。其余表全部由此派生：
#   - ROUTE_PATH_TO_CS_TARGET / CS_TARGET_TO_EXPERT：兼容视图
#     （cs_target 是 prefilter 的历史 trace 标识，评测与质量报告按其判定路由一致率）
#   - DOMAIN_TO_EXPERT_NAME：domain 兜底映射（cs_route.route_path 缺失时用）

ROUTE_PATH_TO_EXPERT_NAME = {
    "knowledge_query": "knowledge",
    "business_query": "query",
    "business_action": "action",
    "complaint_flow": "complaint",
    "human_handoff": "handoff",
}

EXPERT_NAME_TO_NODE = {
    "knowledge": CS_KNOWLEDGE_EXPERT,
    "query": CS_QUERY_EXPERT,
    "action": CS_ACTION_EXPERT,
    "complaint": CS_COMPLAINT_EXPERT,
    "handoff": CS_HANDOFF_EXPERT,
}

DOMAIN_TO_EXPERT_NAME = {
    "KNOWLEDGE": "knowledge",
    "TRANSACTION": "query",
    "AFTER_SALES": "action",
    "ACCOUNT": "action",
    "COMPLAINT": "complaint",
    "HUMAN": "handoff",
}

ROUTE_PATH_TO_CS_TARGET = {
    "knowledge_query": "cs_knowledge",
    "business_query": "cs_business_query",
    "business_action": "cs_business_action",
    "complaint_flow": "cs_complaint",
    "human_handoff": "cs_handoff",
}

CS_TARGET_TO_EXPERT = {
    ROUTE_PATH_TO_CS_TARGET[rp]: EXPERT_NAME_TO_NODE[expert_name]
    for rp, expert_name in ROUTE_PATH_TO_EXPERT_NAME.items()
}
# "cs_pending"（确认拦截）不在 route_path 体系内，单独补映射
CS_TARGET_TO_EXPERT["cs_pending"] = CS_PENDING_HANDLER


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
