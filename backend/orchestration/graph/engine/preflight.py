"""
backend/orchestration/graph/engine/preflight.py — 请求前置门禁、安全澄清豁免与会话上下文装配
"""
from __future__ import annotations

import logging
import time
import uuid
from functools import wraps
from typing import Any, Generator

from backend.shared.error_protocol import (
    ErrorCode,
    ProtocolError,
    error_envelope_from_exception,
    sse_error_event,
)
from backend.orchestration.graph.events import make_done_event
from backend.security.input_guard import (
    GuardAction,
    GuardCategory,
    RiskLevel,
    get_input_guard,
)
from backend.shared.logger import logger

_SECURITY_CLARIFY_CATEGORIES = frozenset({
    GuardCategory.PROMPT_INJECTION,
    GuardCategory.HARMFUL,
    GuardCategory.SENSITIVE_DATA,
})

_ANSWER_EVENT = "_answer"


def _get_input_guard():
    try:
        import backend.orchestration.graph.runner as runner_mod
        fn = getattr(runner_mod, "get_input_guard", None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    from backend.security.input_guard import get_input_guard
    return get_input_guard()


def _emit_delta_events(*args, **kwargs):
    try:
        import backend.orchestration.graph.runner as runner_mod
        fn = getattr(runner_mod, "emit_delta_events", None)
        if callable(fn):
            return fn(*args, **kwargs)
    except Exception:
        pass
    from backend.orchestration.graph.events import emit_delta_events
    return emit_delta_events(*args, **kwargs)


def _is_user_balance_exhausted(exc: Exception) -> bool:
    """只将真实用户账本余额不足识别为暂停，租户额度和配置错误仍报错。"""
    try:
        from backend.infra.llm.quota import QuotaExceeded
    except Exception:
        return False
    if not isinstance(exc, QuotaExceeded):
        return False
    scope = str(getattr(exc, "subject_id", "")).partition(":")[0]
    return scope == "user"


def _runner_error_event(exc: Exception) -> tuple[dict, str]:
    """Runner 终态按协议封套分类，内置参数异常在图执行边界视为内部错误。"""
    envelope = error_envelope_from_exception(exc, source="sse")
    code = envelope.code
    if not isinstance(exc, ProtocolError) and code in (
        ErrorCode.INVALID_PARAM,
        ErrorCode.NOT_FOUND,
    ):
        code = ErrorCode.INTERNAL_ERROR
    terminal_exc = (
        exc if isinstance(exc, ProtocolError) or code != ErrorCode.INTERNAL_ERROR
        else ProtocolError(ErrorCode.INTERNAL_ERROR, source="sse")
    )
    return sse_error_event(terminal_exc, source="sse"), code.value


def _pin_prompt_snapshot(iter_fn):
    """在图生成器整个生命周期内固定 Prompt 模板。"""
    @wraps(iter_fn)
    def wrapper(self, *args, **kwargs):
        from backend.prompts.hot_reload import ensure_prompt_snapshot_fresh
        from backend.prompts.service import prompt_service

        try:
            ensure_prompt_snapshot_fresh()
        except Exception:  # noqa: BLE001 - 热更新旁路不得阻断请求
            logger.debug("[GraphRunner] Prompt Runtime freshness check skipped", exc_info=True)
        with prompt_service.pin_snapshot():
            yield from iter_fn(self, *args, **kwargs)

    return wrapper


def _resolve_followup_for_request(
    *,
    trace,
    raw_question: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    l1_messages: list,
) -> str | None:
    """P2.7：Router 之前的统一 follow-up 解析（全局入口专用）。"""
    try:
        from langchain_core.messages import HumanMessage

        from backend.orchestration.context.conversation_context import (
            ConversationContext,
        )
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
            get_conversation_context_repository,
        )
        from backend.orchestration.context.follow_up_resolver import (
            resolve_followup,
        )

        last_user_turn = ""
        for msg in reversed(l1_messages or []):
            if isinstance(msg, HumanMessage):
                last_user_turn = (msg.content or "").strip()
                break

        repo = get_conversation_context_repository()
        ctx = repo.get(tenant_id, user_id, session_id)
        if ctx is None:
            ctx = ConversationContext(tenant_id=tenant_id or "",
                                      user_id=user_id or "",
                                      conversation_id=session_id or "")
        resolution = resolve_followup(raw_question, ctx, last_user_turn)

        try:
            trace.tags["follow_up_detected"] = str(
                resolution["follow_up_detected"]).lower()
            trace.tags["rewrite_method"] = resolution["rewrite_method"]
            trace.tags["context_slots_used"] = ",".join(
                resolution["used_context"][:5])
            if resolution["need_clarification"]:
                trace.tags["need_clarification"] = "true"
        except Exception:
            pass

        write_payload: dict = {}
        if resolution.get("overwrite_destination"):
            write_payload["overwrite_destination"] = resolution[
                "overwrite_destination"]
        elif resolution.get("follow_up_detected") and resolution.get("resolved"):
            write_payload = {"resolved": True,
                             "topic": (resolution.get("standalone_query") or "")[:40]}
        if write_payload:
            repo.mutate(tenant_id, user_id, session_id, ContextMutation(
                MutationType.APPLY_FOLLOWUP_RESOLUTION, write_payload))

        if resolution["need_clarification"]:
            logger.info(
                "[Runner] follow-up 需要澄清: raw=%s used=%s",
                resolution["raw_query"][:40], resolution["used_context"])
            return None

        standalone = resolution["standalone_query"] or raw_question
        if standalone != raw_question:
            logger.info(
                "[Runner] follow-up 解析: %r → %r (method=%s, used=%s)",
                raw_question[:40], standalone[:60],
                resolution["rewrite_method"], resolution["used_context"])
        return standalone
    except Exception as exc:  # noqa: BLE001 — 解析失败回退原文
        logger.warning("[Runner] follow-up 解析失败，回退原文: %s", exc)
        return raw_question


