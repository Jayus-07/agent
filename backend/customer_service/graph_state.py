"""
customer_service/graph_state.py — CS Graph 独立状态定义

独立于 Main Graph 的 OrchestratorState，CS Graph 内部所有节点读写此状态。
通过 cs_graph_node 适配器 + CSGraphResult 契约与 Main Graph 通信。

设计参考: docs/domains/customer-service.md §7.2
"""
from __future__ import annotations

from enum import Enum
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


class PendingTurnDecision(str, Enum):
    """Pending 期间仅对当前用户轮次有效的分流决策。"""

    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    READ_ONLY_QUERY = "READ_ONLY_QUERY"
    NEW_WRITE_CONFLICT = "NEW_WRITE_CONFLICT"
    HANDOFF = "HANDOFF"
    AMBIGUOUS = "AMBIGUOUS"


def route_path_value(value: Any) -> str:
    """归一 Router 的 route_path 字符串或 Enum 值。"""
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "")

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
    tenant_id: str

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
    pending_turn_decision: str | None
    pending_turn_expert_consumed: bool
    pending_turn_slot_fill: bool

    # === 当前轮有限复合任务计划 ===
    task_plan: dict | None
    task_plan_source: str
    task_cursor: int
    task_results: list[dict]
    current_task: dict | None

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
    tenant_id: str = "default",
) -> dict[str, Any]:
    """构建 CS Graph 初始输入（含执行态默认值）

    cs_state_loader 节点会在此基础上填充状态快照字段。
    """
    metadata = (cs_route or {}).get("metadata") or {}
    candidate = metadata.get("task_plan_candidate")
    from backend.customer_service.understanding.validator import (
        validate_task_plan_candidate,
    )
    validated_plan = validate_task_plan_candidate(candidate)

    return {
        "user_message": user_message,
        "user_id": user_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "cs_route": cs_route,
        "tenant_id": tenant_id or "default",
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
        "pending_turn_decision": None,
        "pending_turn_expert_consumed": False,
        "pending_turn_slot_fill": False,
        "task_plan": validated_plan,
        "task_plan_source": str(metadata.get("task_plan_source") or ""),
        "task_cursor": 0,
        "task_results": [],
        "current_task": None,
        "final_answer": "",
        "cs_context": {},
        "cs_audit_entries": [],
        "cs_action_result": {},
    }
