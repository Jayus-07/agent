"""planner — 向后兼容 re-export（新代码请用 multi_agent.planner.*）"""
from backend.agent.planner.planner import (
    planner_node, _extract_json, _normalize_plan, _filter_plan,
    _ensure_knowledge_step, _fallback_plan,
)
from backend.agent.planner.prompt import (
    PLANNER_SYSTEM, is_knowledge_question, _format_capabilities_schema,
)

__all__ = [
    "planner_node", "_extract_json", "_normalize_plan", "_filter_plan",
    "_ensure_knowledge_step", "_fallback_plan",
    "PLANNER_SYSTEM", "is_knowledge_question", "_format_capabilities_schema",
]