def _should_bypass_guard_for_human_relay(
    guard_result,
    *,
    domain_hint: str,
    user_id: str,
    session_id: str,
) -> bool:
    """判断人工接管期是否可以绕过低风险体验型澄清。"""
    if domain_hint != "customer_service":
        return False
    if guard_result.action != GuardAction.CLARIFY:
        return False
    if guard_result.risk_level != RiskLevel.LOW:
        return False
    if guard_result.category not in {
        GuardCategory.GARBAGE,
        GuardCategory.AMBIGUOUS,
    }:
        return False

    try:
        from backend.orchestration.graph.cs_prefilter import (
            _active_relay_state,
        )

        return _active_relay_state(user_id, session_id) is not None
    except Exception:
        logger.debug(
            "[GraphRunner] human relay state lookup failed",
            exc_info=True,
        )
        return False


def _is_security_clarify(guard_result) -> bool:
    """安全类澄清不参与体验豁免（审计 #71）。"""
    return (
        guard_result.category in _SECURITY_CLARIFY_CATEGORIES
        and guard_result.risk_level != RiskLevel.LOW
    )


def _should_exempt_guard_clarify(
    guard_result,
    question: str,
    *,
    domain_hint: str,
    user_id: str,
    session_id: str,
) -> bool:
    """转人工/确认/人工接管期是否豁免 CLARIFY（安全类不豁免）。"""
    if guard_result.action != GuardAction.CLARIFY:
        return False
    if _is_security_clarify(guard_result):
        return False
    return (
        _is_explicit_handoff(question or "")
        or _is_confirmation_text(question or "")
        or _should_bypass_guard_for_human_relay(
            guard_result,
            domain_hint=domain_hint,
            user_id=user_id,
            session_id=session_id,
        )
    )


def _is_explicit_handoff(question: str) -> bool:
    """是否为显式转人工表述（复用 handoff 关键词/正则表，零模型调用）。"""
    try:
        from backend.customer_service.handoff import detect_handoff_trigger
        return detect_handoff_trigger(question) is not None
    except Exception:  # noqa: BLE001 — 检测失败按非转人工处理
        return False


def _is_confirmation_text(question: str) -> bool:
    """是否为确认/取消类短词（P3.5 豁免，同转人工豁免模式）。"""
    try:
        from backend.customer_service.confirmation import (
            ConfirmationIntent,
            detect_confirmation_intent,
        )
        return detect_confirmation_intent(question or "") != ConfirmationIntent.NONE
    except Exception:  # noqa: BLE001
        return False


