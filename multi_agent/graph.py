"""
graph.py — LangGraph 图编译 + MultiAgentSystem 入口类

Graph 拓扑:
  START → Planner → Supervisor (node) → route_after_supervisor (routing)
    ├─ returns Send[] → Workers (并行) → supervisor (loop)
    └─ returns "reporter" → Reporter → END

关键: route_after_supervisor 是 conditional_edges 的路由函数（非节点），
返回 list[Send] 实现并行扇出，LangGraph 自动处理并发执行和结果合并。
"""

import time
from typing import Generator

from langgraph.graph import StateGraph, START, END

from multi_agent.state import AgentState
from multi_agent.planner import planner_node
from multi_agent.supervisor import supervisor_node, route_after_supervisor
from multi_agent.workers.sql_worker import sql_worker_node
from multi_agent.workers.rag_worker import rag_worker_node
from multi_agent.workers.report_worker import report_worker_node
from multi_agent.reporter import reporter_node
from utils.logger import logger


# 节点名 → 用户可读的阶段标签
_NODE_LABELS = {
    "planner":       "任务规划",
    "supervisor":    "调度决策",
    "sql_worker":    "数据库查询",
    "rag_worker":    "知识库检索",
    "report_worker": "报告生成",
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
        from memory import memory_manager
        self._memory = memory_manager
        self._last_sources = []  # 最近一次 ask() 提取的来源文档
        logger.info("[MultiAgent] 就绪")

    def ask(self, question: str, session_id: str = "default", kb_id: str = "default") -> str:
        """
        处理用户问题，返回最终 Markdown 回答。
        session_id 用于跨轮次记忆持久化。
        kb_id 用于知识库隔离（policy/tech/finance/hr/default）。
        """
        if not question or not question.strip():
            return "## 提示\n\n请输入有效问题。"

        logger.info(f"[MultiAgent] 收到问题: {question[:80]}... (session={session_id}, kb={kb_id})")

        # L1/L2/L3: 加载会话上下文
        l1 = self._memory.start_session(session_id, question)

        initial_state: AgentState = {
            "question": question.strip(),
            "kb_id": kb_id,
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
            "current_step_id": None,
            "messages": list(l1.messages),
            "final_answer": "",
        }

        try:
            final_state = self._graph.invoke(initial_state)
            answer = final_state.get("final_answer", "")
            if not answer:
                answer = self._fallback_summary(final_state)

            # 提取结构化来源（供前端 SourceCard 展示）
            step_results = final_state.get("step_results", {})
            self._last_sources = self._extract_sources(step_results, answer)

            # L2/L3: 持久化本轮
            self._memory.end_turn(session_id, question, answer)
            return answer
        except Exception as e:
            logger.error(f"[MultiAgent] 执行失败: {e}")
            self._memory.end_turn(session_id, question, f"[错误] {e}")
            return f"## 系统错误\n\n处理问题失败: {e}\n\n请稍后重试。"

    @staticmethod
    def _extract_sources(step_results: dict, final_answer: str) -> list[dict]:
        """从 step_results 和 final_answer 中提取结构化来源列表"""
        from multi_agent.reporter import _extract_sources_from_steps, _parse_sources_from_text
        sources = _extract_sources_from_steps(step_results)
        if not sources and final_answer:
            sources = _parse_sources_from_text(final_answer)
        return sources

    def stream_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,  # threading.Event | None — 中断标志
    ) -> Generator[dict, None, None]:
        """
        SSE 流式处理：产出 4 种事件类型 (status / log / delta / done)。

        事件格式:
            {"event": "status", "data": {"node": "supervisor", "ts": 1.23}}
            {"event": "log",    "data": {"level":"info|warn|error","node":"...","step_id":"...","message":"...","payload":{...},"ts":...}}
            {"event": "delta",  "data": {"content": "句子块", "ts": ...}}
            {"event": "done",   "data": {"elapsed": ..., "sources": [...]}}

        stop_event: threading.Event，前端主动断开时 set，生成器在检查点退出。
        """
        import re
        import threading as _threading

        if not question or not question.strip():
            yield {"event": "error", "data": {"message": "请输入有效问题", "ts": time.time()}}
            return

        l1 = self._memory.start_session(session_id, question)
        start_time = time.time()

        initial_state: AgentState = {
            "question": question.strip(),
            "kb_id": kb_id,
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
            "current_step_id": None,
            "messages": list(l1.messages),
            "final_answer": "",
        }

        final_answer = ""
        all_step_results = {}  # 累积全部步骤结果，用于 done 提取 sources
        worker_set = {"sql_worker", "rag_worker", "report_worker"}

        try:
            for event in self._graph.stream(initial_state):
                # ── 中断检查点 (1): 每次迭代 ──
                if stop_event is not None and stop_event.is_set():
                    yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                    return

                node_name, node_output = _parse_event(event)
                if node_name is None:
                    continue

                # ═══════════════════════════════════════
                # event: status — 节点进入（纯 node 映射，无 label）
                # ═══════════════════════════════════════
                yield {"event": "status", "data": {"node": node_name, "ts": time.time()}}

                # ═══════════════════════════════════════
                # event: log — 按节点类型构造详细日志
                # ═══════════════════════════════════════

                # —— Planner ——
                if node_name == "planner":
                    plan = node_output.get("plan", {})
                    nodes = plan.get("nodes", {})
                    descriptions = [n.get("description", "") for n in nodes.values()]
                    yield {
                        "event": "log",
                        "data": {
                            "level": "info",
                            "node": node_name,
                            "step_id": "planning",
                            "message": f"任务分解完成，共 {len(nodes)} 个子任务",
                            "payload": {
                                "task_count": len(nodes),
                                "tasks": descriptions,
                            },
                            "ts": time.time(),
                        },
                    }

                # —— Supervisor ——
                elif node_name == "supervisor":
                    ready = node_output.get("_ready_dispatch", [])
                    all_done = node_output.get("_all_steps_done", False)
                    step_results = node_output.get("step_results", {})
                    all_step_results.update(step_results)

                    # 每个步骤状态变化产一条 log
                    for sid, sr in step_results.items():
                        status = sr.get("status", "?")
                        if status == "?":
                            continue
                        desc = sr.get("description", sid)

                        level = "info"
                        if status == "failed":
                            level = "error"
                        elif status == "skipped":
                            level = "warn"

                        payload: dict = {"description": desc, "status": status}
                        if sr.get("error"):
                            payload["error"] = sr["error"]
                        if sr.get("started_at") is not None:
                            payload["started_at"] = round(sr["started_at"], 3)
                        if sr.get("finished_at") is not None:
                            payload["elapsed"] = round(sr["finished_at"] - sr.get("started_at", sr["finished_at"]), 2)
                            payload["finished_at"] = round(sr["finished_at"], 3)

                        yield {
                            "event": "log",
                            "data": {
                                "level": level,
                                "node": node_name,
                                "step_id": sid,
                                "message": f"{'完成' if status == 'success' else '失败' if status == 'failed' else '跳过' if status == 'skipped' else '派发'}: {desc}",
                                "payload": payload,
                                "ts": time.time(),
                            },
                        }

                    # 调度快照：一次派发了哪些任务
                    if ready:
                        dispatch_info = [{"step_id": r["step_id"], "worker": r["worker"]} for r in ready]
                        yield {
                            "event": "log",
                            "data": {
                                "level": "info",
                                "node": node_name,
                                "step_id": "dispatch",
                                "message": f"调度 {len(ready)} 个任务到 Worker",
                                "payload": {"dispatched": dispatch_info},
                                "ts": time.time(),
                            },
                        }

                # —— Workers (sql/rag/report) ——
                elif node_name in worker_set:
                    step_results = node_output.get("step_results", {})
                    for sid, sr in step_results.items():
                        status = sr.get("status", "?")
                        if status == "?":
                            continue

                        level = "info"
                        if status == "failed":
                            level = "error"
                        elif status == "skipped":
                            level = "warn"

                        desc = sr.get("description", sid)
                        output = sr.get("output", "")

                        # Rich payload: 包含 Worker 入参/出参摘要
                        payload: dict = {
                            "description": desc,
                            "status": status,
                            "output_preview": str(output)[:500] if output else "",
                        }
                        if sr.get("error"):
                            payload["error"] = sr["error"]
                        if sr.get("started_at") is not None:
                            payload["started_at"] = round(sr["started_at"], 3)
                        if sr.get("finished_at") is not None:
                            payload["elapsed"] = round(sr["finished_at"] - sr.get("started_at", sr["finished_at"]), 2)
                            payload["finished_at"] = round(sr["finished_at"], 3)
                        # 保留原始 output 中的结构化信息（如 SQL 文本、RAG query 等）
                        if isinstance(output, dict):
                            for k in ("sql", "query", "params", "row_count", "result_count", "top_k"):
                                if k in output:
                                    payload[k] = output[k]

                        yield {
                            "event": "log",
                            "data": {
                                "level": level,
                                "node": node_name,
                                "step_id": sid,
                                "message": f"{'完成' if status == 'success' else '失败'}: {desc}",
                                "payload": payload,
                                "ts": time.time(),
                            },
                        }

                # —— Reporter ——
                elif node_name == "reporter":
                    final_answer = node_output.get("final_answer", "")
                    yield {
                        "event": "log",
                        "data": {
                            "level": "info",
                            "node": node_name,
                            "step_id": "summary",
                            "message": f"汇总生成最终回答 ({len(final_answer)} 字符)",
                            "payload": {"char_count": len(final_answer)},
                            "ts": time.time(),
                        },
                    }

            # ═══════════════════════════════════════
            # event: delta — 句子级切块推送（打字机数据源）
            # ═══════════════════════════════════════
            if final_answer:
                sentences = re.split(r'(?<=[。！？\n])', final_answer)
                sentences = [s for s in sentences if s.strip()]
                for sentence in sentences:
                    # ── 中断检查点 (2): 每个 delta 之前 ──
                    if stop_event is not None and stop_event.is_set():
                        yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                        return
                    yield {
                        "event": "delta",
                        "data": {"content": sentence, "ts": time.time()},
                    }
                    time.sleep(0.02)  # 模拟流式间隔

            # ── 中断后不发送 done ──
            if stop_event is not None and stop_event.is_set():
                yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                return

            # ═══════════════════════════════════════
            # event: done — 结束信号
            # ═══════════════════════════════════════
            from multi_agent.reporter import _extract_sources_from_steps, _parse_sources_from_text
            elapsed = time.time() - start_time
            sources = _extract_sources_from_steps(all_step_results)
            if not sources and final_answer:
                sources = _parse_sources_from_text(final_answer)
            yield {
                "event": "done",
                "data": {
                    "elapsed": round(elapsed, 1),
                    "sources": sources,
                },
            }

        except Exception as e:
            logger.error(f"[MultiAgent] 流式执行失败: {e}")
            yield {
                "event": "error",
                "data": {"message": f"执行失败: {e}", "ts": time.time()},
            }
        finally:
            self._memory.end_turn(session_id, question, final_answer or "")

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


# =====================================================
# 辅助函数
# =====================================================

def _parse_event(event: dict) -> tuple:
    """从 LangGraph stream 事件中提取 (node_name, node_output)。"""
    if not isinstance(event, dict):
        return None, None
    for key, value in event.items():
        if key in _NODE_LABELS or key in {"sql_worker", "rag_worker", "report_worker"}:
            return key, value
    return None, None
