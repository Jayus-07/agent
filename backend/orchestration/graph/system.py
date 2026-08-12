"""
system.py — MultiAgentSystem 运行时入口

提供 ask()（同步）和 stream_events()（SSE 流式）两种调用方式。

Tracing（2026-07-16）：ask() / stream_events() 自动产出 TraceRecord + Span 树，
通过 trace_collector 统一收集，API 层无需额外处理。
"""
from __future__ import annotations

import re
import time
from typing import Generator, List

from backend.orchestration.graph.builder import build_graph, _parse_event
from backend.orchestration.supervisor.scheduler import MAX_SUPERVISOR_LOOPS
from backend.orchestration.state import AgentState
from backend.orchestration.graph.events import (
    stream_node_events, emit_delta_events, make_done_event,
    make_initial_state, extract_sources_from_results,
    make_step_payload, make_step_log_event,
)

from backend.orchestration.tool_registry import tool_registry
from backend.shared.logger import logger


class MultiAgentSystem:
    """Multi-Agent 工作流系统入口"""

    def __init__(self):
        logger.info("[MultiAgent] 初始化 Multi-Agent 工作流系统...")
        self._graph = build_graph()
        from backend.memory import memory_manager
        self._memory = memory_manager
        self._skill_nodes = tool_registry.get_skill_node_names()
        self._last_sources: list[dict] = []

        # P1 性能优化：后台预热 SQLAgent + RAG，首次请求不再冷启动
        import threading
        threading.Thread(target=self._prewarm, daemon=True, name="prewarm").start()

    def _prewarm(self) -> None:
        """后台预热：SQLAgent 连接池 + RAG 管道初始化。

        非阻塞——init 立即返回，预热在后台线程进行。
        首次请求如果预热未完成，SQLSkill/BusinessAnalysisSkill 会惰性等待。
        """
        t0 = time.time()
        try:
            from backend.sql.sql_agent import get_sql_agent
            _ = get_sql_agent()
            logger.info(f"[MultiAgent] SQLAgent 预热完成 ({(time.time()-t0)*1000:.0f}ms)")
        except Exception as e:
            logger.warning(f"[MultiAgent] SQLAgent 预热失败（非致命）: {e}")

        t0 = time.time()
        try:
            from backend.app.api.deps import get_rag_pipeline
            _ = get_rag_pipeline()
            logger.info(f"[MultiAgent] RAG 预热完成 ({(time.time()-t0)*1000:.0f}ms)")
        except Exception as e:
            logger.warning(f"[MultiAgent] RAG 预热失败（非致命）: {e}")
        logger.info("[MultiAgent] 预热完毕，就绪")

    # =====================================================
    # 同步入口
    # =====================================================

    def ask(self, question: str, session_id: str = "default", kb_id: str = "default") -> str:
        """处理用户问题，返回最终 Markdown 回答。"""
        if not question or not question.strip():
            return "## 提示\n\n请输入有效问题。"

        logger.info(f"[MultiAgent] 收到问题: {question[:80]}... (session={session_id}, kb={kb_id})")

        l1 = self._memory.start_session(session_id, question)
        initial_state = make_initial_state(question, session_id, kb_id, l1.messages)

        # ── Tracing: start + root span ──
        from backend.observability.tracer import trace_collector
        t_total = time.time()
        trace = trace_collector.start(question, session_id, workflow_name="agent")
        trace_collector.start_span("root", parent_id=None,
                                   name="多 Agent 协作管线", type="workflow",
                                   input={"question": question, "kb_id": kb_id})

        try:
            final_state = self._graph.invoke(initial_state)
            answer = final_state.get("final_answer", "")
            if not answer:
                answer = self._fallback_summary(final_state)

            step_results = final_state.get("step_results", {})
            self._last_sources = extract_sources_from_results(step_results, answer)

            # ── Tracing: 从 final_state 重建 span 树 ──
            self._trace_from_state(trace, final_state)

            total_ms = int((time.time() - t_total) * 1000)
            _end_root(trace, metrics={"span_count": len(trace.spans) - 1})
            trace_collector.finish(trace, answer, total_ms, "", "")

            self._memory.end_turn(session_id, question, answer)
            return answer
        except Exception as e:
            logger.error(f"[MultiAgent] 执行失败: {e}")
            try:
                _end_root(trace, status="error", metrics={"error": str(e)[:100]})
                trace_collector.finish(trace, "[ERROR]",
                                       int((time.time() - t_total) * 1000), "", "")
            except Exception:
                pass
            self._memory.end_turn(session_id, question, f"[错误] {e}")
            return f"## 系统错误\n\n处理问题失败: {e}\n\n请稍后重试。"

    # =====================================================
    # 事件分派
    # =====================================================

    def _stream_node_events(self, node_name: str, node_output: dict):
        """委托 events.stream_node_events。"""
        yield from stream_node_events(
            node_name, node_output, self._skill_nodes,
            make_step_payload, make_step_log_event,
        )

    # =====================================================
    # 事件构建器 — 逐节点
    # =====================================================

    def _fallback_summary(self, state: AgentState) -> str:
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
    # Tracing 重建（从 final_state 构建 Span 树）
    # =====================================================

    def _trace_from_state(self, trace, state: dict):
        """从 LangGraph final_state 重建执行过程 Span 树。

        invoke() 是黑盒，不能逐节点 trace；此方法从最终状态中提取执行痕迹：
          - Planner → span(type=agent, kind=graph_node)
          - Critique → span(type=agent, kind=graph_node)
          - Supervisor Round N → span(type=workflow, kind=graph_loop)
          - Skill 执行 → span(type=agent, kind=internal)
          - Reporter → span(type=agent, kind=graph_node)
          - LangGraph 拓扑 → trace.graph
        """
        from backend.observability.tracer import Span

        plan = state.get("plan", {})
        nodes = plan.get("nodes", {})
        step_results = dict(state.get("step_results", {}))
        loop_count = state.get("_supervisor_loop_count", 0)
        degraded_steps = state.get("_degraded_steps", set())
        plan_changed = state.get("_plan_changed", False)

        # ── Planner ──
        planner_span = Span(
            span_id="planner", parent_id="root",
            name="Planner 拆解", type="agent",
            status="success",
            metrics={"subtasks": len(nodes)},
        )
        trace.spans.append(planner_span)

        # ── Critique ──
        crit_span = Span(
            span_id="critique", parent_id="root",
            name="计划审查", type="agent",
            status="success",
            metrics={"plan_changed": plan_changed},
        )
        trace.spans.append(crit_span)

        # ── Supervisor Rounds ──
        for r in range(1, loop_count + 1):  # loop_count >= 1
            round_span = Span(
                span_id=f"supervisor-round-{r}", parent_id="root",
                name=f"调度轮次 {r}", type="workflow",
                status="success",
                metrics={"round": r},
            )
            trace.spans.append(round_span)

        # ── Skills（从 step_results 重建）──
        for step_id, sr in step_results.items():
            cap = sr.get("capability", "")
            status = sr.get("status", "pending")
            desc = sr.get("description", step_id)
            if status in ("pending", "running"):
                continue  # 未执行的不生成 span

            skill_span = Span(
                span_id=f"skill-{step_id}", parent_id="root",
                name=desc, type="agent",
                status=status if status in ("success", "error", "skipped") else "error",
                metrics={
                    "capability": cap,
                    "error": sr.get("error", ""),
                    "retry_count": sr.get("retries", 0),
                },
            )
            trace.spans.append(skill_span)

        # ── Reporter ──
        final_answer = state.get("final_answer", "")
        reporter_span = Span(
            span_id="reporter", parent_id="root",
            name="Reporter 汇总", type="agent",
            status="success" if final_answer else "error",
            metrics={"answer_len": len(final_answer)},
            output={"answer_preview": final_answer[:200]} if final_answer else None,
        )
        trace.spans.append(reporter_span)

        # ── Graph 拓扑 ──
        trace.graph = _build_graph_snapshot(state, loop_count, degraded_steps)

        # ── SLA ──
        trace.sla_threshold_ms = _sla_for_plan(nodes)

    # =====================================================
    # Tracing: stream_events 实时
    # =====================================================

    def stream_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,
    ) -> Generator[dict, None, None]:
        """SSE 流式处理。同步产出 trace + span 树。"""
        from backend.observability.tracer import trace_collector

        if not question or not question.strip():
            yield {"event": "error", "data": {"message": "请输入有效问题", "ts": time.time()}}
            return

        l1 = self._memory.start_session(session_id, question)
        from backend.orchestration.tools import set_session_id
        set_session_id(session_id)
        start_time = time.time()
        initial_state = make_initial_state(question, session_id, kb_id, l1.messages)

        # ── Tracing ──
        trace = trace_collector.start(question, session_id, workflow_name="agent")
        trace_collector.start_span("root", parent_id=None,
                                   name="多 Agent 协作管线", type="workflow",
                                   input={"question": question, "kb_id": kb_id})

        final_answer = ""
        all_step_results = {}
        current_plan = dict(initial_state.get("plan", {}))
        plan_changed = False

        try:
            for event in self._graph.stream(initial_state):
                if stop_event is not None and stop_event.is_set():
                    yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                    _end_root(trace, status="error", metrics={"reason": "user_abort"})
                    trace_collector.finish(trace, final_answer or "",
                                           int((time.time() - start_time) * 1000), "", "")
                    return

                node_name, node_output = _parse_event(event)
                if node_name is None:
                    continue

                yield {"event": "status", "data": {"node": node_name, "ts": time.time()}}
                yield from self._stream_node_events(node_name, node_output)

                if node_name in self._skill_nodes or node_name == "supervisor" \
                        or node_name in ("workflow_executor", "skill_executor"):
                    all_step_results.update(node_output.get("step_results", {}))
                elif node_name == "reporter":
                    final_answer = node_output.get("final_answer", "")
                elif node_name in ("planner", "critique"):
                    # 捕获 plan 用于 trace 重建
                    if node_output.get("plan"):
                        current_plan = node_output["plan"]
                    if node_output.get("_plan_changed"):
                        plan_changed = True

            yield from emit_delta_events(final_answer, stop_event)
            if stop_event is not None and stop_event.is_set():
                yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                return

            # ── Tracing: 重建 span 树（仅在未被中止时执行；stop 后直接退出，
            #    既避免错误地把半截内容持久化为最终答案，也避开不必要的 trace 重建）──
            state_for_trace = {
                "plan": current_plan,           # 从 Planner/Critique 捕获，非初始空 plan
                "step_results": all_step_results,
                "_supervisor_loop_count": _count_rounds_from_results(all_step_results),
                "_degraded_steps": set(),
                "_plan_changed": plan_changed,
                "final_answer": final_answer,
            }
            self._trace_from_state(trace, state_for_trace)
            _end_root(trace, metrics={"span_count": len(trace.spans) - 1})
            trace_collector.finish(trace, final_answer,
                                   int((time.time() - start_time) * 1000), "", "")

            yield make_done_event(final_answer, all_step_results, start_time)

        except Exception as e:
            logger.error(f"[MultiAgent] 流式执行失败: {e}")
            yield {"event": "error", "data": {"message": f"执行失败: {e}", "ts": time.time()}}
            try:
                _end_root(trace, status="error", metrics={"error": str(e)[:100]})
                trace_collector.finish(trace, final_answer or "",
                                       int((time.time() - start_time) * 1000), "", "")
            except Exception:
                pass
        finally:
            self._memory.end_turn(session_id, question, final_answer or "")


