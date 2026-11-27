"""Travel SSE 流式规划引擎（/v2/plan/stream 与已退役 /plan/stream）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterator
from uuid import uuid4
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.app.api.identity import require_identity
from backend.shared.logger import logger
from backend.travel.models.brief import TravelBrief

from .base import *
from .schemas import (
    TravelPlanRequest,
    TravelResponseMetadata,
    TravelUiContext,
)

def _travel_sse_frame(event: dict) -> str:
    """编码旅游域 SSE 帧；event 字段直接使用白名单事件名。"""
    event_name = str(event.get("event") or "message")
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n"


async def travel_plan_stream(request: Request):
    return await facade._travel_plan_stream_impl(request, persist_legacy_plan=True)


async def travel_v2_plan_stream(request: Request):
    """调用既有旅游 Agent 图，返回真实 SSE 事件与结构化结果。

    V2 行程由独立 Trip API 保存；此入口不读写已退役的 V1 版本账本。
    """
    return await facade._travel_plan_stream_impl(request, persist_legacy_plan=False)


async def _travel_plan_stream_impl(
    request: Request, *, persist_legacy_plan: bool,
):
    """旅游域真实事件流。

    图仍由既有旅游域执行；SSE 只把 ``travel/core/events.py`` 的真实 Tool
    事件投影给用户端，并在结束时发送同一份结构化结果。没有 mock Tool，
    也不把固定阶段动画当作执行事实。V2 调用用独立会话命名空间，避免恢复
    V1 checkpoint；只有退役入口才触碰 V1 计划账本。
    """
    identity = facade.require_identity(request)
    # audit P3：全域租户 fail-closed——worker 内异常会被吞成错误帧，必须在入口拦截
    tenant = facade._travel_tenant(request, identity)
    req = await facade._load_travel_plan_request(request)
    if not persist_legacy_plan and req.mode == "read_only":
        raise HTTPException(status_code=422, detail="旅游 V2 规划流不接受 V1 只读模式")

    conversation_id = req.conversation_id or req.session_id or f"travel-{uuid4().hex}"
    execution_conversation_id = (
        conversation_id if persist_legacy_plan else f"travel-v2:{conversation_id}"
    )
    run_id = f"travel-{uuid4().hex}"
    turn_id = req.client_run_id or f"turn-{uuid4().hex}"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=512)
    cancelled = threading.Event()
    tool_events: list[dict] = []
    sequence = 0
    sequence_lock = threading.Lock()
    # 为终止标记保留一个队列槽；业务事件最多占 511 个槽，满载时仍能
    # 投递错误终帧/结束标记，客户端不会只看到半截事件流。
    pending_events = threading.BoundedSemaphore(511)
    run_control = None
    handle = None

    def stop(reason: str) -> None:
        cancelled.set()
        if run_control is not None:
            run_control.cancel(reason)
        if handle is not None:
            handle.control.cancel(reason)

    def queue_event(event: dict) -> None:
        nonlocal sequence
        facade._record_travel_tool_trace_event(tool_events, event)
        if cancelled.is_set():
            return
        if not pending_events.acquire(blocking=False):
            stop("backpressure")
            return
        with sequence_lock:
            sequence += 1
            payload = {**event, "run_id": run_id, "seq": sequence}

            def put() -> None:
                if cancelled.is_set():
                    pending_events.release()
                    return
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    # 队列满意味着客户端消费跟不上；worker 不能丢弃事件后
                    # 假装正常完成，直接让流以错误终止。
                    pending_events.release()
                    stop("backpressure")

            try:
                # 在同一把锁内排入事件循环，保证 seq 顺序与队列顺序一致。
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                pending_events.release()
                stop("cancelled")

    def queue_terminal(event: dict | None) -> None:
        def put() -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stop("backpressure")

        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            stop("cancelled")

    def worker(control) -> None:
        nonlocal run_control
        run_control = control
        from backend.orchestration.graph.travel_graph_node import _build_invoke_config
        from backend.travel.core.events import emit_travel_event, travel_event_scope
        from backend.travel.graph_builder import (
            get_travel_graph,
            get_travel_read_only_graph,
        )
        from backend.travel.models.graph_result import build_travel_graph_result
        from backend.travel.request_runtime import RunStopped

        graph_input = facade._build_travel_graph_input(
            req, identity, execution_conversation_id, turn_id)
        graph_input["session_id"] = execution_conversation_id
        tenant_id = tenant
        started_at = time.monotonic()
        from backend.observability.tracer import trace_collector
        trace = trace_collector.start(
            facade._masked_trace_question(req.message),
            session_id=execution_conversation_id or req.session_id,
            workflow_name="agent",
        )
        facade._annotate_trace_source(trace, req.client_run_id, req.source)
        trace_result: dict = facade._plan_error("旅游规划执行失败，请稍后再试。")
        trace_state: dict = {}
        read_only_context: dict[str, Any] = {}
        resume_mode = "normal"
        try:
            with travel_event_scope(queue_event), facade._travel_context_budget_event_scope(
                    tenant_id=tenant_id,
                    user_id=identity.user_id or "",
                    session_id=execution_conversation_id,
                    request_id=run_id,
                    queue_event=queue_event,
            ):
                control.check()
                emit_travel_event(
                    "run.started", agent="supervisor", run_id=run_id,
                    conversation_id=conversation_id, turn_id=turn_id,
                )
                user_id = identity.user_id or ""
                if req.mode == "read_only":
                    read_only_context = facade._load_read_only_plan_context(
                        conversation_id, user_id, tenant_id)
                    facade._read_only_graph_input(graph_input, read_only_context)
                    graph = get_travel_read_only_graph()
                    invoke_conversation_id = f"{conversation_id}:read-only:{turn_id}"
                else:
                    if persist_legacy_plan:
                        restored = facade._restore_from_chat_reference(
                            req, identity, conversation_id, turn_id)
                        if restored is not None:
                            trace_state = {"conversation_id": conversation_id}
                            facade._attach_trace_plan_projection(
                                trace_state, restored, conversation_id,
                                identity.user_id or "", tenant_id=tenant_id)
                            trace_result = restored
                            emit_travel_event(
                                "run.finished", agent="supervisor",
                                status=restored.get("status", "failed"),
                                turn_id=turn_id,
                                duration_ms=round(
                                    (time.monotonic() - started_at) * 1000),
                            )
                            queue_event({
                                "event": "done",
                                "source": "travel",
                                "status": restored.get("status", "failed"),
                                "result": restored,
                            })
                            return
                        pending = facade._latest_pending_draft(
                            conversation_id, user_id, tenant_id)
                        if pending:
                            message = "当前有待确认草案；请先应用或放弃，再提交行程修改。"
                            trace_result = facade._attach_travel_response_metadata(
                                {**facade._plan_error(message), "error_type": "draft_pending"},
                                conversation_id=conversation_id,
                                turn_id=turn_id,
                                base_plan_version=req.base_plan_version,
                            )
                            emit_travel_event(
                                "run.finished", agent="supervisor", status="failed",
                                turn_id=turn_id, error_type="draft_pending",
                                duration_ms=round((time.monotonic() - started_at) * 1000),
                            )
                            queue_event({
                                "event": "error", "source": "travel",
                                "status": "failed", "message": message,
                                "error_type": "draft_pending",
                            })
                            return
                        facade._seed_cross_turn_base(
                            graph_input, conversation_id, user_id, tenant_id)
                        graph = get_travel_graph()
                        invoke_conversation_id = conversation_id
                    else:
                        graph = get_travel_graph()
                        invoke_conversation_id = execution_conversation_id
                invoke_config = _build_invoke_config(
                    invoke_conversation_id, tenant_id, user_id)
                final_state = None
                early_result = None
                if req.mode != "read_only":
                    pending_decision, pending_snapshot = facade._pending_travel_decision(
                        graph, invoke_config)
                    if pending_decision:
                        thread_id = invoke_config["configurable"]["thread_id"]
                        pending_decision = facade._bind_travel_decision_token(
                            pending_decision, pending_snapshot, thread_id,
                        )
                        decision = facade._parse_travel_decision(req, pending_decision)
                        if decision is not None:
                            with facade._travel_resume_lock(thread_id):
                                # 锁内再次读取；前一请求若已恢复，重复提交只
                                # 返回“无待确认”，绝不把决策当成新规划输入。
                                pending_decision, pending_snapshot = facade._pending_travel_decision(
                                    graph, invoke_config)
                                if pending_decision:
                                    pending_decision = facade._bind_travel_decision_token(
                                        pending_decision, pending_snapshot, thread_id,
                                    )
                                    decision = facade._parse_travel_decision(
                                        req, pending_decision)
                                    if decision is not None:
                                        from langgraph.types import Command

                                        final_state = graph.invoke(
                                            Command(resume=decision),
                                            config=invoke_config,
                                        )
                                        resume_mode = "interrupt_resume"
                                    else:
                                        early_result = facade._travel_decision_prompt(
                                            pending_decision, conversation_id,
                                            unrecognized_input=True,
                                            stale_decision=True,
                                        )
                                else:
                                    early_result = facade._stale_travel_decision(
                                        conversation_id)
                        else:
                            request_token = (
                                req.action_payload.get("decision_token")
                                if isinstance(req.action_payload, dict) else None
                            )
                            early_result = facade._travel_decision_prompt(
                                pending_decision, conversation_id,
                                unrecognized_input=True,
                                stale_decision=(
                                    bool(request_token)
                                    and request_token != pending_decision["decision_token"]
                                ),
                            )
                        if early_result is not None:
                            resume_mode = (
                                "interrupt_pending"
                                if early_result.get("status") == "needs_clarification"
                                else "stale_decision"
                            )
                    elif facade._has_explicit_travel_decision(req):
                        early_result = facade._stale_travel_decision(conversation_id)
                        resume_mode = "stale_decision"

                if early_result is not None:
                    trace_result = facade._attach_travel_response_metadata(
                        early_result,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        base_plan_version=req.base_plan_version,
                    )
                    trace_state = {"conversation_id": execution_conversation_id}
                    emit_travel_event(
                        "run.finished", agent="supervisor",
                        status=trace_result.get("status", "failed"),
                        turn_id=turn_id,
                        duration_ms=round((time.monotonic() - started_at) * 1000),
                    )
                    queue_event({
                        "event": "done",
                        "source": "travel",
                        "status": trace_result.get("status", "failed"),
                        "result": trace_result,
                    })
                    return

                if final_state is None:
                    final_state = graph.invoke(graph_input, config=invoke_config)
                control.check()
                pending_decision = facade._decision_payload_from_interrupts(
                    final_state.get("__interrupt__")
                    if isinstance(final_state, dict) else None
                )
                trace_state = final_state if isinstance(final_state, dict) else {}
                if pending_decision:
                    checkpoint_payload, pending_snapshot = facade._pending_travel_decision(
                        graph, invoke_config,
                    )
                    if not checkpoint_payload or pending_snapshot is None:
                        raise _TravelCheckpointUnavailableError(
                            "行程暂停版本暂不可用；本轮没有继续执行。"
                        )
                    pending_decision = facade._bind_travel_decision_token(
                        checkpoint_payload,
                        pending_snapshot,
                        invoke_config["configurable"]["thread_id"],
                    )
                    trace_state = {
                        key: value for key, value in trace_state.items()
                        if key != "__interrupt__"
                    }
                    result = facade._travel_decision_prompt(
                        pending_decision, conversation_id)
                    resume_mode = "interrupt_pending"
                else:
                    result = dict(build_travel_graph_result(final_state))
                if req.mode == "read_only":
                    result.update({
                        "result_kind": "answer",
                        "active_plan_version": read_only_context.get(
                            "active_plan_version"),
                        "draft_plan_version": read_only_context.get(
                            "draft_plan_version"),
                    })
                elif persist_legacy_plan and not pending_decision:
                    result = facade._record_plan_version(
                        result, conversation_id, user_id, tenant_id)
                base_plan_version = req.base_plan_version
                if req.mode == "read_only" and base_plan_version is None:
                    base_plan_version = (
                        read_only_context.get("draft_plan_version")
                        or read_only_context.get("active_plan_version")
                    )
                result = facade._attach_travel_response_metadata(
                    result,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    base_plan_version=base_plan_version,
                )
                if persist_legacy_plan and not pending_decision:
                    facade._attach_trace_plan_projection(
                        trace_state, result, conversation_id, identity.user_id or "",
                        tenant_id=tenant_id)
                trace_result = result
                emit_travel_event(
                    "run.finished", agent="supervisor",
                    status=result.get("status", "failed"),
                    turn_id=turn_id,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                )
                queue_event({
                    "event": "done",
                    "source": "travel",
                    "status": result.get("status", "failed"),
                    "result": result,
                })
        except RunStopped as exc:
            trace_result = facade._attach_travel_response_metadata(
                facade._stopped_result(exc.reason),
                conversation_id=conversation_id,
                turn_id=turn_id,
                base_plan_version=req.base_plan_version,
            )
        except Exception as exc:  # noqa: BLE001 — 真实失败向前端终止
            logger.exception("[TravelAPI] 旅游域 SSE 执行异常")
            from backend.travel.core.plan_service import (
                PlanVersionConflict,
                PlanVersionPersistenceError,
            )
            if isinstance(exc, PlanVersionConflict):
                error_type = "plan_version_conflict"
                message = str(exc)
            elif isinstance(exc, PlanVersionPersistenceError):
                error_type = "plan_version_persistence_failed"
                message = "行程已生成，但未能安全保存到版本账本；请稍后重试。"
            elif isinstance(exc, _TravelResumeInProgressError):
                error_type = "resume_in_progress"
                message = "该行程的确认正在处理中，请勿重复提交。"
            elif isinstance(exc, _TravelResumeLockUnavailableError):
                error_type = "resume_lock_unavailable"
                message = "行程确认状态暂不可用，本轮没有继续执行。"
            elif isinstance(exc, _TravelCheckpointUnavailableError):
                error_type = "checkpoint_unavailable"
                message = "行程暂停状态暂不可用，本轮没有继续执行。"
            else:
                error_type = type(exc).__name__
                message = "旅游规划执行失败，请根据已显示的 Tool 失败信息重试。"
            with travel_event_scope(queue_event):
                emit_travel_event(
                    "run.finished", agent="supervisor", status="failed",
                    turn_id=turn_id,
                    error_type=error_type,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                )
                queue_event({
                    "event": "error",
                    "source": "travel",
                    "status": "failed",
                    "message": message,
                    "error_type": error_type,
                })
        finally:
            facade._finish_travel_trace(
                trace, started_at, trace_result, trace_state, run_id,
                tool_events, resume_mode,
            )
            queue_terminal(None)

    handle = facade._submit_travel_request(identity, execution_conversation_id, worker)

    async def event_stream():
        from backend.travel.request_runtime import RunStopped
        last_heartbeat = time.monotonic()
        try:
            while True:
                try:
                    handle.control.check()
                except RunStopped as exc:
                    result = facade._stopped_result(exc.reason)
                    yield facade._travel_sse_frame({
                        "event": "error", "source": "travel", "run_id": run_id,
                        "status": "failed", "error_type": exc.reason,
                        "message": result["final_answer"],
                    })
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if handle.future.done() and queue.empty():
                        break
                    if time.monotonic() - last_heartbeat < 15:
                        continue
                    last_heartbeat = time.monotonic()
                    yield facade._travel_sse_frame({
                        "event": "ping", "source": "travel",
                        "run_id": run_id, "seq": 0, "ts": time.time(),
                    })
                    continue
                if event is None:
                    break
                pending_events.release()
                yield facade._travel_sse_frame(event)
                await asyncio.sleep(0)
        except (asyncio.CancelledError, GeneratorExit):
            stop("cancelled")
            raise
        finally:
            if not handle.future.done():
                stop("cancelled")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Travel-Run-Id": run_id,
        },
    )


facade.register("_travel_sse_frame", _travel_sse_frame)
facade.register("travel_plan_stream", travel_plan_stream)
facade.register("travel_v2_plan_stream", travel_v2_plan_stream)
facade.register("_travel_plan_stream_impl", _travel_plan_stream_impl)
