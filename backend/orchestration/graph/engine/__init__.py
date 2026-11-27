"""
backend/orchestration/graph/engine/ — AI 对话图运行核心（事件分发器与流水线驱动子系统）
"""
from __future__ import annotations

from backend.orchestration.graph.engine.dispatcher import EventDispatcher
from backend.orchestration.graph.engine.executor import (
    GraphWorkerExecutor,
    _parse_event,
)
from backend.orchestration.graph.engine.preflight import (
    _ANSWER_EVENT,
    _domain_node_names,
    _funnel_note_resolved,
    _is_confirmation_text,
    _is_explicit_handoff,
    _is_security_clarify,
    _is_user_balance_exhausted,
    _looks_like_refusal_text,
    _new_main_graph_thread_id,
    _pin_prompt_snapshot,
    _resolve_followup_for_request,
    _runner_error_event,
    _should_bypass_guard_for_human_relay,
    _should_exempt_guard_clarify,
    PreflightResult,
    run_preflight,
)
from backend.orchestration.graph.engine.trace_builder import (
    _build_graph_snapshot,
    _count_rounds_from_results,
    _end_root,
    _fallback_summary_from_results,
    _persist_cs_turn_if_needed,
    add_guard_span,
    finish_customer_service_guard_trace,
    finish_guard_trace,
    trace_from_state,
)

__all__ = [
    "EventDispatcher",
    "GraphWorkerExecutor",
    "PreflightResult",
    "run_preflight",
    "_parse_event",
    "_ANSWER_EVENT",
    "_domain_node_names",
    "_funnel_note_resolved",
    "_is_confirmation_text",
    "_is_explicit_handoff",
    "_is_security_clarify",
    "_is_user_balance_exhausted",
    "_looks_like_refusal_text",
    "_new_main_graph_thread_id",
    "_pin_prompt_snapshot",
    "_resolve_followup_for_request",
    "_runner_error_event",
    "_should_bypass_guard_for_human_relay",
    "_should_exempt_guard_clarify",
    "_build_graph_snapshot",
    "_count_rounds_from_results",
    "_end_root",
    "_fallback_summary_from_results",
    "_persist_cs_turn_if_needed",
    "add_guard_span",
    "finish_customer_service_guard_trace",
    "finish_guard_trace",
    "trace_from_state",
]
