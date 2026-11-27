"""runner.py — 图执行统一核心（GraphRunner 门面层）

核心职责:
  - 作为 Agent 对话执行的总入口（Facade 门面）；
  - 采用 Pipeline 与 Event Dispatcher 模式，内部异构职责物理拆解至 `engine/` 子包：
    - `engine/preflight.py`: 请求级前置安全门禁、FollowUp 语义解析与初始状态装配；
    - `engine/executor.py`: LangGraph 工作线程执行、节点输出拦截与用量流式汇聚；
    - `engine/dispatcher.py`: 前台队列事件消费、假打字机兜底与终态生命周期收尾；
    - `engine/trace_builder.py`: 执行状态到 Trace 树的重建、Guard Span 与拓扑快照构建。
  - 本模块 100% 保持向后兼容导出所有内部与公共契约符号。
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Generator

from backend.config import ENABLE_TOKEN_STREAMING, MAIN_GRAPH_RECURSION_LIMIT
from backend.context_budget.errors import ContextBudgetExceededError
from backend.core.request_context import RequestCancelled
from backend.infra.llm.proxy import reset_stream_sink
from backend.orchestration.graph.builder import _parse_event
from backend.orchestration.graph.engine import (
    _ANSWER_EVENT,
    _build_graph_snapshot,
    _count_rounds_from_results,
    _domain_node_names,
    _end_root,
    _fallback_summary_from_results,
    _funnel_note_resolved,
    _is_confirmation_text,
    _is_explicit_handoff,
    _is_security_clarify,
    _is_user_balance_exhausted,
    _looks_like_refusal_text,
    _new_main_graph_thread_id,
    _persist_cs_turn_if_needed,
    _pin_prompt_snapshot,
    _resolve_followup_for_request,
    _runner_error_event,
    _should_bypass_guard_for_human_relay,
    _should_exempt_guard_clarify,
    add_guard_span,
    finish_customer_service_guard_trace,
    finish_guard_trace,
    trace_from_state,
    EventDispatcher,
    GraphWorkerExecutor,
    PreflightResult,
    run_preflight,
)
from backend.orchestration.graph.events import (
    emit_delta_events,
    extract_answer_presentation_metadata,
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
from backend.security.input_guard import (
    GuardAction,
    GuardCategory,
    RiskLevel,
    get_input_guard,
)
from backend.shared.error_protocol import (
    ErrorCode,
    ProtocolError,
    error_envelope_from_exception,
    sse_cancelled_event,
    sse_error_event,
)
from backend.shared.logger import logger


class GraphRunner:
    """管理 LangGraph 执行与事件流生产。"""

    def __init__(self, graph, memory, skill_nodes: set[str]):
        self._graph = graph
        self._memory = memory
        self._skill_nodes = skill_nodes

    @_pin_prompt_snapshot
    def iter_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,
        user_id: str = "default",
        department: str = "",
        permissions: tuple[str, ...] | None = None,
        *,
        fallback_deltas: bool = True,
        model: str = "",
        domain_hint: str = "",
        tenant_id: str = "",
        idempotency_key: str = "",
        roles: tuple[str, ...] = (),
        product_id: str = "internal",
        product_source: str = "internal_call",
        persist_memory: bool = True,
        task_handoff=None,
        cs_entry_order_no: str = "",
    ) -> Generator[dict, None, None]:
        """执行图并产出统一事件流（协调 preflight -> executor -> dispatcher 三段式管道）。"""
        if stop_event is None:
            stop_event = threading.Event()

        # ── 阶段 1: Preflight 门禁、上下文组装与短路检查 ──
        preflight = run_preflight(
            memory=self._memory,
            graph=self._graph,
            question=question,
            session_id=session_id,
            kb_id=kb_id,
            stop_event=stop_event,
            user_id=user_id,
            department=department,
            permissions=permissions,
            fallback_deltas=fallback_deltas,
            model=model,
            domain_hint=domain_hint,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            roles=roles,
            product_id=product_id,
            product_source=product_source,
            persist_memory=persist_memory,
            cs_entry_order_no=cs_entry_order_no,
        )
        if preflight.short_circuit:
            yield from preflight.events
            return

        # ── 阶段 2: 启动后台图执行线程 ──
        executor = GraphWorkerExecutor(
            graph=self._graph,
            skill_nodes=self._skill_nodes,
            initial_state=preflight.initial_state,
            request_ctx=preflight.request_ctx,
            context_event_scope=preflight.context_event_scope,
            merged_q=preflight.merged_q,
            stop_event=stop_event,
            task_handoff=task_handoff,
            ctx=preflight.ctx,
            trace=preflight.trace,
            start_time=preflight.start_time,
            entry_started=preflight.entry_started,
        )
        executor.start()

        # ── 阶段 3: 前台事件队列消费、假打字机与终态收尾 ──
        dispatcher = EventDispatcher(
            merged_q=preflight.merged_q,
            ctx=preflight.ctx,
            trace=preflight.trace,
            start_time=preflight.start_time,
            stop_event=stop_event,
            memory=self._memory,
            product_session_id=preflight.product_session_id,
            session_id=session_id,
            question=question,
            user_id=user_id,
            tenant_id=tenant_id,
            product_id=product_id,
            idempotency_key=idempotency_key,
            clarify_click=preflight.clarify_click,
            fallback_deltas=fallback_deltas,
            streamed=preflight.streamed,
            persist_memory=persist_memory,
            initial_state=preflight.initial_state,
        )
        yield from dispatcher.dispatch()
