"""
graph.py — LangGraph 图编译 + MultiAgentSystem 入口类

Graph 拓扑:
  START → Planner → Supervisor (node) → route_after_supervisor (routing)
    ├─ returns Send[] → Workers (并行) → supervisor (loop)
    └─ returns "reporter" → Reporter → END

关键: route_after_supervisor 是 conditional_edges 的路由函数（非节点），
返回 list[Send] 实现并行扇出，LangGraph 自动处理并发执行和结果合并。
"""

from langgraph.graph import StateGraph, START, END

from multi_agent.state import AgentState
from multi_agent.planner import planner_node
from multi_agent.supervisor import supervisor_node, route_after_supervisor
from multi_agent.workers.sql_worker import sql_worker_node
from multi_agent.workers.rag_worker import rag_worker_node
from multi_agent.workers.report_worker import report_worker_node
from multi_agent.reporter import reporter_node
from utils.logger import logger


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


# =====================================================
# 图构建
# =====================================================

def build_graph():
    """构建 Multi-Agent StateGraph"""
    wf = StateGraph(AgentState)

    # — 节点 —
    wf.add_node("planner", planner_node)
    wf.add_node("supervisor", supervisor_node)
    wf.add_node("sql_worker", sql_worker_node)
    wf.add_node("rag_worker", rag_worker_node)
    wf.add_node("report_worker", report_worker_node)
    wf.add_node("reporter", reporter_node)

    # — 边 —
    wf.add_edge(START, "planner")

    wf.add_conditional_edges(
        "planner",
        route_after_planner,
        {"supervisor": "supervisor", "reporter": "reporter"},
    )

    # Worker 完成 → 回到 Supervisor 继续调度
    wf.add_edge("sql_worker", "supervisor")
    wf.add_edge("rag_worker", "supervisor")
    wf.add_edge("report_worker", "supervisor")

    # Supervisor → route_after_supervisor 路由函数:
    #   返回 list[Send] → LangGraph 自行并行调度到对应 Worker
    #   返回 "reporter" → 进入 Reporter
    wf.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
    )

    wf.add_edge("reporter", END)

    logger.info("[Graph] 图编译完成 (Planner → Supervisor ⇄ Workers → Reporter)")
    return wf.compile()


# =====================================================
# MultiAgentSystem — 对外入口
# =====================================================

class MultiAgentSystem:
    """Multi-Agent 工作流系统入口"""

    def __init__(self):
        logger.info("[MultiAgent] 初始化 Multi-Agent 工作流系统...")
        self._graph = build_graph()
        logger.info("[MultiAgent] 就绪")

    def ask(self, question: str) -> str:
        """
        处理用户问题，返回最终 Markdown 回答。
        """
        if not question or not question.strip():
            return "## 提示\n\n请输入有效问题。"

        logger.info(f"[MultiAgent] 收到问题: {question[:80]}...")

        initial_state: AgentState = {
            "question": question.strip(),
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
            "current_step_id": None,
            "messages": [],
            "final_answer": "",
        }

        try:
            final_state = self._graph.invoke(initial_state)
            answer = final_state.get("final_answer", "")
            if not answer:
                return self._fallback_summary(final_state)
            return answer
        except Exception as e:
            logger.error(f"[MultiAgent] 执行失败: {e}")
            return f"## 系统错误\n\n处理问题失败: {e}\n\n请稍后重试。"

    def stream(self, question: str):
        """
        流式处理，逐步返回状态更新。
        """
        if not question or not question.strip():
            yield {"final_answer": "请输入有效问题。"}
            return

        initial_state: AgentState = {
            "question": question.strip(),
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
            "current_step_id": None,
            "messages": [],
            "final_answer": "",
        }

        for event in self._graph.stream(initial_state):
            yield event

    def _fallback_summary(self, state: AgentState) -> str:
        """当 Reporter 未产生输出时的降级处理"""
        step_results = state.get("step_results", {})
        lines = ["## 执行结果", ""]

        for step_id, sr in sorted(step_results.items()):
            status = sr.get("status", "?")
            desc = sr.get("description", step_id)
            if status == "success":
                lines.append(f"### {desc}")
                lines.append(str(sr.get("output", "")))
                lines.append("")
            elif status == "failed":
                lines.append(f"### {desc} ❌")
                lines.append(f"失败: {sr.get('error', '')}")
                lines.append("")

        return "\n".join(lines) if len(lines) > 2 else "## 无结果\n\n未能获取任何有效数据。"