def _domain_node_names() -> set[str]:
    """已注册域图的主图节点名集合（cs_graph_node / travel_graph_node / …）。"""
    try:
        from backend.orchestration.domain_registry import domain_graph_registry
        return domain_graph_registry.get_node_names()
    except Exception:  # noqa: BLE001 — 注册表不可用时退回仅内置节点
        logger.debug("[GraphRunner] domain registry 不可用，按空域图节点集处理", exc_info=True)
        return set()


def _new_main_graph_thread_id() -> str:
    """返回每次主图执行独立且不含会话标识的 Checkpointer 键。"""
    return f"agent-{uuid.uuid4().hex}"


def _looks_like_refusal_text(message: str) -> bool:
    """回答是否为拒答/短路类文案（追问漏斗 resolved 的近似口径）。"""
    from backend.orchestration.graph.clarify_content import CLARIFY_STANDALONE_TEXT

    text = (message or "").lstrip()
    if not text:
        return True
    return (
        text.startswith("## 抱歉")
        or text == CLARIFY_STANDALONE_TEXT
        or text.startswith("未能找到")
        or "暂时无法直接处理" in text[:60]
        or "无法处理" in text[:60]
    )


def _funnel_note_resolved(clarify_click: dict | None, message: str,
                          session_id: str = "", trace_id: str = "") -> None:
    """点击追问卡的本轮以非拒答回答收尾 → resolved 双写（软失败）。"""
    if not clarify_click or _looks_like_refusal_text(message):
        return
    try:
        from backend.observability.clarify_funnel import record_funnel_event

        record_funnel_event(
            "resolved", source=clarify_click.get("source", ""),
            session_id=session_id, trace_id=trace_id)
    except Exception:  # noqa: BLE001 — 漏斗旁路
        logger.debug("[Runner] resolved 记录失败", exc_info=True)


class PreflightResult:
    """前置流水线评估与装配结果。"""

    def __init__(
        self,
        *,
        short_circuit: bool = False,
        events: list[dict] | None = None,
        initial_state: dict | None = None,
        request_ctx: Any | None = None,
        context_event_scope: Any | None = None,
        trace: Any | None = None,
        product_session_id: str = "",
        clarify_click: dict | None = None,
        start_time: float = 0.0,
        entry_started: float = 0.0,
        merged_q: Any | None = None,
        ctx: dict | None = None,
        streamed: list[bool] | None = None,
    ) -> None:
        self.short_circuit = short_circuit
        self.events = events or []
        self.initial_state = initial_state or {}
        self.request_ctx = request_ctx
        self.context_event_scope = context_event_scope
        self.trace = trace
        self.product_session_id = product_session_id
        self.clarify_click = clarify_click
        self.start_time = start_time
        self.entry_started = entry_started
        self.merged_q = merged_q
        self.ctx = ctx or {}
        self.streamed = streamed or [False]