# =====================================================
# Tracing 辅助函数（模块级）
# =====================================================

def _end_root(trace, output: dict = None, metrics: dict = None,
              status: str = "success"):
    """查找并结束 root span。"""
    from backend.observability.tracer import trace_collector
    for sp in trace.spans:
        if sp.parent_id is None:
            trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
            return


def _build_graph_snapshot(state: dict, loop_count: int,
                          degraded_steps: set) -> dict:
    """从执行状态构建 LangGraph 拓扑快照。"""
    plan = state.get("plan", {})
    plan_nodes = plan.get("nodes", {})

    # 静态节点（始终运行）
    graph_nodes = [
        {"id": "planner", "label": "Planner 拆解"},
        {"id": "critique", "label": "计划审查"},
        {"id": "supervisor", "label": "Supervisor 调度"},
    ]
    # 动态节点（plan 中的步骤）
    for sid, node_info in plan_nodes.items():
        graph_nodes.append({
            "id": f"skill-{sid}",
            "label": node_info.get("description", sid),
        })
    graph_nodes.append({"id": "reporter", "label": "Reporter 汇总"})

    # 边（简化：planner→critique→supervisor→skills→supervisor→reporter）
    graph_edges = [
        {"source": "planner", "target": "critique"},
        {"source": "critique", "target": "supervisor"},
    ]
    for sid in plan_nodes:
        graph_edges.append({"source": "supervisor", "target": f"skill-{sid}", "label": "dispatch"})
        graph_edges.append({"source": f"skill-{sid}", "target": "supervisor", "label": "完成"})
    graph_edges.append({"source": "supervisor", "target": "reporter", "label": "all_done"})

    return {
        "nodes": graph_nodes,
        "edges": graph_edges,
        "max_loops": MAX_SUPERVISOR_LOOPS,
        "loop_count": loop_count,
        "degradation_triggered": len(degraded_steps) > 0 if degraded_steps else False,
    }


def _sla_for_plan(plan_nodes: dict) -> int:
    """根据计划复杂度估算 SLA 阈值（ms）。

    每步预留 LLM 调用 + DB 查询时间:
      - 1 步 (纯 RAG): 30s
      - 2 步 (SQL+分析): 60s
      - 3+ 步 (复杂编排): 90s
    """
    n = len(plan_nodes)
    if n <= 1:
        return 30000
    if n <= 2:
        return 60000
    return 90000


def _count_rounds_from_results(step_results: dict) -> int:
    """从 step_results 估算 supervisor 循环次数（SSE 流式路径用）。"""
    if not step_results:
        return 0
    # 简单启发：每有一个完成的步骤算 1 轮
    completed = sum(1 for sr in step_results.values()
                    if sr.get("status") in ("success", "failed", "skipped"))
    return max(1, completed)
