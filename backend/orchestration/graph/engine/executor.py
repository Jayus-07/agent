"""
backend/orchestration/graph/engine/executor.py — 后台图执行线程、节点事件捕获与用量流式汇聚
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from contextvars import copy_context
from typing import Any

from backend.config import MAIN_GRAPH_RECURSION_LIMIT
from backend.context_budget.errors import ContextBudgetExceededError
from backend.core.request_context import RequestCancelled, RequestContext, bind_cancel_event
from backend.orchestration.graph.engine.preflight import (
    _domain_node_names,
    _is_user_balance_exhausted,
    _new_main_graph_thread_id,
    _runner_error_event,
)
from backend.orchestration.graph.events import (
    make_step_log_event,
    make_step_payload,
    make_todo_event,
    make_usage_event,
    stream_node_events,
    summarize_turn_usage,
)
from backend.shared.error_protocol import sse_error_event
from backend.shared.logger import logger


def _parse_event(event: dict) -> tuple[str | None, dict]:
    """解析 LangGraph 流事件：提取节点名和节点输出 dict。"""
    for k, v in event.items():
        if isinstance(v, dict):
            return k, v
    return None, {}


class GraphWorkerExecutor:
    """管理在独立线程中执行 LangGraph 图生命周期与事件捕获。"""

    def __init__(
        self,
        graph: Any,
        skill_nodes: set[str],
        initial_state: dict,
        request_ctx: RequestContext,
        context_event_scope: Any,
        merged_q: queue.Queue,
        stop_event: threading.Event,
        task_handoff: Any,
        ctx: dict,
        trace: Any,
        start_time: float,
        entry_started: float,
    ) -> None:
        self.graph = graph
        self.skill_nodes = skill_nodes
        self.initial_state = initial_state
        self.request_ctx = request_ctx
        self.context_event_scope = context_event_scope
        self.merged_q = merged_q
        self.stop_event = stop_event
        self.task_handoff = task_handoff
        self.ctx = ctx
        self.trace = trace
        self.start_time = start_time
        self.entry_started = entry_started

    def start(self) -> threading.Thread:
        """启动后台图执行线程。"""
        self.trace.metadata["entry_timing"]["worker_launch_ms"] = round(
            (time.perf_counter() - self.entry_started) * 1000, 1,
        )
        worker_context = copy_context()
        thread = threading.Thread(
            target=worker_context.run,
            args=(self._worker,),
            daemon=True,
            name="graph-worker",
        )
        thread.start()
        return thread

    def _worker(self) -> None:
        """在 worker 线程中执行 LangGraph，捕获事件与异常。"""
        _scope_token = None
        _sink_token = None
        _sse_sink_token = None
        try:
            self.request_ctx.bind()
            bind_cancel_event(self.stop_event)

            from backend.context_budget.metrics import (
                reset_context_event_scope,
                reset_context_sink,
                set_context_event_scope,
                set_context_sink,
            )
            from backend.orchestration.graph.sse_event_sink import (
                bind_sse_event_sink,
                reset_sse_event_sink,
            )

            def _context_sink(evt: dict) -> None:
                if self.ctx.get("sse_events_closed"):
                    return
                if self.stop_event is not None and self.stop_event.is_set():
                    return
                self.merged_q.put(("evt", {"event": "context", "data": evt}))

            def _sse_event_sink(evt: dict) -> None:
                if self.ctx.get("sse_events_closed"):
                    return
                if self.stop_event is not None and self.stop_event.is_set():
                    return
                self.merged_q.put(("evt", evt))

            _scope_token = set_context_event_scope(self.context_event_scope)
            _sink_token = set_context_sink(
                _context_sink, scope=self.context_event_scope,
            )
            _sse_sink_token = bind_sse_event_sink(_sse_event_sink)
        except Exception as e:
            logger.error(f"[GraphRunner] worker 绑定失败: {e}", exc_info=True)
            self.ctx["worker_error"] = True
            event, error_code = _runner_error_event(e)
            self.ctx["worker_error_code"] = error_code
            self.merged_q.put(("evt", event))

            from backend.context_budget.metrics import (
                reset_context_event_scope as _reset_context_event_scope,
                reset_context_sink as _reset_context_sink,
            )
            from backend.orchestration.graph.sse_event_sink import (
                reset_sse_event_sink as _reset_sse_event_sink,
            )

            for token, reset in (
                (_sse_sink_token, _reset_sse_event_sink),
                (_sink_token, _reset_context_sink),
                (_scope_token, _reset_context_event_scope),
            ):
                if token is not None:
                    try:
                        reset(token)
                    except Exception:
                        pass
            self.merged_q.put(("done", None))
            return

        try:
            invoke_config: dict = {"recursion_limit": MAIN_GRAPH_RECURSION_LIMIT}
            has_checkpointer = getattr(self.graph, "checkpointer", None) is not None
            if has_checkpointer:
                invoke_config["configurable"] = {
                    "thread_id": _new_main_graph_thread_id(),
                }
            from backend.orchestration.checkpoint.chat_handoff import stream_with_handoff

            for event in stream_with_handoff(
                self.graph, self.initial_state, invoke_config, self.task_handoff,
            ):
                if self.stop_event is not None and self.stop_event.is_set():
                    self.ctx["aborted"] = True
                    break

                if "__task_handoff" in event:
                    self.ctx["background_task"] = event["__task_handoff"]
                    break

                node_name, node_output = _parse_event(event)
                if node_name is None:
                    continue

                if node_name in self.skill_nodes or node_name in (
                    "skill_executor", "workflow_executor",
                ):
                    self.merged_q.put(("evt", {"event": "log", "data": {
                        "level": "info",
                        "node": node_name,
                        "step_id": "tool_start",
                        "message": "正在调用数据工具" if node_name in self.skill_nodes
                        else "正在执行任务步骤",
                        "payload": {
                            "phase": "tool_start",
                            "tool": node_name,
                        },
                        "ts": time.time(),
                    }}))

                for evt in stream_node_events(
                    node_name, node_output, self.skill_nodes,
                    make_step_payload, make_step_log_event,
                ):
                    self.merged_q.put(("evt", evt))

                if (
                    node_name in self.skill_nodes
                    or node_name == "supervisor"
                    or node_name in (
                        "workflow_executor", "skill_executor",
                        "cs_knowledge", "cs_pending", "general_chat",
                    )
                    or node_name in _domain_node_names()
                ):
                    self.ctx["all_step_results"].update(node_output.get("step_results", {}))
                    executor_answer = node_output.get("final_answer", "")
                    if executor_answer:
                        if not isinstance(executor_answer, str):
                            executor_answer = str(executor_answer)
                        self.ctx["final_answer"] = executor_answer
                        self.ctx["answer_source"] = node_name
                elif node_name == "reporter":
                    reporter_answer = node_output.get("final_answer", "")
                    if reporter_answer and (
                        self.ctx["route_mode"] in {"direct", "workflow"}
                        or not self.ctx["final_answer"]
                    ):
                        self.ctx["final_answer"] = reporter_answer
                        self.ctx["answer_source"] = "reporter"
                elif node_name == "router":
                    if node_output.get("route_mode"):
                        self.ctx["route_mode"] = node_output["route_mode"]
                    if node_output.get("cs_context"):
                        self.ctx["cs_context_snapshot"] = node_output["cs_context"]
                elif node_name == "cs_graph_node":
                    if node_output.get("cs_context"):
                        self.ctx["cs_context_snapshot"] = node_output["cs_context"]
                    self.ctx["cs_pending_action"] = node_output.get("cs_pending_action") or None
                elif node_name in ("planner", "critique"):
                    if node_output.get("plan"):
                        self.ctx["current_plan"] = node_output["plan"]
                    if node_output.get("_plan_changed"):
                        self.ctx["plan_changed"] = True

                if node_name in ("planner", "critique", "supervisor"):
                    plan_nodes = (self.ctx.get("current_plan") or {}).get("nodes") or {}
                    if plan_nodes:
                        self.merged_q.put((
                            "evt",
                            make_todo_event(plan_nodes, self.ctx["all_step_results"]),
                        ))
                if node_name == "supervisor":
                    usage_evt = make_usage_event()
                    if usage_evt:
                        self.merged_q.put(("evt", usage_evt))

            self.ctx["usage"] = summarize_turn_usage()
        except ContextBudgetExceededError as e:
            logger.warning(f"[GraphRunner] 输入超预算被硬门禁拒绝: {e}")
            self.ctx["worker_error"] = True
            budget_data = e.to_sse_data()
            budget_data.setdefault("ts", time.time())
            self.merged_q.put(("evt", {"event": "error", "data": budget_data}))
        except RequestCancelled as e:
            logger.info(f"[GraphRunner] 图执行被用户中止: stage={e.stage}")
            self.ctx["aborted"] = True
        except (asyncio.TimeoutError, TimeoutError) as e:
            logger.error(f"[GraphRunner] 执行超时: {e}", exc_info=True)
            self.ctx["worker_error"] = True
            self.ctx["worker_error_code"] = "TIMEOUT"
            self.merged_q.put(("evt", sse_error_event(e)))
        except Exception as e:
            if _is_user_balance_exhausted(e):
                logger.info("[GraphRunner] 用户模型用量余额不足，暂停当前执行")
                self.ctx["worker_paused"] = True
            else:
                logger.error(f"[GraphRunner] 流式执行失败: {e}", exc_info=True)
                self.ctx["worker_error"] = True
                event, error_code = _runner_error_event(e)
                self.ctx["worker_error_code"] = error_code
                self.merged_q.put(("evt", event))
        finally:
            self.ctx["sse_events_closed"] = True
            from backend.context_budget.metrics import (
                reset_context_event_scope,
                reset_context_sink,
            )
            from backend.infra.llm.proxy import reset_stream_sink
            from backend.orchestration.graph.sse_event_sink import reset_sse_event_sink

            reset_sse_event_sink(_sse_sink_token)
            reset_context_sink(_sink_token)
            reset_context_event_scope(_scope_token)
            reset_stream_sink()
            self.merged_q.put(("done", None))
