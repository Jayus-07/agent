"""
builder.py — LangGraph StateGraph 构建

Graph 拓扑:
  START → Planner → Critique → Supervisor (node) → route_after_supervisor (routing)
    ├─ returns Send[] → Skills (并行) → supervisor (loop)
    └─ returns "reporter" → Reporter → END

Skill 节点由 tool_registry 自动发现，不在此处硬编码。
新增 Skill 只需创建包 + 注册，builder 无需修改。
"""

import asyncio

from langgraph.graph import StateGraph, START, END

from backend.orchestration.state import AgentState
from backend.orchestration.graph.router_node import router_node, route_selector
from backend.orchestration.graph.direct_executor import skill_executor_node, workflow_executor_node
from backend.observability.trace_middleware import trace_middleware
from backend.agents.planner.planner import planner_node
from backend.agents.planner.critique import critique_node
from backend.orchestration.supervisor.scheduler import supervisor_node, route_after_supervisor
from backend.agents.reporter.reporter import reporter_node
from backend.orchestration.tool_registry import tool_registry
from backend.shared.logger import logger

# 触发 Skill 包自注册（必须在 build_graph() 之前 import）
import backend.skills  # noqa: F401


# 节点名 → 用户可读的阶段标签（与 trace_middleware.py 对齐）
_NODE_LABELS = {
    "router":             "路由决策",
    "skill_executor":     "直接执行",
    "workflow_executor":  "工作流执行",
    "planner":            "任务规划",
    "critique":           "计划审查",
    "supervisor":         "调度决策",
    "sql_skill":          "数据库查询",
    "rag_skill":          "知识库检索",
    "report_skill":       "报告生成",
    "reporter":           "结果汇总",
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
    """构建 Multi-Agent StateGraph。

    Skill 节点由 tool_registry 自动发现，不在此处硬编码节点名。
    """
    wf = StateGraph(AgentState)

    # ── 内置节点（永远不变，TraceMiddleware 自动记录 Span）──
    # 注意：router 不用中间件包装 —— MultiTierRouter.route() 内部已自建
    # 完整 span（含 rule/vector/llm 三层事件与 metrics）。双重包装会产生
    # 同名重复 span（浏览器实测发现的 0ms+真实时长两条“路由决策”）。
    wf.add_node("router", router_node)
    wf.add_node("skill_executor", trace_middleware.wrap_sync_node("skill_executor", skill_executor_node))
    wf.add_node("workflow_executor", trace_middleware.wrap_sync_node("workflow_executor", workflow_executor_node))
    wf.add_node("planner", trace_middleware.wrap_sync_node("planner", planner_node))
    wf.add_node("critique", trace_middleware.wrap_sync_node("critique", critique_node))
    wf.add_node("supervisor", trace_middleware.wrap_sync_node("supervisor", supervisor_node))
    wf.add_node("reporter", trace_middleware.wrap_sync_node("reporter", reporter_node))

    # ── Skill 节点（自动发现 + TraceMiddleware 自动记录 Span）──
    for name, func in tool_registry.get_skill_nodes().items():
        sync_func = _make_sync(func)
        traced_func = trace_middleware.wrap_sync_node(name, sync_func)
        wf.add_node(name, traced_func)
        wf.add_edge(name, "supervisor")  # 完成 → 回到 Supervisor
        logger.info(f"[Graph] 自动注册 Skill 节点: {name}")

    # ── 边 ────────────────────────────────────────
    # 2026-08-11：Router 在入口（V2 三路分流）
    wf.add_edge(START, "router")
    wf.add_conditional_edges(
        "router",
        route_selector,
        {
            "planner": "planner",
            "skill_executor": "skill_executor",  # direct: 跳过 Planner
            "workflow_executor": "workflow_executor",  # workflow: 跳过 Planner
        },
    )

    # V2: skill/workflow executor 直接到 reporter
    wf.add_edge("skill_executor", "reporter")
    wf.add_edge("workflow_executor", "reporter")

    wf.add_edge("planner", "critique")

    wf.add_conditional_edges(
        "critique",
        route_after_critique,
        {"supervisor": "supervisor", "reporter": "reporter"},
    )

    # Supervisor → route_after_supervisor:
    #   返回 list[Send] → LangGraph 自行并行调度到对应 Skill
    #   返回 "reporter" → 进入 Reporter
    wf.add_conditional_edges("supervisor", route_after_supervisor)

    wf.add_edge("reporter", END)

    skill_count = len(tool_registry.get_skill_nodes())
    logger.info(
        f"[Graph] 图编译完成 (内置7节点+Router/executors + {skill_count} Skill = {7 + skill_count}节点)"
    )
    return wf.compile()


# =====================================================
# 辅助函数
# =====================================================

def _parse_event(event: dict) -> tuple:
    """从 LangGraph stream 事件中提取 (node_name, node_output)。"""
    if not isinstance(event, dict):
        return None, None
    all_keys = set(_NODE_LABELS.keys()) | tool_registry.get_skill_node_names()
    for key, value in event.items():
        if key in all_keys:
            return key, value
    return None, None