def run_preflight(
    *,
    memory: Any,
    graph: Any,
    question: str,
    session_id: str = "default",
    kb_id: str = "default",
    stop_event: Any = None,
    user_id: str = "default",
    department: str = "",
    permissions: tuple[str, ...] | None = None,
    fallback_deltas: bool = True,
    model: str = "",
    domain_hint: str = "",
    tenant_id: str = "",
    idempotency_key: str = "",
    roles: tuple[str, ...] = (),
    product_id: str = "internal",
    product_source: str = "internal_call",
    persist_memory: bool = True,
    cs_entry_order_no: str = "",
) -> PreflightResult:
    """执行请求前置门禁、追问解析、记忆加载与上下文装配。"""
    import queue
    from backend.config.llm import ENABLE_TOKEN_STREAMING
    from backend.core.product_context import namespace_session_id, product_by_id
    from backend.orchestration.request_context import RequestContext, put_context
    from backend.core.tool_runtime.deadline import RequestDeadline
    from backend.observability.tracer import SpanKind, trace_collector
    from backend.orchestration.graph.engine.trace_builder import (
        _end_root,
        add_guard_span,
        finish_customer_service_guard_trace,
        finish_guard_trace,
    )
    from backend.orchestration.graph.events import emit_delta_events, make_initial_state
    from backend.security.authorization import widest_data_scope

    product_context = product_by_id(product_id)
    product_session_id = namespace_session_id(product_context, session_id)
    memory_owner_id = user_id

    start_time = time.time()
    entry_started = time.perf_counter()
    entry_stages: dict[str, float] = {}
    stage_started = entry_started

    # 1. 追问漏斗：选项点击检测
    clarify_click = None
    try:
        from backend.orchestration.graph.clarify_content import consume_clarify_click

        clarify_click = consume_clarify_click(product_session_id, question or "")
        if clarify_click is not None:
            from backend.observability.clarify_funnel import record_funnel_event

            record_funnel_event(
                "clicked", source=clarify_click.get("source", ""),
                session_id=product_session_id,
            )
            logger.info(
                "[Runner] 追问卡选项点击: source=%s q=%.40s",
                clarify_click.get("source", ""), question or "",
            )
    except Exception:
        clarify_click = None
    entry_stages["clarify_cache_ms"] = round((time.perf_counter() - stage_started) * 1000, 1)
    stage_started = time.perf_counter()

    # 2. Prompt Runtime 热刷新检查
    try:
        from backend.prompts.hot_reload import ensure_prompt_snapshot_fresh

        ensure_prompt_snapshot_fresh()
    except Exception:
        logger.debug("[Runner] Prompt Runtime freshness check skipped", exc_info=True)
    entry_stages["prompt_refresh_ms"] = round((time.perf_counter() - stage_started) * 1000, 1)
    stage_started = time.perf_counter()

    # 3. Input Guard 平台级门禁
    guard_result = _get_input_guard().guard(question or "", session_id=product_session_id)
    entry_stages["input_guard_ms"] = round((time.perf_counter() - stage_started) * 1000, 1)

    if guard_result.action in (GuardAction.BLOCK, GuardAction.CLARIFY):
        if _should_exempt_guard_clarify(
            guard_result, question or "",
            domain_hint=domain_hint,
            user_id=user_id,
            session_id=session_id,
        ):
            logger.info(
                "[Runner] 转人工/确认/人工接管期豁免 input_guard clarify，放行进图: "
                f"{(question or '')[:40]}"
            )
            guard_result = guard_result.model_copy(update={
                "action": GuardAction.ALLOW,
                "confidence": 1.0,
                "reason": f"customer_service_relay_bypass: {guard_result.reason}",
            })
        else:
            finish_guard_trace(product_session_id, guard_result)
            sc_events = [{"event": "status", "data": {"node": "input_guard", "ts": time.time()}}]
            message = guard_result.message or "## 提示\n\n无法处理该问题。"
            if fallback_deltas:
                sc_events.extend(list(_emit_delta_events(message, stop_event)))
            sc_events.append({"event": _ANSWER_EVENT, "data": {"answer": message}})
            _funnel_note_resolved(clarify_click, message, session_id=product_session_id)
            sc_events.append(make_done_event(
                message, {}, start_time,
                reply_source="system_notice",
                answer_source="input_guard",
            ))
            return PreflightResult(short_circuit=True, events=sc_events)

    # 4. 客服业务门禁
    if (domain_hint or "").strip().lower() in {"customer_service", "cs"}:
        from backend.customer_service.security.input_guard import (
            GuardAction as CSGuardAction,
            get_cs_input_guard,
        )

        cs_guard_result = get_cs_input_guard().check(
            getattr(guard_result, "normalized_query", "") or question or "",
        )
        if cs_guard_result.action in (CSGuardAction.BLOCK, CSGuardAction.CLARIFY):
            finish_customer_service_guard_trace(product_session_id, cs_guard_result)
            sc_events = [{
                "event": "status",
                "data": {"node": "cs_input_guard", "ts": time.time()},
            }]
            message = cs_guard_result.message or "无法处理该客服请求。"
            if fallback_deltas:
                sc_events.extend(list(_emit_delta_events(message, stop_event)))
            sc_events.append({"event": _ANSWER_EVENT, "data": {"answer": message}})
            _funnel_note_resolved(clarify_click, message, session_id=product_session_id)
            sc_events.append(make_done_event(
                message, {}, start_time,
                reply_source="system_notice",
                answer_source="cs_input_guard",
            ))
            return PreflightResult(short_circuit=True, events=sc_events)

    # 5. 启动 Trace 与预算先行绑定
    trace = trace_collector.start(question, product_session_id, workflow_name="agent")
    trace.metadata["entry_timing"] = {
        **entry_stages,
        "before_trace_ms": round((time.perf_counter() - entry_started) * 1000, 1),
    }
    try:
        from backend.infra.llm.budget import bind_request_budget

        bind_request_budget(
            str(getattr(trace, "request_id", "") or getattr(trace, "id", "")),
            user_id=str(user_id or ""),
            tenant_id=str(tenant_id or ""),
        )
    except Exception:
        logger.debug("[Runner] 预算先行绑定失败，维持 worker 内绑定", exc_info=True)

    from backend.context_budget.metrics import ContextEventScope
    context_event_scope = ContextEventScope(
        product_id=str(product_id or ""),
        tenant_id=str(tenant_id or ""),
        user_id=str(user_id or ""),
        session_id=product_session_id,
        request_id=str(getattr(trace, "request_id", "") or getattr(trace, "id", "")),
    )
    if not context_event_scope.complete:
        context_event_scope = None
    trace.tags["product_id"] = product_id
    trace.tags["product_source"] = product_source

    try:
        from backend.prompts.hot_reload import prompt_runtime_metadata
        from backend.prompts.service import prompt_service

        _pv = prompt_service.current_versions()
        trace.tags["prompt_versions"] = ",".join(
            f"{k}={v}" for k, v in sorted(_pv.items())[:12]
        ) or "none"
        trace.tags["prompt_version"] = trace.tags["prompt_versions"]
        trace.metadata["prompt_versions"] = dict(_pv)
        runtime = prompt_runtime_metadata(_pv)
        trace.tags["prompt_runtime"] = runtime
        trace.tags["prompt_epoch"] = runtime["epoch"]
        trace.tags["snapshot_time"] = runtime["snapshot_time"]
        trace.tags["reload_source"] = runtime["reload_source"]
    except Exception:
        _pv = {}

    trace_collector.start_span("root", parent_id=None,
                               name="多 Agent 协作管线", type="workflow",
                               input={"question": question, "kb_id": kb_id})
    add_guard_span(guard_result)

    # 6. 会话/记忆加载
    load_span = trace_collector.start_span(
        "session_load", name="会话/记忆加载", type="workflow",
        kind=SpanKind.KB_ROUTING.value,
        input={"session_id": product_session_id, "product": product_id},
    )
    retrieve_span = trace_collector.start_span(
        "memory.retrieve", name="长期记忆检索", type="retrieval",
        input={"session_id": product_session_id, "product": product_id},
    )
    try:
        memory_scope = {"tenant_id": tenant_id}
        if domain_hint:
            memory_scope["domain"] = domain_hint
        if persist_memory:
            l1 = memory.start_session(
                product_session_id, question, user_id=memory_owner_id,
                event_scope=context_event_scope, product=product_id,
                **memory_scope,
            )
        else:
            from backend.memory.short_term import ShortTermBuffer
            l1 = ShortTermBuffer()
        trace_collector.end_span(
            retrieve_span, metrics=dict(getattr(l1, "retrieval_stats", {}) or {}),
        )
        initial_state = make_initial_state(
            question, session_id, kb_id, l1.messages,
            guard_result=guard_result.model_dump(mode="json"),
            user_id=user_id, department=department,
            domain_hint=domain_hint, tenant_id=tenant_id,
        )
        initial_state["product_id"] = product_id
        initial_state["product_source"] = product_source
        initial_state["product_session_id"] = product_session_id
        initial_state["prompt_versions"] = _pv
    except Exception as e:
        trace_collector.end_span(retrieve_span, status="error", metrics={"error": str(e)[:100]})
        trace_collector.end_span(load_span, status="error", metrics={"error": str(e)[:100]})
        logger.warning(f"[GraphRunner] 会话/记忆加载失败: {e}", exc_info=True)
        error_evt, _code = _runner_error_event(e)
        _end_root(trace, status="error", metrics={"error": "session_load_failed"})
        trace_collector.finish(trace, "", int((time.time() - start_time) * 1000), "", "")
        return PreflightResult(short_circuit=True, events=[error_evt])
    trace_collector.end_span(load_span, metrics={"history_messages": len(l1.messages)})

    # 7. FollowUpResolver
    if (domain_hint or "").strip().lower() not in ("customer_service", "cs"):
        effective_question = _resolve_followup_for_request(
            trace=trace,
            raw_question=question,
            tenant_id=tenant_id,
            user_id=memory_owner_id,
            session_id=product_session_id,
            l1_messages=l1.messages,
        )
        if effective_question is None:
            message = "你指的是哪个城市或地区？"
            logger.info("[Runner] follow-up 无上下文，澄清接管: session=%s", session_id)
            sc_events = [{"event": "status", "data": {"node": "follow_up_resolver", "ts": time.time()}}]
            if fallback_deltas:
                sc_events.extend(list(_emit_delta_events(message, stop_event)))
            sc_events.append({"event": _ANSWER_EVENT, "data": {"answer": message}})
            _funnel_note_resolved(clarify_click, message, session_id=product_session_id)
            sc_events.append(make_done_event(
                message, {}, start_time,
                reply_source="system_notice",
                answer_source="follow_up_resolver",
            ))
            _end_root(trace, status="success", metrics={"follow_up": "clarified"})
            trace_collector.finish(trace, message, int((time.time() - start_time) * 1000), "", "")
            return PreflightResult(short_circuit=True, events=sc_events)
        if effective_question != question:
            initial_state["question"] = effective_question
            initial_state["raw_query"] = question

    # 8. Routing Context 组装
    try:
        from backend.orchestration.context.routing_context import assemble_routing_context

        initial_state["routing_context"] = assemble_routing_context(
            tenant_id, user_id, product_session_id,
        )
    except Exception as e:
        logger.warning(f"[Runner] routing_context 组装失败（软降级）: {e}")
        initial_state["routing_context"] = {}

    # 9. 队列与早期上下文事件 flush
    merged_q: queue.Queue = queue.Queue()
    from backend.context_budget.metrics import drain_pending_events
    for _early in drain_pending_events(scope=context_event_scope):
        merged_q.put(("evt", {"event": "context", "data": _early}))

    request_ctx = RequestContext(
        session_id=product_session_id, user_id=user_id, kb_id=kb_id,
        tenant_id=tenant_id, domain_hint=domain_hint,
        idempotency_key=idempotency_key,
        department=department, permissions=permissions,
        roles=tuple(roles),
        data_scope=widest_data_scope(tuple(roles)) or "",
        trace=trace, model=model,
        deadline=RequestDeadline.started_now(),
        product_id=product_id, product_source=product_source,
    )
    ctx = {
        "final_answer": "",
        "answer_source": "",
        "all_step_results": {},
        "current_plan": dict(initial_state.get("plan", {})),
        "plan_changed": False,
        "route_mode": "plan",
        "cs_context_snapshot": {},
        "usage": None,
        "aborted": False,
        "worker_error": False,
        "worker_paused": False,
        "memory_persisted": False,
        "cs_entry_order_no": cs_entry_order_no,
    }
    streamed = [False]

    def _sink(text: str, kind: str = "answer") -> None:
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
    has_checkpointer = getattr(graph, "checkpointer", None) is not None
    if has_checkpointer:
        initial_state["request_context"] = request_ctx.checkpoint_safe()
    else:
        put_context(initial_state, request_ctx)

    return PreflightResult(
        short_circuit=False,
        initial_state=initial_state,
        request_ctx=request_ctx,
        context_event_scope=context_event_scope,
        trace=trace,
        product_session_id=product_session_id,
        clarify_click=clarify_click,
        start_time=start_time,
        entry_started=entry_started,
        merged_q=merged_q,
        ctx=ctx,
        streamed=streamed,
    )
