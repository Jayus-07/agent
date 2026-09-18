"""
builder.py — LangGraph StateGraph 构建

Graph 拓扑:
  START → Router → route_selector (条件边)
    ├─ planner → critique → supervisor → skills → reporter → END
    ├─ skill_executor → reporter → END
    ├─ workflow_executor → reporter → END
    └─ 域图节点 (自动发现) → END

Skill 节点由 tool_registry 自动发现，域图节点由 domain_graph_registry 自动发现。
新增 Skill/域图只需创建包 + 注册，builder 无需修改。
"""

import asyncio
import threading

from langgraph.graph import END, START, StateGraph

# 触发域图自注册
import backend.domains  # noqa: F401

# 触发 Skill 包自注册（必须在 build_graph() 之前 import）
import backend.skills  # noqa: F401
from backend.agents.planner.critique import critique_node
from backend.agents.planner.planner import planner_node
from backend.agents.reporter.reporter import reporter_node
from backend.observability.trace_middleware import trace_middleware
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.graph.direct_executor import skill_executor_node, workflow_executor_node
from backend.orchestration.graph.router_node import route_selector, router_node
from backend.orchestration.graph.tool_selector import tool_selector_node
from backend.orchestration.state import AgentState, OrchestratorState
from backend.orchestration.supervisor.scheduler import route_after_supervisor, supervisor_node
from backend.orchestration.capability_registry import tool_registry
from backend.shared.logger import logger

