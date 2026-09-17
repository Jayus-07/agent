"""runner.py — 图执行统一核心（GraphRunner）

ask() 与 stream_events() 的单一实现（重构 #2：收敛双轨编排）。

历史问题: MultiAgentSystem.ask()（同步）与 stream_events()（SSE）各自维护
一套「执行图 → 捕获 state → 重建 trace → 持久化」逻辑，语义差异（错误路径
是否 finish trace、final_answer 何时空）需要人肉对齐，改任何一边都要同步另一边。

现在: GraphRunner.iter_events() 产出统一事件流
    status / log / delta / _answer(内部) / done / error
  - stream_events()   → 逐个转发给 SSE（过滤 _answer 内部事件）
  - ask()             → 物化事件流，取 _answer 作为最终回答

执行语义要点（与原两轨对齐后的统一口径）:
  - Guard 拦截/澄清 → 短路话术，不进图
  - 用户中止 → error 事件，不产出 done，不落库
  - 图执行异常 → error 事件，trace 以 error 收尾，不产出 done
  - final_answer 为空（正常完成）→ 用 step_results 兜底汇总（两轨统一，
    旧 stream 路径空回答无提示属体验缺陷）
  - 仅 final_answer 非空才 memory.end_turn（避免历史出现空气泡/错误气泡）
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Generator

from backend.config import ENABLE_TOKEN_STREAMING, MAIN_GRAPH_RECURSION_LIMIT
from backend.infra.llm.proxy import reset_stream_sink
from backend.orchestration.graph.builder import _parse_event
from backend.orchestration.graph.events import (
    emit_delta_events,
    make_done_event,
    make_initial_state,
    make_step_log_event,
    make_step_payload,
    make_todo_event,
    make_usage_event,
    stream_node_events,
    summarize_turn_usage,
)
from backend.orchestration.request_context import RequestContext, put_context
from backend.security.input_guard import GuardAction, get_input_guard
from backend.shared.logger import logger

# _answer 是 runner → 调用方的内部事件（携带最终回答文本），
# 不属于 SSE 协议，stream_events 转发层必须过滤
_ANSWER_EVENT = "_answer"


def _is_explicit_handoff(question: str) -> bool:
    """是否为显式转人工表述（复用 handoff 关键词/正则表，零模型调用）。

    供 input_guard clarify 豁免判断；检测器不可用时返回 False（保持
    原短路行为，宁可多澄清一次也不误放行）。
    """
    try:
        from backend.customer_service.handoff import detect_handoff_trigger
        return detect_handoff_trigger(question) is not None
    except Exception:  # noqa: BLE001 — 检测失败按非转人工处理
        return False


def _is_confirmation_text(question: str) -> bool:
    """是否为确认/取消类短词（P3.5 豁免，同转人工豁免模式）。

    「确认/取消/是的/不了」等确认语义短词会被 guard 的模糊问题→clarify
    规则拦在图外，CS pending_handler 永远收不到（剧本 C 文本取消路径
    实测失效）。检测器不可用时返回 False 保持短路行为。
    """
    try:
        from backend.customer_service.confirmation import detect_confirmation_intent
        from backend.customer_service.confirmation import ConfirmationIntent
        return detect_confirmation_intent(question or "") != ConfirmationIntent.NONE
    except Exception:  # noqa: BLE001
        return False


def _domain_node_names() -> set[str]:
    """已注册域图的主图节点名集合（cs_graph_node / travel_graph_node / …）。

    runner 的输出消费逻辑按节点名白名单取 final_answer，域图节点必须
    在内，否则域图跑完的答案会被主图丢弃。用注册表而非硬编码：新增域
    图（domains/<x>/register.py）注册即生效，无需改 runner。
    """
    try:
        from backend.orchestration.domain_registry import domain_graph_registry
        return domain_graph_registry.get_node_names()
    except Exception:  # noqa: BLE001 — 注册表不可用时退回仅内置节点
        logger.debug("[GraphRunner] domain registry 不可用，按空域图节点集处理", exc_info=True)
        return set()


class GraphRunner:
    """统一图执行核心。"""

    def __init__(self, graph, memory, skill_nodes: set):
        self._graph = graph
        self._memory = memory
        self._skill_nodes = skill_nodes

    def iter_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,
        user_id: str = "default",
        department: str = "",
        *,
        fallback_deltas: bool = True,
        model: str = "",
        domain_hint: str = "",
    ) -> Generator[dict, None, None]:
        """执行图并产出统一事件流。

        Args:
            fallback_deltas: 无真流式 delta 时是否用假打字机兜底呈现
                （SSE 模式 True；ask 模式 False，事件流仅用于取答案）
            model: 按请求模型覆盖（空 = 全局 LLM_MODEL；非法名在 bind 时忽略）
            domain_hint: 入口域提示（customer_service = 客服窗口锁域，
                router_node 据此强制走 CS 预过滤并跳过其他域图 prefilter）
        """
        from backend.observability.tracer import SpanKind, trace_collector

        start_time = time.time()

        # ── Input Guard：输入侧门禁（Router/Planner 之前；拦截即短路不进图）──
        guard_result = get_input_guard().guard(question or "", session_id=session_id)
        if guard_result.action in (GuardAction.BLOCK, GuardAction.CLARIFY):
            # 显式转人工豁免（2026-09-17）：guard 的「模糊问题→clarify」规则
            # 会把「转人工/找真人客服」类硬意图短路在图外（13ms 内直接回澄清
            # 话术），cs_prefilter 直通层永远收不到。此处仅豁免体验层的
            # CLARIFY——改写为 ALLOW 放行进图，由 cs_prefilter 直通路由到
            # cs_handoff；BLOCK（安全拦截：注入/有害）不豁免。
            if (
                guard_result.action == GuardAction.CLARIFY
                and (
                    _is_explicit_handoff(question or "")
                    or _is_confirmation_text(question or "")
                )
            ):
                logger.info(
                    "[Runner] 显式转人工/确认意图豁免 input_guard clarify，放行进图: "
                    f"{(question or '')[:40]}"
                )
                guard_result = guard_result.model_copy(update={
                    "action": GuardAction.ALLOW,
                    "confidence": 1.0,
                    "reason": f"explicit_handoff_bypass: {guard_result.reason}",
                })
            else:
                finish_guard_trace(session_id, guard_result)
                yield {"event": "status", "data": {"node": "input_guard", "ts": time.time()}}
                message = guard_result.message or "## 提示\n\n无法处理该问题。"
                if fallback_deltas:
                    yield from emit_delta_events(message, stop_event)
                yield {"event": _ANSWER_EVENT, "data": {"answer": message}}
                yield make_done_event(message, {}, start_time)
                return

        # ── Tracing（提前到会话加载之前，使 memory/kb 加载耗时可归因）──
        trace = trace_collector.start(question, session_id, workflow_name="agent")
        trace_collector.start_span("root", parent_id=None,
                                   name="多 Agent 协作管线", type="workflow",
                                   input={"question": question, "kb_id": kb_id})
        add_guard_span(guard_result)

        # 会话/记忆加载埋点
        load_span = trace_collector.start_span(
            "session_load", name="会话/记忆加载", type="workflow",
            kind=SpanKind.KB_ROUTING.value,
            input={"session_id": session_id})
        try:
            l1 = self._memory.start_session(session_id, question, user_id=user_id)
            initial_state = make_initial_state(
                question, session_id, kb_id, l1.messages,
                guard_result=guard_result.model_dump(mode="json"),
                user_id=user_id, department=department,
                domain_hint=domain_hint,
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
            session_id=session_id, user_id=user_id, kb_id=kb_id,
            department=department, trace=trace, model=model)
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

        def _sink(text: str, kind: str = "answer") -> None:
            """proxy 流式 sink：生成 chunk 增量直接入队（worker/LangGraph 线程调用）。

            kind="thinking" → 推理模型思考链增量，走独立 thinking 事件；
            不置 streamed[0]（思考链不算回答输出，全思考零回答时仍走假打字机兜底）。
            """
            if stop_event is not None and stop_event.is_set():
                return
            if kind == "thinking":
                merged_q.put(("evt", {
                    "event": "thinking", "data": {"content": text, "ts": time.time()},
                }))
                return
            streamed[0] = True
            merged_q.put(("evt", {
                "event": "delta", "data": {"content": text, "ts": time.time()},
            }))

        request_ctx.stream_sink = _sink if ENABLE_TOKEN_STREAMING else None
        # 主图 checkpointer 感知：开启时 state 里只能放可序列化的 dict 形态
        # （trace/sink 进不了 checkpoint），节点入口经 get_context_from_state 还原
        has_checkpointer = getattr(self._graph, "checkpointer", None) is not None
        if has_checkpointer:
            initial_state["request_context"] = request_ctx.checkpoint_safe()
        else:
            put_context(initial_state, request_ctx)

        def _worker() -> None:
            # ContextVar 不跨线程：worker 入口整体绑定一次请求上下文；
            # Send 内部线程分支由节点入口的 bind_from_state 覆盖
            request_ctx.bind()
            try:
                # recursion_limit：超限时 LangGraph 抛 GraphRecursionError 而非
                # 静默挂起（supervisor 自身 10 轮上限之外的最后一道防线）。
                # thread_id：每轮唯一（session+毫秒），checkpoint 定位用于崩溃
                # 恢复/审计，不做跨轮状态合并（多轮记忆由 MemoryService 负责）
                invoke_config: dict = {"recursion_limit": MAIN_GRAPH_RECURSION_LIMIT}
                if has_checkpointer:
                    invoke_config["configurable"] = {
                        "thread_id": f"agent-{session_id}-{int(time.time() * 1000)}",
                    }
                for event in self._graph.stream(initial_state, config=invoke_config):
                    if stop_event is not None and stop_event.is_set():
                        ctx["aborted"] = True
                        break

                    node_name, node_output = _parse_event(event)
                    if node_name is None:
                        continue

                    merged_q.put(("evt", {"event": "status",
                                          "data": {"node": node_name, "ts": time.time()}}))
                    for evt in stream_node_events(
                            node_name, node_output, self._skill_nodes,
                            make_step_payload, make_step_log_event):
                        merged_q.put(("evt", evt))

                    # 域图节点（cs_graph_node / travel_graph_node / 未来新增域）
                    # 统一从注册表取——加新域不必回来改这份白名单。
                    # （曾漏掉 travel_graph_node：域图跑完产出 903 字行程，
                    # 主图却因白名单不含它而丢弃 final_answer，前端只看到
                    # 「未能获取任何有效数据」兜底文案。）
                    if node_name in self._skill_nodes or node_name == "supervisor" \
                            or node_name in ("workflow_executor", "skill_executor",
                                             "cs_knowledge", "cs_pending") \
                            or node_name in _domain_node_names():
                        ctx["all_step_results"].update(node_output.get("step_results", {}))
                        # direct/workflow executor 自己就是最终产出者
                        executor_answer = node_output.get("final_answer", "")
                        if executor_answer:
                            # 类型兜底:final_answer 必须是 str(direct executor
                            # 的结构化输出已在源头渲染,此处仅防御)
                            if not isinstance(executor_answer, str):
                                executor_answer = str(executor_answer)
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
                        # P3.1：等待确认的 pending_action → done 帧下发 CSConfirmCard
                        ctx["cs_pending_action"] = node_output.get("cs_pending_action") or None
                    elif node_name in ("planner", "critique"):
                        # 捕获 plan 用于 trace 重建
                        if node_output.get("plan"):
                            ctx["current_plan"] = node_output["plan"]
                        if node_output.get("_plan_changed"):
                            ctx["plan_changed"] = True

                    # P1: todo 快照 —— 任务列表变化节点跑完后，同步全量快照给前端。
                    # 位置在 all_step_results.update 之后：supervisor 轮次能带上最新步骤状态。
                    if node_name in ("planner", "critique", "supervisor"):
                        plan_nodes = (ctx.get("current_plan") or {}).get("nodes") or {}
                        if plan_nodes:
                            merged_q.put(("evt", make_todo_event(plan_nodes, ctx["all_step_results"])))
                    # P1: 流中用量 —— supervisor 每轮调度后透出累计用量（粒度 = supervisor 轮数）
                    if node_name == "supervisor":
                        usage_evt = make_usage_event()
                        if usage_evt:
                            merged_q.put(("evt", usage_evt))

                # 用量 ContextVar 在 worker 上下文累计，必须就地汇总
                ctx["usage"] = summarize_turn_usage()
            except Exception as e:
                logger.error(f"[GraphRunner] 流式执行失败: {e}", exc_info=True)
                ctx["worker_error"] = True
                import traceback as _tb
                _tb_tail = "\n".join(_tb.format_exc().splitlines()[-12:])
                merged_q.put(("evt", {"event": "error",
                                      "data": {"message": f"执行失败: {e}\n{_tb_tail}",
                                               "ts": time.time()}}))
            finally:
                reset_stream_sink()
                merged_q.put(("done", None))

        threading.Thread(target=_worker, daemon=True, name="graph-worker").start()

        try:
            while True:
                kind, evt = merged_q.get()
                if kind == "done":
                    break
                yield evt

            # ── 图执行结束后的收尾 ──
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

            answer = ctx["final_answer"]
            # 两轨统一：正常完成但无最终回答时，用 step_results 兜底汇总
            if not answer:
                answer = _fallback_summary_from_results(ctx["all_step_results"])

            # 未发生过真流式输出 → 兜底假打字机（guard/降级/非 LLM 路径仍有增量呈现）
            if fallback_deltas and not streamed[0] and answer:
                yield from emit_delta_events(answer, stop_event)
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
                "final_answer": answer,
                "route_mode": ctx["route_mode"],   # fix f8
                "cs_context": ctx["cs_context_snapshot"],
            }
            trace_from_state(trace, state_for_trace)
            _end_root(trace, metrics={"span_count": len(trace.spans) - 1})
            trace_collector.finish(trace, answer,
                                   int((time.time() - start_time) * 1000), "", "")

            _persist_cs_turn_if_needed(
                ctx["cs_context_snapshot"], session_id, question, answer, trace.id,
            )

            # 内部事件：ask() 从这里取最终回答（SSE 层过滤）
            yield {"event": _ANSWER_EVENT, "data": {"answer": answer}}
            yield make_done_event(answer, ctx["all_step_results"], start_time,
                                  usage=ctx["usage"],
                                  pending_action=ctx.get("cs_pending_action"))

        except Exception as e:
            import traceback as _tb
            logger.error(f"[GraphRunner] 流式执行失败: {e}", exc_info=True)
            _tb_tail = "\n".join(_tb.format_exc().splitlines()[-12:])
            yield {"event": "error",
                   "data": {"message": f"执行失败: {e}\n{_tb_tail}", "ts": time.time()}}
            try:
                _end_root(trace, status="error", metrics={"error": str(e)[:100]})
                trace_collector.finish(trace, ctx["final_answer"] or "",
                                       int((time.time() - start_time) * 1000), "", "")
            except Exception:
                logger.debug("[P1-10] 错误路径 trace 收尾失败", exc_info=True)
        finally:
            # 中止/早期失败路径 final_answer 为空：不落库，避免历史恢复时出现空气泡
            # CS 轮次不落主库：客服域已由 _persist_cs_turn_if_needed 独家落库
            # （customer_service.conversations/messages），再写 chat_sessions 会让
            # 客服会话泄漏进主历史侧栏（「我想转接人工客服」混入任务列表的根因）
            if ctx["final_answer"] and not (
                ctx["cs_context_snapshot"].get("conversation_id")
            ):
                self._memory.end_turn(session_id, question, ctx["final_answer"],
                                      user_id=user_id)


# =====================================================
# Guard trace 辅助
# =====================================================

def add_guard_span(guard_result) -> None:
    """把 Input Guard 判定记录为 span（挂在 root 下，埋点失败不影响主流程）。"""
    from backend.observability.tracer import trace_collector
    try:
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
        logger.debug("[GraphRunner] Guard span 记录失败", exc_info=True)


def finish_guard_trace(session_id: str, guard_result) -> None:
    """为被 Guard 拦截的请求产出一条最小 trace（留 rejected 证据）。

    安全约束：trace 的 question 不落拦截请求原文（可能是恶意/敏感内容），
    用类别占位符替代；原文取证信息在 Guard 审计日志（sha256 摘要）中。
    """
    from backend.observability.tracer import trace_collector
    try:
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
        logger.debug("[GraphRunner] Guard 拦截 trace 记录失败", exc_info=True)


# =====================================================
# 持久化 / trace 收尾辅助
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
        logger.debug("[GraphRunner] CS turn persistence failed", exc_info=True)


def _end_root(trace, output: dict = None, metrics: dict = None,
              status: str = "success"):
    """查找并结束 root span。"""
    from backend.observability.tracer import trace_collector
    for sp in trace.spans:
        if sp.parent_id is None:
            trace_collector.end_span(sp, output=output, metrics=metrics, status=status)
            return


def _fallback_summary_from_results(step_results: dict) -> str:
    """final_answer 为空时，用 step_results 兜底汇总（原 ask._fallback_summary）。"""
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


def _count_rounds_from_results(step_results: dict) -> int:
    """从 step_results 估算 supervisor 循环次数（事件流路径用）。"""
    if not step_results:
        return 0
    # 简单启发：每有一个完成的步骤算 1 轮
    completed = sum(1 for sr in step_results.values()
                    if sr.get("status") in ("success", "failed", "skipped"))
    return max(1, completed)


def trace_from_state(trace, state: dict):
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


def _build_graph_snapshot(state: dict, loop_count: int,
                          degraded_steps: set) -> dict:
    """从执行状态构建 LangGraph 拓扑快照（按实际路由模式区分）。

    fix f8：旧实现固定输出 planner→critique→supervisor 拓扑，
    direct/workflow 模式实际未走这些节点，前端展示的拓扑与真实执行
    路径不符。现按 route_mode 分支构建。
    """
    from backend.orchestration.supervisor.scheduler import MAX_SUPERVISOR_LOOPS

    route_mode = state.get("route_mode") or "plan"
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
