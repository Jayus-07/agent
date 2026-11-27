"""Travel 规划引擎：图输入构建、状态恢复、上下文预算、Trace 追踪与版本落库。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Request

from backend.app.api.identity import require_identity
from backend.shared.logger import logger
from backend.travel.models.brief import TravelBrief

from .base import *
from .schemas import (
    TravelPlanRequest,
    TravelResponseMetadata,
    TravelUiContext,
)

def _attach_travel_response_metadata(
    result: dict,
    *,
    conversation_id: str,
    turn_id: str,
    base_plan_version: int | None,
) -> dict:
    """在不覆盖既有状态字段的前提下补齐统一轮次响应契约。"""
    itinerary = result.get("itinerary")
    clarification = result.get("clarification")
    task_results = result.get("task_results")
    kind = result.get("result_kind")
    if kind not in {"answer", "plan", "draft", "clarification", "task_result"}:
        if clarification or result.get("status") == "needs_clarification":
            kind = "clarification"
        elif task_results and not itinerary:
            kind = "task_result"
        elif itinerary:
            kind = "plan" if result.get("plan_status") == "confirmed" else "draft"
        else:
            kind = "answer"

    draft_version = result.get("draft_plan_version")
    if (draft_version is None and kind == "draft"
            and result.get("plan_status") == "waiting_confirmation"):
        draft_version = (itinerary or {}).get("plan_version")

    metadata = TravelResponseMetadata(
        result_kind=kind,
        conversation_id=conversation_id,
        turn_id=turn_id,
        active_plan_version=result.get("active_plan_version"),
        draft_plan_version=draft_version,
        base_plan_version=(base_plan_version if base_plan_version is not None
                           else result.get("base_plan_version")),
        task_results=task_results if isinstance(task_results, list) else [],
    )
    return {**result, **metadata.model_dump()}


def _build_travel_graph_input(
    req: TravelPlanRequest,
    identity: Any,
    conversation_id: str,
    turn_id: str,
) -> dict[str, Any]:
    """构造本轮输入并保留结构化请求，不让 LangGraph 丢弃 schema 外字段。"""
    from backend.travel.graph_state import new_travel_graph_input

    brief_input = (
        req.brief_input.model_dump(mode="json", exclude_unset=True)
        if req.brief_input is not None else None
    )
    graph_input = new_travel_graph_input(
        user_message=req.message,
        user_id=identity.user_id or "",
        session_id=req.session_id,
        conversation_id=conversation_id,
    )
    graph_input.update({
        "request_mode": req.mode,
        "brief_input": brief_input,
        "base_plan_version": req.base_plan_version,
        "ui_context": req.ui_context.model_dump(exclude_none=True),
        "action_payload": req.action_payload,
        "turn_id": turn_id,
    })
    return graph_input


def _seed_cross_turn_base(graph_input: dict, conversation_id: str,
                          user_id: str, tenant_id: str = "default") -> None:
    """跨轮基底自愈（#53/#140 地基）：checkpointer 丢失（强杀/重启/降级
    MemorySaver）后，改单轮拿不到上一轮的 brief 基底与版本 parent ——
    slot_filler 变化检测恒 False（旧行程被当草案输出）、transit 版本号
    回落 v1 与旧版同号。plan_store 账本是跨进程权威，有账即预置：

      - reconstruct_brief：slot_filler 的兜底基底（state.brief 优先，
        checkpoint 活着时值与账本一致，覆盖无害且自愈）；
      - plan_parent_version：transit 盖版本章的 parent（vN+1 传导）。

    无账（首次规划）不预置，维持「input 只放本轮输入」契约。
    """
    if not conversation_id or not user_id:
        return
    try:
        from backend.travel.core.plan_service import plan_version_service

        latest = plan_version_service.latest_version(
            conversation_id, user_id, tenant_id=tenant_id, strict=True)
        active = plan_version_service.active_version(
            conversation_id, user_id, tenant_id=tenant_id, strict=True)
        if active and active.get("itinerary"):
            active_itinerary = active["itinerary"]
            graph_input["reconstruct_brief"] = (
                active_itinerary.get("brief") or {})
            # 指纹基线同样可派生：对账本 brief 重算——没有它，
            # detect_brief_change 的 `bool(last_fingerprint)` 恒 False，
            # 改单轮永远判「需求没变」（实测：改单轮旧行程被当草案输出）
            from backend.travel.graph_state import brief_fingerprint
            from backend.travel.models.brief import TravelBrief

            graph_input["brief_fingerprint"] = brief_fingerprint(
                TravelBrief(**(active_itinerary.get("brief") or {})))
        if latest:
            graph_input["plan_parent_version"] = latest.get("plan_version")
    except Exception:  # noqa: BLE001 — 自愈是增强项，失败不挡规划主链
        logger.debug("[TravelAPI] 跨轮基底预置失败（按无基底执行）",
                     exc_info=True)


def _load_read_only_plan_context(
    conversation_id: str,
    user_id: str,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """按当前身份读取 Active/Draft；只读问答不信任前端传来的行程快照。"""
    if not conversation_id or not user_id:
        return {
            "active_plan_version": None,
            "draft_plan_version": None,
            "reference_itinerary": None,
            "reference_status": "missing_identity",
        }
    try:
        from backend.travel.core.plan_service import plan_version_service

        latest = plan_version_service.latest_version(
            conversation_id, user_id, tenant_id=tenant_id, strict=True)
        active = plan_version_service.active_version(
            conversation_id, user_id, tenant_id=tenant_id, strict=True)
        draft = latest if latest and latest.get("plan_status") == "waiting_confirmation" else None
        reference = draft or active
        return {
            "active_plan_version": (active or {}).get("plan_version"),
            "draft_plan_version": (draft or {}).get("plan_version"),
            "reference_status": (draft or active or {}).get("plan_status", "missing"),
            "reference_itinerary": (reference or {}).get("itinerary"),
        }
    except Exception:  # noqa: BLE001 — 缺上下文时仍保持只读，不读 checkpoint 草案
        logger.warning("[TravelAPI] 只读问答读取版本上下文失败", exc_info=True)
        return {
            "active_plan_version": None,
            "draft_plan_version": None,
            "reference_itinerary": None,
            "reference_status": "unavailable",
        }


def _latest_pending_draft(
    conversation_id: str,
    user_id: str,
    tenant_id: str = "default",
) -> dict[str, Any] | None:
    """读账本检查是否已有待决草案；写请求需在执行图前拒绝。"""
    if not conversation_id or not user_id:
        return None
    try:
        from backend.travel.core.plan_service import plan_version_service

        latest = plan_version_service.latest_version(
            conversation_id, user_id, tenant_id=tenant_id, strict=True)
        if latest and latest.get("plan_status") == "waiting_confirmation":
            return latest
    except Exception:  # noqa: BLE001 — 无法确认无草案时禁止写入 checkpoint
        logger.error("[TravelAPI] 待确认草案预检失败，拒绝规划写请求", exc_info=True)
        raise
    return None


def _restore_from_chat_reference(
    req: TravelPlanRequest,
    identity: Any,
    conversation_id: str,
    turn_id: str,
) -> dict | None:
    """将明确的自然语言回退指令接到现有版本恢复/CAS 链路。

    只处理可写聊天模式；read_only 请求继续由只读图拦截，绝不借此绕过
    P1-02 的只读门。无法唯一定位时直接澄清，不落入普通规划图。
    """
    if req.mode == "read_only":
        return None
    from backend.travel.core.restore_reference import (
        parse_restore_reference,
        resolve_restore_target,
    )

    reference = parse_restore_reference(req.message)
    if reference is None:
        return None

    from backend.travel.core.plan_service import plan_version_service

    user_id = identity.user_id or ""
    tenant_id = getattr(identity, "tenant_id", "")
    if not tenant_id:
        # 调用方（/plan 与 v2 流 worker）路由层已 fail-closed；此处兜底防复活
        raise ValueError("缺少可信租户身份")
    latest = plan_version_service.latest_version(
        conversation_id, user_id, tenant_id=tenant_id, strict=True)
    if not latest:
        return facade._attach_travel_response_metadata(
            {
                "status": "needs_clarification",
                "final_answer": "当前会话还没有可恢复的行程，请先生成一份行程。",
                "clarification": "当前会话没有可恢复的历史版本。",
                "itinerary": None,
            },
            conversation_id=conversation_id,
            turn_id=turn_id,
            base_plan_version=req.base_plan_version,
        )

    versions = plan_version_service.list_versions(
        conversation_id, user_id, tenant_id=tenant_id)
    if not versions:
        versions = [latest]
    active = plan_version_service.active_version(
        conversation_id, user_id, tenant_id=tenant_id, strict=True)
    target_version = resolve_restore_target(reference, versions)
    latest_version = int(latest["plan_version"])
    if target_version is None:
        candidates = [
            int(row["plan_version"])
            for row in versions
            if int(row.get("plan_version") or 0) < latest_version
        ]
        if reference.kind == "explicit" and reference.target_version == latest_version:
            message = f"当前已经是 v{latest_version}，请指定更早的版本号。"
        elif candidates:
            choices = "、".join(f"v{version}" for version in candidates[:8])
            message = (
                f"我暂时无法唯一确定要恢复的版本。当前是 v{latest_version}，"
                f"可恢复历史版本：{choices}。请说“恢复到第 N 版”。"
            )
        else:
            message = "当前会话没有更早的可恢复版本，请指定已有的历史版本号。"
        return facade._attach_travel_response_metadata(
            {
                "status": "needs_clarification",
                "final_answer": message,
                "clarification": message,
                "itinerary": None,
                "active_plan_version": (active or {}).get("plan_version"),
                "draft_plan_version": (
                    latest_version
                    if latest.get("plan_status") == "waiting_confirmation"
                    else None
                ),
            },
            conversation_id=conversation_id,
            turn_id=turn_id,
            base_plan_version=(
                req.base_plan_version
                if req.base_plan_version is not None else latest_version
            ),
        )

    base_version = (
        req.base_plan_version
        if req.base_plan_version is not None else latest_version
    )
    restored = plan_version_service.restore(
        conversation_id,
        user_id,
        target_version=target_version,
        base_version=base_version,
        tenant_id=tenant_id,
    )
    itinerary = restored["itinerary"]
    draft_version = int(itinerary["plan_version"])
    result = {
        "status": "success",
        "result_kind": "draft",
        "final_answer": (
            f"已将 v{target_version} 的内容恢复为待确认草案 v{draft_version}；"
            "正式行程尚未改变，请确认后应用。"
        ),
        "itinerary": itinerary,
        "plan_status": "waiting_confirmation",
        "change_record": restored.get("change_record"),
        "active_plan_version": (active or {}).get("plan_version"),
        "draft_plan_version": draft_version,
        "base_plan_version": base_version,
    }
    return facade._attach_travel_response_metadata(
        result,
        conversation_id=conversation_id,
        turn_id=turn_id,
        base_plan_version=base_version,
    )


def _read_only_graph_input(
    graph_input: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """注入账本权威的问答上下文，图本身不挂持久化状态。"""
    graph_input["read_only_context"] = context
    itinerary = context.get("reference_itinerary")
    if isinstance(itinerary, dict):
        graph_input["itinerary"] = itinerary
        graph_input["brief"] = itinerary.get("brief") or {}
    return graph_input


def _record_plan_version(
    out: dict, conversation_id: str, user_id: str,
    tenant_id: str = "default",
) -> dict:
    """把成功行程投影进版本账本；与非流式入口共用。"""
    itinerary = out.get("itinerary")
    if itinerary and out.get("status") == "success":
        from backend.travel.core.plan_service import plan_version_service

        out.update(plan_version_service.record_plan_result(
            conversation_id, user_id, itinerary, tenant_id=tenant_id))
    return out


def _attach_trace_plan_projection(state: dict, result: dict,
                                  conversation_id: str,
                                  user_id: str,
                                  *, tenant_id: str = "default") -> None:
    """把版本账本的 active/draft 事实放进观测旁路，不改变 API 响应。"""
    if not conversation_id or not user_id or not isinstance(state, dict):
        return
    try:
        from backend.travel.core.plan_service import plan_version_service

        itinerary = result.get("itinerary") or state.get("itinerary") or {}
        active = plan_version_service.active_version(
            conversation_id, user_id, tenant_id=tenant_id)
        change = result.get("change_record") or {}
        state["_trace_plan_versions"] = {
            "base_plan_version": change.get("parent_plan_version") or "",
            "active_plan_version": (active or {}).get("plan_version") or "",
            "draft_plan_version": (
                result.get("draft_plan_version")
                or itinerary.get("plan_version") or ""),
        }
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        logger.debug("[TravelAPI] Trace 版本投影失败", exc_info=True)


def _record_travel_tool_trace_event(records: list[dict], event: dict) -> None:
    """把 Tool 事件压成白名单 Trace 字段，不保存原始参数、预览或错误文本。"""
    event_name = str(event.get("event") or "")
    if event_name not in {"tool.started", "tool.result", "tool.degraded", "tool.blocked"}:
        return
    call_id = str(event.get("tool_call_id") or "")
    record = next(
        (item for item in records
         if call_id and item.get("tool_call_id") == call_id),
        None,
    )
    if record is None:
        record = {
            "tool_call_id": call_id or f"legacy-{len(records) + 1}",
            "task_id": str(event.get("task_id") or ""),
            "tool": str(event.get("tool") or ""),
            "agent": str(event.get("agent") or ""),
            "status": "running" if event_name == "tool.started" else "",
            "data_status": "",
            "result_count": 0,
            "duration_ms": 0,
            "error_type": "",
        }
        records.append(record)
    for key in ("task_id", "tool", "agent"):
        if event.get(key):
            record[key] = str(event[key])[:128]
    if event_name in {"tool.result", "tool.degraded", "tool.blocked"}:
        for key in ("status", "data_status", "result_count", "duration_ms", "error_type"):
            if event.get(key) is not None:
                value = event[key]
                record[key] = str(value)[:128] if key in {"status", "data_status", "error_type"} else value


@contextmanager
def _travel_context_budget_event_scope(
    *, tenant_id: str, user_id: str, session_id: str, request_id: str,
    queue_event,
):
    """把共享预算事件安全投影到当前旅游 SSE 轮次。"""
    from backend.context_budget.metrics import (
        ContextEventScope,
        reset_context_event_scope,
        reset_context_sink,
        set_context_event_scope,
        set_context_sink,
    )

    scope = ContextEventScope(
        product_id="travel",
        tenant_id=str(tenant_id or ""),
        user_id=str(user_id or ""),
        session_id=str(session_id or ""),
        request_id=str(request_id or ""),
    )

    def sink(event: dict) -> None:
        if event.get("type") != "context":
            return
        safe_fields = (
            "level", "action", "before_tokens", "after_tokens",
            "saved_tokens", "ts", "reversible", "fold_id",
            "folded_messages", "replaced_messages", "summary_tokens",
            "delta_messages", "reason",
        )
        data = {key: event[key] for key in safe_fields if key in event}
        queue_event({
            "event": "context",
            "source": "context_budget",
            **data,
        })

    scope_token = set_context_event_scope(scope)
    sink_token = set_context_sink(sink, scope=scope)
    try:
        yield
    finally:
        reset_context_sink(sink_token)
        reset_context_event_scope(scope_token)


def _finish_travel_trace(trace, started_at: float, result: dict,
                         state: dict | None, run_id: str,
                         tool_events: list[dict] | None = None,
                         resume_mode: str = "normal") -> None:
    """收口旅游入口 Trace；观测失败不能改变已生成的业务结果。"""
    try:
        trace.tags.update({
            # runtime_domain 是 trace_source 三分类的唯一依据（单一事实源）。
            # 旅游入口绕过主图 Router，没有 prefilter_chain 的
            # record_router_decision 写入 runtime_* 归因，因此必须在这里
            # 显式声明，否则 classify_trace_source 只能退到
            # workflow_name=="agent" 分支，把旅游误判成 AI 助手。
            "runtime_domain": "travel",
            "travel_status": result.get("status", "failed"),
            "travel_run_id": run_id,
            "travel_destination": facade._travel_destination(state, result),
            "travel_resume_mode": resume_mode,
            "travel_result_kind": result.get("result_kind", ""),
        })
        for key in (
            "active_plan_version", "draft_plan_version", "base_plan_version",
        ):
            value = result.get(key)
            if value is not None:
                trace.tags[f"travel_{key}"] = str(value)
        itinerary = result.get("itinerary")
        graph_version = (
            itinerary.get("plan_version") if isinstance(itinerary, dict) else None
        )
        if graph_version is not None:
            trace.tags["travel_graph_plan_version"] = str(graph_version)
        from backend.travel.trace_semantics import build_trace_semantics

        semantics_state = dict(state or {})
        if not semantics_state.get("conversation_id"):
            # 极简测试图/异常出口可能没有把输入键回传到最终 state；
            # Trace 创建时的 session_id 仍是同一 conversation_id。
            semantics_state["conversation_id"] = getattr(
                trace, "session_id", "") or ""
        semantics = build_trace_semantics(semantics_state, result)
        trace.metadata["travel_semantics"] = semantics
        trace.metadata["travel_tool_calls"] = list(tool_events or [])
        trace.tags["travel_tool_call_count"] = str(len(tool_events or []))
        for key, value in semantics.items():
            if isinstance(value, list):
                trace.tags[f"travel_{key}"] = ",".join(str(item) for item in value)
            else:
                trace.tags[f"travel_{key}"] = str(value)
        from backend.observability.tracer import trace_collector
        trace_collector.finish(
            trace,
            result.get("final_answer") or "",
            round((time.monotonic() - started_at) * 1000),
            model="travel",
            provider="travel_graph",
        )
        # 线上问题台账（2026-10-08 #13）：question 在入口已过 PII 掩码；
        # 旁路软失败且独立兜底，不能影响上面的 trace 收口语义
        try:
            from backend.observability.question_ledger import (
                record_question_from_trace,
            )

            record_question_from_trace(
                trace,
                domain="travel",
                answer_summary=result.get("final_answer") or "",
                source="travel",
            )
        except Exception:  # noqa: BLE001 — 台账断流不影响主流程
            logger.debug("[TravelAPI] 问题台账写入失败", exc_info=True)
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        logger.debug("[TravelAPI] Trace 收口失败", exc_info=True)


def _stopped_result(reason: str) -> dict:
    messages = {
        "timeout": "规划超时，请缩小行程范围后重试。",
        "cancelled": "本次规划已取消。",
        "backpressure": "事件流消费过慢，本次规划已停止，请重试。",
        "admission_unavailable": "规划执行资源暂时不可用，请稍后重试。",
    }
    return {**facade._plan_error(messages.get(reason, "本次规划已停止。")), "error_type": reason}


def _submit_travel_request(identity, conversation_id, worker):
    from backend.travel.request_runtime import RequestRejected, get_request_executor
    tenant = getattr(identity, "tenant_id", "")
    if not tenant:
        raise HTTPException(status_code=403, detail="缺少可信租户身份")
    try:
        return get_request_executor().submit(
            tenant, identity.user_id or "",
            conversation_id, worker,
        )
    except RequestRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_type": exc.reason, "message": "规划繁忙或当前会话仍在执行，请稍后重试。"},
            headers={"Retry-After": "2"},
        ) from exc


facade.register("_attach_travel_response_metadata", _attach_travel_response_metadata)
facade.register("_build_travel_graph_input", _build_travel_graph_input)
facade.register("_seed_cross_turn_base", _seed_cross_turn_base)
facade.register("_load_read_only_plan_context", _load_read_only_plan_context)
facade.register("_latest_pending_draft", _latest_pending_draft)
facade.register("_restore_from_chat_reference", _restore_from_chat_reference)
facade.register("_read_only_graph_input", _read_only_graph_input)
facade.register("_record_plan_version", _record_plan_version)
facade.register("_attach_trace_plan_projection", _attach_trace_plan_projection)
facade.register("_record_travel_tool_trace_event", _record_travel_tool_trace_event)
facade.register("_travel_context_budget_event_scope", _travel_context_budget_event_scope)
facade.register("_finish_travel_trace", _finish_travel_trace)
facade.register("_stopped_result", _stopped_result)
facade.register("_submit_travel_request", _submit_travel_request)