# 节点名 → 用户可读的阶段标签（单一事实源）。
# 2026-09-15 收敛：chat.py 曾维护一份含过期节点名（sql_worker/rag_worker，
# 实际节点为 sql_skill/rag_skill）的重复映射，meta 事件发给前端的标签与
# 真实节点漂移。现统一由本表提供，chat.py 直接 import；域图标签在
# build_graph 时动态补入。
_NODE_LABELS = {
    "router":             "路由决策",
    "tool_selector":      "工具选择",
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

# ⚠️ 设计约定（2026-09-17 事件循环复用改造）::
#   Skill 节点运行在线程本地事件循环上（每 worker 线程一个 loop，跨节点/
#   跨请求复用）。因此 Skill 内部**禁止持有绑定"某一次调用"事件循环的
#   全局 async 资源**（全局 aiohttp.ClientSession / asyncpg pool 等）——
#   下次调用可能落在另一个线程的另一个 loop 上，触发
#   "attached to a different loop" 错误。
#   确需共享连接池：绑定到专用单线程 ThreadPoolExecutor(max_workers=1)
#   并在节点内用 run_in_executor 派发，或直接走同步客户端。
_thread_local = threading.local()


def _get_thread_loop() -> asyncio.AbstractEventLoop:
    """取当前线程的复用事件循环（懒创建；关闭过则重建）。"""
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_local.loop = loop
    return loop


def _make_sync(async_fn):
    """将 async 函数包装为同步函数，避免 LangGraph sync invoke 报错。

    2026-09-17 前用 ``asyncio.run``——每次调用新建事件循环，开销大且使
    "loop 内创建的 task/call_later" 无法跨调用观测。现改为线程本地 loop
    复用（run_until_complete）：同线程内多次调用共享同一 loop，
    asyncio.create_task / get_running_loop 在协程内照常可用。
    """
    import functools

    @functools.wraps(async_fn)
    def wrapper(state: dict) -> dict:
        loop = _get_thread_loop()
        return loop.run_until_complete(async_fn(state))
    return wrapper


# =====================================================
# 图构建
# =====================================================

def build_graph(checkpointer=None):
    """构建 Multi-Agent StateGraph。

    Skill 节点由 tool_registry 自动发现，不在此处硬编码节点名。
    checkpointer: 主图状态持久化（MAIN_GRAPH_CHECKPOINTER_ENABLED 开启时由
    system.py 传入 build_main_checkpointer() 的结果；None = 不持久化）。
    """
    wf = StateGraph(OrchestratorState)

    # ── 内置节点（永远不变，TraceMiddleware 自动记录 Span）──
    # 注意：router 不用中间件包装 —— MultiTierRouter.route() 内部已自建
    # 完整 span（含 rule/vector/llm 三层事件与 metrics）。双重包装会产生
    # 同名重复 span（浏览器实测发现的 0ms+真实时长两条"路由决策"）。
    wf.add_node("router", router_node)
    # direct 路径: router --direct--> tool_selector（FC 门控选工具+填参，
    # 失败/快路径直通零开销）→ skill_executor
    wf.add_node("tool_selector", trace_middleware.wrap_sync_node("tool_selector", tool_selector_node))
    wf.add_node("skill_executor", trace_middleware.wrap_sync_node("skill_executor", skill_executor_node))
    wf.add_node("workflow_executor", trace_middleware.wrap_sync_node("workflow_executor", workflow_executor_node))
    wf.add_node("planner", trace_middleware.wrap_sync_node("planner", planner_node))
    wf.add_node("critique", trace_middleware.wrap_sync_node("critique", critique_node))
    wf.add_node("supervisor", trace_middleware.wrap_sync_node("supervisor", supervisor_node))
    wf.add_node("reporter", trace_middleware.wrap_sync_node("reporter", reporter_node))

    # ── 域图节点（自动发现，每个域图自带 reporter，直接到 END）──
    domains = domain_graph_registry.get_all()
    for domain in domains.values():
        wf.add_node(domain.node_name, trace_middleware.wrap_sync_node(domain.node_name, domain.adapter))
        _NODE_LABELS[domain.node_name] = domain.label
        logger.debug(f"[Graph] 自动注册域图节点: {domain.name} → {domain.node_name}")
    if domains:
        logger.info(f"[Graph] 已注册 {len(domains)} 个域图节点")

    # ── Skill 节点（自动发现 + TraceMiddleware 自动记录 Span）──
    skill_nodes = tool_registry.get_skill_nodes()
    for name, func in skill_nodes.items():
        sync_func = _make_sync(func)
        traced_func = trace_middleware.wrap_sync_node(name, sync_func)
        wf.add_node(name, traced_func)
        wf.add_edge(name, "supervisor")  # 完成 → 回到 Supervisor
        logger.debug(f"[Graph] 自动注册 Skill 节点: {name}")
    if skill_nodes:
        logger.info(f"[Graph] 已注册 {len(skill_nodes)} 个 Skill 节点")

    # ── 边 ────────────────────────────────────────
    wf.add_edge(START, "router")

    # 条件边映射：内置路径 + 域图自动发现。
    # direct 的返回值 "skill_executor" 映射到 tool_selector（先做 FC 门控
    # 选择再进 executor）；route_selector 本身不改，语义仍是"直接执行路径"
    edge_map = {
        "planner": "planner",
        "skill_executor": "tool_selector",
        "workflow_executor": "workflow_executor",
        # L1 弱命中追问（2026-09-19）：router 短路出追问，reporter 只出短文案
        "clarify": "reporter",
    }
    for domain in domains.values():
        edge_map[domain.node_name] = domain.node_name

    wf.add_conditional_edges("router", route_selector, edge_map)

    wf.add_edge("tool_selector", "skill_executor")

    # V2: skill/workflow executor 直接到 reporter
    wf.add_edge("skill_executor", "reporter")
    wf.add_edge("workflow_executor", "reporter")

    # 域图自带 reporter，直接到 END
    for domain in domains.values():
        wf.add_edge(domain.node_name, END)

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
        f"[Graph] 图编译完成 (内置9节点+Router/executors + {skill_count} Skill = {9 + skill_count}节点,"
        f"checkpointer={'on' if checkpointer is not None else 'off'})"
    )
    return wf.compile(checkpointer=checkpointer)


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
