"""
builder.py — LangGraph StateGraph 构建

Graph 拓扑:
  START → Planner → Critique → Supervisor (node) → route_after_supervisor (routing)
    ├─ returns Send[] → Skills (并行) → supervisor (loop)
    └─ returns "reporter" → Reporter → END
"""

import asyncio

from langgraph.graph import StateGraph, START, END

from backend.agent.state import AgentState
from backend.agent.planner.planner import planner_node
from backend.agent.planner.critique import critique_node
from backend.agent.supervisor.scheduler import supervisor_node, route_after_supervisor
from backend.agent.skills.sql_skill import sql_skill_node
from backend.agent.skills.rag_skill import rag_skill_node
from backend.agent.skills.report_skill import report_skill_node
from backend.agent.reporter.reporter import reporter_node
from backend.shared.logger import logger


# 节点名 → 用户可读的阶段标签
_NODE_LABELS = {
    "planner":       "任务规划",
    "critique":      "计划审查",
    "supervisor":    "调度决策",
    "sql_skill":     "数据库查询",
    "rag_skill":     "知识库检索",
    "report_skill":  "报告生成",
    "reporter":      "结果汇总",
}


# =====================================================
# 路由函数
# =====================================================

def route_after_planner(state: AgentState) -> str:
    """Planner 之后：有 plan → supervisor，空 → reporter"""
    plan = state.get("plan", {})
    nodes = plan.get("nodes", {})
    if nodes:
        logger.info("[Graph] Planner → Supervisor")
        return "supervisor"
    logger.info("[Graph] Planner → Reporter (空计划)")
    return "reporter"


def route_after_critique(state: AgentState) -> str:
    """Critique 后的路由：空计划直接到 Reporter，否则到 Supervisor"""
    plan = state.get("plan", {})
    if not plan.get("nodes"):
        logger.info("[Graph] 空 plan，跳过 Supervisor")
        return "reporter"
    return "supervisor"


# =====================================================
# async→sync 适配 (skill 节点是 async，graph 用 sync invoke)
# =====================================================

def _make_sync(async_fn):
    """将 async 函数包装为同步函数，避免 LangGraph sync invoke 报错"""
    import functools
    @functools.wraps(async_fn)
    def wrapper(state: dict) -> dict:
        return asyncio.run(async_fn(state))
    return wrapper


# =====================================================
# 图构建
# =====================================================

def build_graph():
    """构建 Multi-Agent StateGraph"""
    wf = StateGraph(AgentState)

    # — 节点 (skill 节点用 _make_sync 适配 async→sync) —
    wf.add_node("planner", planner_node)
    wf.add_node("critique", critique_node)
    wf.add_node("supervisor", supervisor_node)
    wf.add_node("sql_skill", _make_sync(sql_skill_node))
    wf.add_node("rag_skill", _make_sync(rag_skill_node))
    wf.add_node("report_skill", _make_sync(report_skill_node))
    wf.add_node("reporter", reporter_node)

    # — 边 —
    wf.add_edge(START, "planner")
    wf.add_edge("planner", "critique")

    wf.add_conditional_edges(
        "critique",
        route_after_critique,
        {"supervisor": "supervisor", "reporter": "reporter"},
    )

    # Skill 完成 → 回到 Supervisor 继续调度
    wf.add_edge("sql_skill", "supervisor")
    wf.add_edge("rag_skill", "supervisor")
    wf.add_edge("report_skill", "supervisor")

    # Supervisor → route_after_supervisor 路由函数:
    #   返回 list[Send] → LangGraph 自行并行调度到对应 Skill
    #   返回 "reporter" → 进入 Reporter
    wf.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
    )

    wf.add_edge("reporter", END)

    logger.info("[Graph] 图编译完成 (Planner -> Critique -> Supervisor <-> Skills -> Reporter)")
    return wf.compile()


# =====================================================
# 辅助函数
# =====================================================

_SKILL_NODES = {"sql_skill", "rag_skill", "report_skill"}


def _parse_event(event: dict) -> tuple:
    """从 LangGraph stream 事件中提取 (node_name, node_output)。"""
    if not isinstance(event, dict):
        return None, None
    for key, value in event.items():
        if key in _NODE_LABELS or key in _SKILL_NODES:
            return key, value
    return None, None
