"""
system.py — MultiAgentSystem 运行时入口

提供 ask()（同步）和 stream_events()（SSE 流式）两种调用方式。

Tracing（2026-07-16）：ask() / stream_events() 自动产出 TraceRecord + Span 树，
通过 trace_collector 统一收集，API 层无需额外处理。
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Generator

from backend.config import ENABLE_TOKEN_STREAMING
from backend.orchestration.graph.builder import _parse_event, build_graph
from backend.orchestration.request_context import RequestContext, put_context
from backend.orchestration.graph.events import (
    emit_delta_events,
    extract_sources_from_results,
    make_done_event,
    summarize_turn_usage,
    make_initial_state,
    make_step_log_event,
    make_step_payload,
    stream_node_events,
)
from backend.orchestration.state import AgentState
from backend.orchestration.supervisor.scheduler import MAX_SUPERVISOR_LOOPS
from backend.orchestration.tool_registry import tool_registry
from backend.security.input_guard import GuardAction, get_input_guard
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

        t0 = time.time()
        try:
            from backend.orchestration.router.router import get_router
            _ = get_router()
            logger.info(f"[MultiAgent] Router 预热完成 ({(time.time()-t0)*1000:.0f}ms)")
        except Exception as e:
            logger.warning(f"[MultiAgent] Router 预热失败（非致命）: {e}")

        logger.info("[MultiAgent] 预热完毕，就绪")

    # =====================================================
    # 同步入口
    # =====================================================

    def ask(self, question: str, session_id: str = "default", kb_id: str = "default",
            user_id: str = "default") -> str:
        """处理用户问题，返回最终 Markdown 回答。"""
        logger.info(f"[MultiAgent] 收到问题: {(question or '')[:80]}... (session={session_id}, kb={kb_id}, user={user_id})")

        # ── Input Guard：输入侧门禁（Router/Planner 之前；拦截即短路不进图）──
        guard_result = get_input_guard().guard(question or "", session_id=session_id)
        if guard_result.action in (GuardAction.BLOCK, GuardAction.CLARIFY):
            self._finish_guard_trace(session_id, guard_result)
            return guard_result.message or "## 提示\n\n无法处理该问题。"

        # ── Tracing: start + root span（提前到会话加载之前，使 memory/kb
        #    加载耗时纳入 trace 归因，不再成为无埋点黑洞）──
        from backend.observability.tracer import SpanKind, trace_collector
        t_total = time.time()
        trace = trace_collector.start(question, session_id, workflow_name="agent")
        trace_collector.start_span("root", parent_id=None,
                                   name="多 Agent 协作管线", type="workflow",
                                   input={"question": question, "kb_id": kb_id})
        self._add_guard_span(guard_result)

        # P0-4: 会话/记忆加载埋点 —— 旧实现在 trace 之外执行，
        # 实测贡献过 9 秒不可归因耗时。
        load_span = trace_collector.start_span(
            "session_load", name="会话/记忆加载", type="workflow",
            kind=SpanKind.KB_ROUTING.value,
            input={"session_id": session_id})
        try:
            l1 = self._memory.start_session(session_id, question, user_id=user_id)
            initial_state = make_initial_state(
                question, session_id, kb_id, l1.messages,
                guard_result=guard_result.model_dump(mode="json"),
            )
            # 请求上下文随状态显式流动：节点入口（trace_middleware）从 state
            # 绑定 trace/session/user，LangGraph Send 分支也天然可达
            put_context(initial_state, RequestContext(
                session_id=session_id, user_id=user_id, kb_id=kb_id, trace=trace))
        except Exception as e:
            trace_collector.end_span(load_span, status="error",
                                     metrics={"error": str(e)[:100]})
            _end_root(trace, status="error", metrics={"error": "session_load_failed"})
            trace_collector.finish(trace, "[ERROR]",
                                   int((time.time() - t_total) * 1000), "", "")
            raise
        trace_collector.end_span(
            load_span, metrics={"history_messages": len(l1.messages)})

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

            _persist_cs_turn_if_needed(
                final_state.get("cs_context", {}),
                session_id, question, answer, trace.id,
            )

            self._memory.end_turn(session_id, question, answer, user_id=user_id)
            return answer
        except Exception as e:
            logger.error(f"[MultiAgent] 执行失败: {e}")
            try:
                _end_root(trace, status="error", metrics={"error": str(e)[:100]})
                trace_collector.finish(trace, "[ERROR]",
                                       int((time.time() - t_total) * 1000), "", "")
            except Exception:
                logger.debug("[P1-10] 错误路径 trace 收尾失败", exc_info=True)
            self._memory.end_turn(session_id, question, f"[错误] {e}", user_id=user_id)
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
        # fix f9：direct/workflow 模式未走 planner/critique/supervisor，
        # 不合成这些节点的占位 span（旧实现会产出 0ms planner 噪声 span）。
        route_mode = state.get("route_mode") or "plan"

        # 合成兜底策略（2026-08-21 浏览器实测整改）：
        # 中间件 span 命名为 {node_name} 或 {node_name}:{step_id}，只有节点真正
        # 执行过才会产生 span。因此「存在中间件 span」即执行证据：
        #   - 已有精确时长 span → 不再重复合成；
        #   - 无 span → 节点未执行（如 direct 模式跳过 planner/critique/supervisor）
        #     → 不合成 0ms 占位噪声 span。
        existing_ids = {s.span_id for s in trace.spans}

        def _has_node_span(node: str) -> bool:
            return any(sid == node or sid.startswith(f"{node}:") for sid in existing_ids)

        def _has_middleware_span(step_id: str) -> bool:
            # 中间件 skill span 命名：{node_name}:{step_id}
            return any(sid.endswith(f":{step_id}") for sid in existing_ids)

        # ── Planner（仅在 plan 模式执行过且中间件 span 缺失时补）──
        if route_mode == "plan" and nodes and not _has_node_span("planner"):
            planner_span = Span(
                span_id="planner", parent_id="root",
                name="Planner 拆解", type="agent",
                status="success",
                metrics={"subtasks": len(nodes), "synthesized": True},
            )
            trace.spans.append(planner_span)

        # ── Critique ──
        if route_mode == "plan" and state.get("_plan_critiqued") and not _has_node_span("critique"):
            crit_span = Span(
                span_id="critique", parent_id="root",
                name="计划审查", type="agent",
                status="success",
                metrics={"plan_changed": plan_changed, "synthesized": True},
            )
            trace.spans.append(crit_span)

        # ── Supervisor Rounds（direct/workflow 模式无调度轮次）──
        # 执行证据：plan 内的 step 才有 supervisor 派发（direct 模式的
        # direct_* step 由 skill_executor 直接执行，plan.nodes 为空 → 0 轮）。
        executed_rounds = sum(
            1 for sid, sr in step_results.items()
            if sid in nodes and sr.get("status", "pending") not in ("pending", "running")
        ) if route_mode == "plan" else 0
        for r in range(1, min(loop_count, executed_rounds) + 1):
            if _has_node_span("supervisor"):
                break
            round_span = Span(
                span_id=f"supervisor-round-{r}", parent_id="root",
                name=f"调度轮次 {r}", type="workflow",
                status="success",
                metrics={"round": r, "synthesized": True},
            )
            trace.spans.append(round_span)

        # ── Skills（从 step_results 重建）──
        for step_id, sr in step_results.items():
            cap = sr.get("capability", "")
            status = sr.get("status", "pending")
            desc = sr.get("description", step_id)
            if status in ("pending", "running"):
                continue  # 未执行的不生成 span
            if _has_middleware_span(step_id):
                continue  # 中间件已记录真实时长 span，不重复合成

            # error 兜底：失败步骤无错误详情时给出可读信息，
            # 避免前端看到 metrics.error='' 的空 error span。
            err = sr.get("error", "") or (
                f"步骤执行失败（状态={status}）" if status not in ("success", "skipped") else "")
            skill_span = Span(
                span_id=f"skill-{step_id}", parent_id="root",
                name=desc, type="agent",
                status=status if status in ("success", "error", "skipped") else "error",
                metrics={
                    "capability": cap,
                    "error": err,
                    "retry_count": sr.get("retries", 0),
                    "synthesized": True,
                },
            )
            trace.spans.append(skill_span)

        # ── Reporter（执行过但中间件 span 缺失才补）──
        final_answer = state.get("final_answer", "")
        if final_answer and not _has_node_span("reporter"):
            reporter_span = Span(
                span_id="reporter", parent_id="root",
                name="Reporter 汇总", type="agent",
                status="success" if final_answer else "error",
                metrics={"answer_len": len(final_answer), "synthesized": True},
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
        user_id: str = "default",
    ) -> Generator[dict, None, None]:
        """SSE 流式处理。同步产出 trace + span 树。

        P1 真 token 级流式：graph.stream 在 worker 线程执行，节点事件与
        LLM 生成增量（sink 回调）合并进同一队列，生成器逐个产出 ——
        skill 节点生成期间 delta 持续流出，TTFT 从"整图跑完"提前到
        "首个生成 chunk 到达"。无流式输出时（非 LLM 路径/降级）兜底
        emit_delta_events 假打字机，保证任何路径都有增量呈现。
        """
        from backend.observability.tracer import trace_collector

        start_time = time.time()

        # ── Input Guard：输入侧门禁（Router/Planner 之前；拦截即短路不进图）──
        guard_result = get_input_guard().guard(question or "", session_id=session_id)
        if guard_result.action in (GuardAction.BLOCK, GuardAction.CLARIFY):
            self._finish_guard_trace(session_id, guard_result)
            yield {"event": "status", "data": {"node": "input_guard", "ts": time.time()}}
            message = guard_result.message or "## 提示\n\n无法处理该问题。"
            yield from emit_delta_events(message, stop_event)
            yield make_done_event(message, {}, start_time)
            return

        # ── Tracing（提前到会话加载之前，使 memory/kb 加载耗时可归因）──
        trace = trace_collector.start(question, session_id, workflow_name="agent")
        trace_collector.start_span("root", parent_id=None,
                                   name="多 Agent 协作管线", type="workflow",
                                   input={"question": question, "kb_id": kb_id})
        self._add_guard_span(guard_result)

        # P0-4: 会话/记忆加载埋点（与同步 ask() 路径对齐）
        from backend.observability.tracer import SpanKind
        load_span = trace_collector.start_span(
            "session_load", name="会话/记忆加载", type="workflow",
            kind=SpanKind.KB_ROUTING.value,
            input={"session_id": session_id})
        try:
            l1 = self._memory.start_session(session_id, question, user_id=user_id)
            initial_state = make_initial_state(
                question, session_id, kb_id, l1.messages,
                guard_result=guard_result.model_dump(mode="json"),
            )
        except Exception as e:
            trace_collector.end_span(load_span, status="error",
                                     metrics={"error": str(e)[:100]})
            yield {"event": "error", "data": {"message": f"会话加载失败: {e}", "ts": time.time()}}
            _end_root(trace, status="error", metrics={"error": "session_load_failed"})
            trace_collector.finish(trace, "",
                                   int((time.time() - start_time) * 1000), "", "")
            return
        trace_collector.end_span(
            load_span, metrics={"history_messages": len(l1.messages)})

        # ── worker 线程执行图 + 事件合并队列 ──
        # merged_q 元素: ("evt", event_dict) 或 ("done", None) 哨兵
        merged_q: queue.Queue = queue.Queue()
        # 请求上下文：trace/sink 显式持有并随状态流动，Send 分支经
        # trace_middleware 从 state 重新绑定（ContextVar 不跨线程继承）
        request_ctx = RequestContext(
            session_id=session_id, user_id=user_id, kb_id=kb_id, trace=trace)
        ctx = {
            "final_answer": "",
            "all_step_results": {},
            "current_plan": dict(initial_state.get("plan", {})),
            "plan_changed": False,
            "route_mode": "plan",   # fix f8：捕获 Router 决策，trace 拓扑按模式构建
            "cs_context_snapshot": {},
            "usage": None,
            "aborted": False,       # 用户中止（worker 内检测）
            "worker_error": False,  # 图执行异常（worker 已 yield error 事件）
        }
        streamed = [False]  # 本轮是否发生过真流式 delta

        def _sink(text: str) -> None:
            """proxy 流式 sink：生成 chunk 增量直接入队（worker/LangGraph 线程调用）。"""
            if stop_event is not None and stop_event.is_set():
                return
            streamed[0] = True
            merged_q.put(("evt", {
                "event": "delta", "data": {"content": text, "ts": time.time()},
            }))

        request_ctx.stream_sink = _sink if ENABLE_TOKEN_STREAMING else None
        put_context(initial_state, request_ctx)

        def _worker() -> None:
            # ContextVar 不跨线程：worker 入口整体绑定一次请求上下文；
            # Send 内部线程分支由节点入口的 bind_from_state 覆盖
            request_ctx.bind()
            try:
                for event in self._graph.stream(initial_state):
                    if stop_event is not None and stop_event.is_set():
                        ctx["aborted"] = True
                        break

                    node_name, node_output = _parse_event(event)
                    if node_name is None:
                        continue

                    merged_q.put(("evt", {"event": "status",
                                          "data": {"node": node_name, "ts": time.time()}}))
                    for evt in self._stream_node_events(node_name, node_output):
                        merged_q.put(("evt", evt))

                    if node_name in self._skill_nodes or node_name == "supervisor" \
                            or node_name in ("workflow_executor", "skill_executor",
                                             "cs_knowledge", "cs_pending",
                                             "cs_graph_node"):
                        ctx["all_step_results"].update(node_output.get("step_results", {}))
                        # direct/workflow executor 自己就是最终产出者
                        executor_answer = node_output.get("final_answer", "")
                        if executor_answer:
                            ctx["final_answer"] = executor_answer
                    elif node_name == "reporter":
                        # reporter 只在 plan 模式下才是最终答案；direct/workflow 已由 executor 产出
                        if not ctx["final_answer"]:
                            ctx["final_answer"] = node_output.get("final_answer", "")
                    elif node_name == "router":
                        # fix f8：捕获路由模式供 trace 拓扑快照使用
                        if node_output.get("route_mode"):
                            ctx["route_mode"] = node_output["route_mode"]
                        # CS: 捕获 cs_context 供 trace 拓扑快照使用
                        if node_output.get("cs_context"):
                            ctx["cs_context_snapshot"] = node_output["cs_context"]
                    elif node_name == "cs_graph_node":
                        # Phase 4: CS Graph 适配器输出合并后的 cs_context
                        if node_output.get("cs_context"):
                            ctx["cs_context_snapshot"] = node_output["cs_context"]
                    elif node_name in ("planner", "critique"):
                        # 捕获 plan 用于 trace 重建
                        if node_output.get("plan"):
                            ctx["current_plan"] = node_output["plan"]
                        if node_output.get("_plan_changed"):
                            ctx["plan_changed"] = True
                # 用量 ContextVar 在 worker 上下文累计，必须就地汇总
                ctx["usage"] = summarize_turn_usage()
            except Exception as e:
                logger.error(f"[MultiAgent] 流式执行失败: {e}")
                ctx["worker_error"] = True
                merged_q.put(("evt", {"event": "error",
                                      "data": {"message": f"执行失败: {e}", "ts": time.time()}}))
            finally:
                from backend.infra.llm.proxy import reset_stream_sink
                reset_stream_sink()
                merged_q.put(("done", None))

        threading.Thread(target=_worker, daemon=True, name="graph-worker").start()

        try:
            while True:
                kind, evt = merged_q.get()
                if kind == "done":
                    break
                yield evt

            # ── 图执行结束后的收尾（原同步逻辑语义保持）──
            if ctx["aborted"]:
                yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                _end_root(trace, status="error", metrics={"reason": "user_abort"})
                trace_collector.finish(trace, ctx["final_answer"] or "",
                                       int((time.time() - start_time) * 1000), "", "")
                return
            if ctx["worker_error"]:
                try:
                    _end_root(trace, status="error", metrics={"error": "graph_failed"})
                    trace_collector.finish(trace, ctx["final_answer"] or "",
                                           int((time.time() - start_time) * 1000), "", "")
                except Exception:
                    logger.debug("[P1-10] 错误路径 trace 收尾失败", exc_info=True)
                return

            # 未发生过真流式输出 → 兜底假打字机（guard/降级/非 LLM 路径仍有增量呈现）
            if not streamed[0] and ctx["final_answer"]:
                yield from emit_delta_events(ctx["final_answer"], stop_event)
                if stop_event is not None and stop_event.is_set():
                    yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                    return

            # ── Tracing: 重建 span 树 ──
            state_for_trace = {
                "plan": ctx["current_plan"],       # 从 Planner/Critique 捕获，非初始空 plan
                "step_results": ctx["all_step_results"],
                "_supervisor_loop_count": _count_rounds_from_results(ctx["all_step_results"]),
                "_degraded_steps": set(),
                "_plan_changed": ctx["plan_changed"],
                "final_answer": ctx["final_answer"],
                "route_mode": ctx["route_mode"],   # fix f8
                "cs_context": ctx["cs_context_snapshot"],
            }
            self._trace_from_state(trace, state_for_trace)
            _end_root(trace, metrics={"span_count": len(trace.spans) - 1})
            trace_collector.finish(trace, ctx["final_answer"],
                                   int((time.time() - start_time) * 1000), "", "")

            _persist_cs_turn_if_needed(
                ctx["cs_context_snapshot"], session_id, question, ctx["final_answer"], trace.id,
            )

            yield make_done_event(ctx["final_answer"], ctx["all_step_results"], start_time,
                                  usage=ctx["usage"])

        except Exception as e:
            logger.error(f"[MultiAgent] 流式执行失败: {e}")
            yield {"event": "error", "data": {"message": f"执行失败: {e}", "ts": time.time()}}
            try:
                _end_root(trace, status="error", metrics={"error": str(e)[:100]})
                trace_collector.finish(trace, ctx["final_answer"] or "",
                                       int((time.time() - start_time) * 1000), "", "")
            except Exception:
                logger.debug("[P1-10] 错误路径 trace 收尾失败", exc_info=True)
        finally:
            # 中止/早期失败路径 final_answer 为空：不落库，避免历史恢复时出现空气泡
            if ctx["final_answer"]:
                self._memory.end_turn(session_id, question, ctx["final_answer"],
                                      user_id=user_id)

    # =====================================================
    # Input Guard 辅助（短路 trace / span）
    # =====================================================

    def _add_guard_span(self, guard_result) -> None:
        """把 Input Guard 判定记录为 span（挂在 root 下，埋点失败不影响主流程）。"""
        try:
            from backend.observability.tracer import trace_collector
            span = trace_collector.start_span(
                "input_guard", name="Input Guard", type="workflow",
                input={"query_len": len(guard_result.normalized_query)},
            )
            trace_collector.end_span(
                span,
                output={
                    "action": guard_result.action.value,
                    "category": guard_result.category.value,
                    "risk_level": guard_result.risk_level.value,
                    "confidence": guard_result.confidence,
                    "reason": guard_result.reason,
                },
                metrics={
                    "layer": guard_result.layer,
                    "llm_consulted": guard_result.llm_consulted,
                    "needs_permission": guard_result.needs_permission,
                    "domain": guard_result.domain or "",
                    "policy_version": guard_result.policy_version,
                },
                status="success",
            )
        except Exception:
            logger.debug("[MultiAgent] Guard span 记录失败", exc_info=True)

    def _finish_guard_trace(self, session_id: str, guard_result) -> None:
        """为被 Guard 拦截的请求产出一条最小 trace（留 rejected 证据）。

        安全约束：trace 的 question 不落拦截请求原文（可能是恶意/敏感内容），
        用类别占位符替代；原文取证信息在 Guard 审计日志（sha256 摘要）中。
        """
        try:
            from backend.observability.tracer import trace_collector
            trace = trace_collector.start(
                f"[guard-rejected:{guard_result.category.value}]",
                session_id, workflow_name="agent",
            )
            span = trace_collector.start_span(
                "input_guard", parent_id=None,
                name="Input Guard 拦截", type="workflow",
            )
            trace_collector.end_span(
                span,
                output={
                    "action": guard_result.action.value,
                    "category": guard_result.category.value,
                    "risk_level": guard_result.risk_level.value,
                    "confidence": guard_result.confidence,
                },
                metrics={"policy_version": guard_result.policy_version},
                status="success",
            )
            trace_collector.finish(
                trace, guard_result.message or "", 0, "", "",
            )
        except Exception:
            logger.debug("[MultiAgent] Guard 拦截 trace 记录失败", exc_info=True)


# =====================================================
# Tracing 辅助函数（模块级）
# =====================================================

def _persist_cs_turn_if_needed(
    cs_context: dict,
    session_id: str,
    question: str,
    answer: str,
    trace_id: str,
) -> None:
    """Fire-and-forget CS turn persistence — no-op when not a CS request."""
    if not cs_context or not cs_context.get("conversation_id"):
        return
    try:
        from backend.customer_service.conversation_store import record_cs_turn
        record_cs_turn(
            conversation_id=cs_context["conversation_id"],
            user_id=cs_context.get("authenticated_user_id", "anonymous"),
            question=question,
            answer=answer,
            trace_id=trace_id,
            cs_route=cs_context.get("cs_route"),
        )
    except Exception:
        logger.debug("[MultiAgent] CS turn persistence failed", exc_info=True)


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
    """从执行状态构建 LangGraph 拓扑快照（按实际路由模式区分）。

    fix f8：旧实现固定输出 planner→critique→supervisor 拓扑，
    direct/workflow 模式实际未走这些节点，前端展示的拓扑与真实执行
    路径不符。现按 route_mode 分支构建。
    """
    route_mode = state.get("route_mode") or "plan"

    # ── direct / workflow 模式：Router → Executor → Reporter ──
    if route_mode in ("direct", "workflow"):
        executor_id = "skill_executor" if route_mode == "direct" else "workflow_executor"
        executor_label = "直接执行" if route_mode == "direct" else "工作流执行"
        graph_nodes = [
            {"id": "router", "label": "路由决策"},
            {"id": executor_id, "label": executor_label},
            {"id": "reporter", "label": "Reporter 汇总"},
        ]
        graph_edges = [
            {"source": "router", "target": executor_id, "label": route_mode},
            {"source": executor_id, "target": "reporter", "label": "完成"},
        ]
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "max_loops": MAX_SUPERVISOR_LOOPS,
            "loop_count": loop_count,
            "degradation_triggered": len(degraded_steps) > 0 if degraded_steps else False,
        }

    # ── 域图模式：Router → 域图节点 → END（通过 registry 动态查找）──
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get(route_mode)
    if domain:
        graph_nodes = [
            {"id": "router", "label": "路由决策"},
            {"id": domain.node_name, "label": domain.label},
        ]
        graph_edges = [
            {"source": "router", "target": domain.node_name, "label": route_mode},
        ]
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "max_loops": 0,
            "loop_count": 0,
            "degradation_triggered": False,
        }

    # ── plan 模式：Router → Planner → Critique → Supervisor → Skills → Reporter ──
    plan = state.get("plan", {})
    plan_nodes = plan.get("nodes", {})

    graph_nodes = [
        {"id": "router", "label": "路由决策"},
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

    graph_edges = [
        {"source": "router", "target": "planner", "label": "plan"},
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
