"""app/api/routes/travel.py — 旅游域 REST API（2026-09-22 P0-4 + P1）

与 chat SSE 并行的**非流式**旅游域入口。为什么需要它：
  1. 前端行程页需要结构化行程（itinerary JSON）做时间轴卡片、地图打点、
     ICS 导出 —— 走 chat SSE 只能拿到 markdown 文本，结构化信息全丢；
  2. ICS 导出是纯转换（行程 JSON → 日历文本），天然适合独立端点；
  3. 反馈/偏好/推荐是轻量 REST 语义，不适合塞进对话流。

高并发考量：
  - /plan 与 /plan/stream 共用有界执行器，不在事件循环执行同步图；域图复用
    get_travel_graph() 单例（进程内共享，图编译只发生一次）；
  - checkpointer thread_id 与 chat 链路同一来源（conversation_id）——
    同一会话无论走 SSE 还是 REST，跨轮状态一致；
  - 天气/RAG/偏好等外部调用在域图内部已有软降级，本层不重复兜底；
  - ICS/推荐为纯函数转换，无状态、无 IO，可任意水平扩展。

鉴权口径（2026-09-30 收口）：本模块**全部端点要求已认证身份**
（``require_identity`` → 未认证 401）。此前用宽容 ``resolve_identity``：
未登录请求按 ``guest`` 放行、``user_id=""`` 落库——偏好读写与反馈会
写进空账号、规划可被匿名调用。

为什么是纵深防御而非唯一闸门：网关 APISIX 的 ``/api/*`` 兜底路由已挂
``gateway-auth``（``GATEWAY_AUTH_MODE=enforce``），未认证请求在网关层
即 401；本层收口拦的是**绕过网关直连后端**的通道（内部脚本 / 未来的
服务间调用）。两者互补：网关管外部入口，本层管进程边界。

前端不受影响：``AuthGate`` 未登录时 ``return null``（不渲染受保护子树），
``/travel`` 页根本不会 mount，因此不会发出匿名请求。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import date, timedelta
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.api.identity import require_identity
from backend.shared.logger import logger
from backend.travel.models.brief import TravelBrief

router = APIRouter(prefix="/travel", tags=["旅游域"])


# ============================================================
# POST /travel/plan — 非流式规划（返回 markdown 行程单 + 结构化行程）
# ============================================================
class TravelUiContext(BaseModel):
    selected_day: int | None = Field(None, ge=1)
    selected_poi_id: str | None = Field(None, max_length=128)


class TravelPlanRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000,
                         description="本轮用户输入（如「帮我排杭州2天行程，亲子，带娃不累」）")
    session_id: str = Field("", max_length=128)
    conversation_id: str = Field("", max_length=128,
                                 description="跨轮会话标识；同一值可跨轮改单（依赖 checkpointer）")
    mode: Literal["plan", "chat", "action", "read_only"] = "plan"
    brief_input: TravelBrief | None = None
    base_plan_version: int | None = Field(None, ge=1)
    ui_context: TravelUiContext = Field(default_factory=TravelUiContext)
    action_payload: dict[str, Any] = Field(default_factory=dict)
    # M4/G2-G3：前端轮次标识与来源归因，随消息体透传进 trace.tags
    #（可选字段向后兼容旧前端；source 白名单 card_action/canvas_action/
    # tier_switch/budget_negotiate/manual，原样记录不做枚举校验——
    # 前端口径演进不破后端）
    client_run_id: str = Field("", max_length=64,
                               description="前端生成的轮次标识（client-<ts>-<rand>），trace.tags 关联用")
    source: str = Field("", max_length=32,
                        description="消息来源归因：card_action/canvas_action/tier_switch/budget_negotiate/manual")


class TravelResponseMetadata(BaseModel):
    result_kind: Literal[
        "answer", "plan", "draft", "clarification", "task_result",
    ]
    conversation_id: str
    turn_id: str
    active_plan_version: int | None = None
    draft_plan_version: int | None = None
    base_plan_version: int | None = None
    task_results: list[dict[str, Any]] = Field(default_factory=list)


class TravelConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=20_000)


class TravelConversationMessagesRequest(BaseModel):
    messages: list[TravelConversationMessage] = Field(..., max_length=100)


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


def _masked_trace_question(message: str) -> str:
    """trace 落库前掩码（验收 #124）：trace_summary.question 是明文账
    （输入手机号即可回读），复用客服 C11 的规则掩码（shared/pii_mask）。
    只脱观测账，不改执行输入——规划链路拿到的仍是原文。掩码失败原样
    落库（观测旁路不阻塞主链）。"""
    try:
        from backend.shared.pii_mask import mask_pii

        return mask_pii(message or "")[0]
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        return message or ""


def _annotate_trace_source(trace, client_run_id: str, source: str) -> None:
    """把前端轮次与来源写进 trace.tags（仅非空时写，避免空 tag 噪音）。

    在 trace 创建后立即调用（而非收口时）——即便执行中途崩溃，
    trace 里也已带上 client_run_id/source，审计口径「当时哪轮、从哪来」
    对失败轮次同样成立。
    """
    try:
        if client_run_id:
            trace.tags["client_run_id"] = str(client_run_id)[:64]
        if source:
            trace.tags["travel_source"] = str(source)[:32]
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        logger.debug("[TravelAPI] trace source 标注失败", exc_info=True)


def _plan_error(message: str, status: str = "failed") -> dict:
    return {"status": status, "final_answer": message, "itinerary": None}


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
    tenant_id = getattr(identity, "tenant_id", "") or "default"
    latest = plan_version_service.latest_version(
        conversation_id, user_id, tenant_id=tenant_id, strict=True)
    if not latest:
        return _attach_travel_response_metadata(
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
        return _attach_travel_response_metadata(
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
    return _attach_travel_response_metadata(
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


def _travel_destination(state: dict | None, result: dict | None = None) -> str:
    """从旅游状态提取可审计的目的地标签，不依赖用户自报字段。"""
    state = state or {}
    brief = state.get("brief") or {}
    if isinstance(brief, dict) and brief.get("destination"):
        return str(brief["destination"])
    itinerary = (result or {}).get("itinerary") or {}
    if isinstance(itinerary, dict):
        return str(itinerary.get("destination") or "")
    return str(getattr(itinerary, "destination", "") or "")


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


def _finish_travel_trace(trace, started_at: float, result: dict,
                         state: dict | None, run_id: str,
                         tool_events: list[dict] | None = None) -> None:
    """收口旅游入口 Trace；观测失败不能改变已生成的业务结果。"""
    try:
        trace.tags.update({
            "travel_status": result.get("status", "failed"),
            "travel_run_id": run_id,
            "travel_destination": _travel_destination(state, result),
        })
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


def _parse_travel_plan_request(request_body: dict) -> TravelPlanRequest:
    try:
        return TravelPlanRequest(**request_body)
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"TravelPlanRequest 解析失败: {exc}") from exc


async def _load_travel_plan_request(request: Request) -> TravelPlanRequest:
    """把 JSON 解析错误也收口为 422，避免流式入口无响应地断流。"""
    try:
        request_body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"TravelPlanRequest JSON 解析失败: {exc}") from exc
    return _parse_travel_plan_request(request_body)


@router.get("/city-guide", summary="城市指南（四级内容链：文档摘要→RAG→知乎→暂无）")
async def travel_city_guide(request: Request, destination: str, force: bool = False):
    """M3-g：城市指南轻端点，不进域图。

    结果按目的地缓存 TTL 7 天（拍板口径）；知乎关闭/配额尽自动降级。
    """
    require_identity(request)
    from backend.travel.services.city_guide_service import get_city_guide

    return get_city_guide(destination, force=force)


@router.post("/plan", summary="旅游规划（非流式，返回行程单与结构化行程）")
async def travel_plan(request: Request):
    """手动 request.json() 解析 body —— 对齐 chat_stream 先例。

    FastAPI/Pydantic 自动 body 解析在部分中文 payload 下会抛
    "There was an error parsing the body"（实测 2026-09-22：页面提交
    「杭州，3天，2个人…」稳定 422），chat.py 已用同款绕过并验证稳定。
    """
    from backend.orchestration.graph.travel_graph_node import (
        _build_invoke_config,  # 复用 thread_id/recursion_limit 组装（单一事实源）
    )
    from backend.travel.graph_builder import (
        get_travel_graph,
        get_travel_read_only_graph,
    )
    from backend.travel.models.graph_result import build_travel_graph_result

    # 鉴权先于 body 解析：未认证不消费请求体（fail-closed）
    identity = require_identity(request)

    req = await _load_travel_plan_request(request)

    conversation_id = req.conversation_id or req.session_id or f"travel-{uuid4().hex}"
    run_id = f"travel-{uuid4().hex}"
    turn_id = req.client_run_id or f"turn-{uuid4().hex}"
    from backend.travel.request_runtime import RunStopped

    def worker(control):
        from backend.observability.tracer import trace_collector
        trace_started_at = time.monotonic()
        trace = trace_collector.start(
            _masked_trace_question(req.message),
            session_id=conversation_id or req.session_id,
            workflow_name="agent",
        )
        _annotate_trace_source(trace, req.client_run_id, req.source)
        graph_input = _build_travel_graph_input(
            req, identity, conversation_id, turn_id)
        tenant_id = getattr(identity, "tenant_id", "") or "default"
        final_state: dict = {}
        read_only_context: dict[str, Any] = {}
        tool_events: list[dict] = []
        out = _plan_error("抱歉，旅游规划服务暂时不可用，请稍后再试。")
        try:
            control.check()
            user_id = identity.user_id or ""
            if req.mode == "read_only":
                read_only_context = _load_read_only_plan_context(
                    conversation_id, user_id, tenant_id)
                _read_only_graph_input(graph_input, read_only_context)
                graph = get_travel_read_only_graph()
                invoke_conversation_id = f"{conversation_id}:read-only:{turn_id}"
            else:
                restored = _restore_from_chat_reference(
                    req, identity, conversation_id, turn_id)
                if restored is not None:
                    out = restored
                    final_state = {"conversation_id": conversation_id}
                    _attach_trace_plan_projection(
                        final_state, out, conversation_id, user_id,
                        tenant_id=tenant_id)
                    return out
                pending = _latest_pending_draft(
                    conversation_id, user_id, tenant_id)
                if pending:
                    out = _plan_error(
                        "当前有待确认草案；请先应用或放弃，再提交行程修改。")
                    out["error_type"] = "draft_pending"
                    return _attach_travel_response_metadata(
                        out,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        base_plan_version=req.base_plan_version,
                    )
                _seed_cross_turn_base(
                    graph_input, conversation_id, user_id, tenant_id)
                graph = get_travel_graph()
                invoke_conversation_id = conversation_id
            from backend.travel.core.events import travel_event_scope

            with travel_event_scope(
                    lambda event: _record_travel_tool_trace_event(
                        tool_events, event)):
                final_state = graph.invoke(
                    graph_input, config=_build_invoke_config(
                        invoke_conversation_id, tenant_id, user_id))
            control.check()
            out = dict(build_travel_graph_result(final_state))
            if req.mode == "read_only":
                out.update({
                    "result_kind": "answer",
                    "active_plan_version": read_only_context.get(
                        "active_plan_version"),
                    "draft_plan_version": read_only_context.get(
                        "draft_plan_version"),
                })
            else:
                out = _record_plan_version(
                    out, conversation_id, user_id, tenant_id)
            base_plan_version = req.base_plan_version
            if req.mode == "read_only" and base_plan_version is None:
                base_plan_version = (
                    read_only_context.get("draft_plan_version")
                    or read_only_context.get("active_plan_version")
                )
            out = _attach_travel_response_metadata(
                out,
                conversation_id=conversation_id,
                turn_id=turn_id,
                base_plan_version=base_plan_version,
            )
            _attach_trace_plan_projection(
                final_state, out, conversation_id, identity.user_id or "",
                tenant_id=tenant_id)
            return out
        except RunStopped as exc:
            out = _stopped_result(exc.reason)
            out = _attach_travel_response_metadata(
                out,
                conversation_id=conversation_id,
                turn_id=turn_id,
                base_plan_version=req.base_plan_version,
            )
            return out
        except Exception as exc:
            logger.exception("[TravelAPI] 域图执行异常")
            from backend.travel.core.plan_service import (
                PlanVersionConflict,
                PlanVersionPersistenceError,
            )
            if isinstance(exc, PlanVersionConflict):
                out = _plan_error(str(exc))
                out["error_type"] = "plan_version_conflict"
            elif isinstance(exc, PlanVersionPersistenceError):
                out = _plan_error(
                    "行程已生成，但未能安全保存到版本账本；请稍后重试。")
                out["error_type"] = "plan_version_persistence_failed"
            else:
                out = _plan_error("抱歉，旅游规划服务暂时不可用，请稍后再试。")
            out = _attach_travel_response_metadata(
                out,
                conversation_id=conversation_id,
                turn_id=turn_id,
                base_plan_version=req.base_plan_version,
            )
            return out
        finally:
            _finish_travel_trace(
                trace, trace_started_at, out, final_state, run_id, tool_events)

    handle = _submit_travel_request(identity, conversation_id, worker)
    future = asyncio.wrap_future(handle.future)
    try:
        while True:
            handle.control.check()
            if hasattr(request, "is_disconnected") and await request.is_disconnected():
                handle.control.cancel()
                return _stopped_result("cancelled")
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=0.1)
            except asyncio.TimeoutError:
                continue
    except RunStopped as exc:
        return _stopped_result(exc.reason)
    except asyncio.CancelledError:
        handle.control.cancel()
        raise


def _stopped_result(reason: str) -> dict:
    messages = {
        "timeout": "规划超时，请缩小行程范围后重试。",
        "cancelled": "本次规划已取消。",
        "backpressure": "事件流消费过慢，本次规划已停止，请重试。",
        "admission_unavailable": "规划执行资源暂时不可用，请稍后重试。",
    }
    return {**_plan_error(messages.get(reason, "本次规划已停止。")), "error_type": reason}


def _submit_travel_request(identity, conversation_id, worker):
    from backend.travel.request_runtime import RequestRejected, get_request_executor
    try:
        return get_request_executor().submit(
            getattr(identity, "tenant_id", "") or "default", identity.user_id or "",
            conversation_id, worker,
        )
    except RequestRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_type": exc.reason, "message": "规划繁忙或当前会话仍在执行，请稍后重试。"},
            headers={"Retry-After": "2"},
        ) from exc


def _travel_sse_frame(event: dict) -> str:
    """编码旅游域 SSE 帧；event 字段直接使用白名单事件名。"""
    event_name = str(event.get("event") or "message")
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n"


@router.post("/plan/stream", summary="旅游规划（真实 Tool 事件流 + 结构化结果）")
async def travel_plan_stream(request: Request):
    """旅游域真实事件流。

    图仍由既有旅游域执行；SSE 只把 ``travel/core/events.py`` 的真实 Tool
    事件投影给用户端，并在结束时发送同一份结构化 PlanResponse。没有 mock
    Tool，也不把固定阶段动画当作执行事实。
    """
    identity = require_identity(request)
    req = await _load_travel_plan_request(request)

    conversation_id = req.conversation_id or req.session_id or f"travel-{uuid4().hex}"
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
        _record_travel_tool_trace_event(tool_events, event)
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

        graph_input = _build_travel_graph_input(
            req, identity, conversation_id, turn_id)
        tenant_id = getattr(identity, "tenant_id", "") or "default"
        started_at = time.monotonic()
        from backend.observability.tracer import trace_collector
        trace = trace_collector.start(
            _masked_trace_question(req.message),
            session_id=conversation_id or req.session_id,
            workflow_name="agent",
        )
        _annotate_trace_source(trace, req.client_run_id, req.source)
        trace_result: dict = _plan_error("旅游规划执行失败，请稍后再试。")
        trace_state: dict = {}
        read_only_context: dict[str, Any] = {}
        try:
            with travel_event_scope(queue_event):
                control.check()
                emit_travel_event(
                    "run.started", agent="supervisor", run_id=run_id,
                    conversation_id=conversation_id, turn_id=turn_id,
                )
                user_id = identity.user_id or ""
                if req.mode == "read_only":
                    read_only_context = _load_read_only_plan_context(
                        conversation_id, user_id, tenant_id)
                    _read_only_graph_input(graph_input, read_only_context)
                    graph = get_travel_read_only_graph()
                    invoke_conversation_id = f"{conversation_id}:read-only:{turn_id}"
                else:
                    restored = _restore_from_chat_reference(
                        req, identity, conversation_id, turn_id)
                    if restored is not None:
                        trace_state = {"conversation_id": conversation_id}
                        _attach_trace_plan_projection(
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
                    pending = _latest_pending_draft(
                        conversation_id, user_id, tenant_id)
                    if pending:
                        message = "当前有待确认草案；请先应用或放弃，再提交行程修改。"
                        trace_result = _attach_travel_response_metadata(
                            {**_plan_error(message), "error_type": "draft_pending"},
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
                    _seed_cross_turn_base(
                        graph_input, conversation_id, user_id, tenant_id)
                    graph = get_travel_graph()
                    invoke_conversation_id = conversation_id
                final_state = graph.invoke(
                    graph_input, config=_build_invoke_config(
                        invoke_conversation_id, tenant_id, user_id))
                control.check()
                result = dict(build_travel_graph_result(final_state))
                trace_state = final_state if isinstance(final_state, dict) else {}
                if req.mode == "read_only":
                    result.update({
                        "result_kind": "answer",
                        "active_plan_version": read_only_context.get(
                            "active_plan_version"),
                        "draft_plan_version": read_only_context.get(
                            "draft_plan_version"),
                    })
                else:
                    result = _record_plan_version(
                        result, conversation_id, user_id, tenant_id)
                base_plan_version = req.base_plan_version
                if req.mode == "read_only" and base_plan_version is None:
                    base_plan_version = (
                        read_only_context.get("draft_plan_version")
                        or read_only_context.get("active_plan_version")
                    )
                result = _attach_travel_response_metadata(
                    result,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    base_plan_version=base_plan_version,
                )
                _attach_trace_plan_projection(
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
            trace_result = _attach_travel_response_metadata(
                _stopped_result(exc.reason),
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
            _finish_travel_trace(
                trace, started_at, trace_result, trace_state, run_id,
                tool_events,
            )
            queue_terminal(None)

    handle = _submit_travel_request(identity, conversation_id, worker)

    async def event_stream():
        from backend.travel.request_runtime import RunStopped
        last_heartbeat = time.monotonic()
        try:
            while True:
                try:
                    handle.control.check()
                except RunStopped as exc:
                    result = _stopped_result(exc.reason)
                    yield _travel_sse_frame({
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
                    yield _travel_sse_frame({
                        "event": "ping", "source": "travel",
                        "run_id": run_id, "seq": 0, "ts": time.time(),
                    })
                    continue
                if event is None:
                    break
                pending_events.release()
                yield _travel_sse_frame(event)
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


# ============================================================
# POST /travel/export/ics — 行程 → 日历文件（纯转换，无状态）
# ============================================================
class IcsExportRequest(BaseModel):
    itinerary: dict = Field(..., description="完整 Itinerary JSON（/plan 返回的 itinerary 字段）")


_ICS_TIME_FMT = "%Y%m%dT%H%M%S"
# 行程时刻按东八区声明（验收 #115）：此前是 floating time（无 TZID），
# 国内日历碰巧正确，但跨时区导入会按日历软件本地时区漂移。VTIMEZONE 块
# 声明 Asia/Shanghai 固定 +08:00（无夏令时），DTSTART/DTEND 带 TZID 引用。
_ICS_TZID = "Asia/Shanghai"
_ICS_VTIMEZONE_LINES = (
    "BEGIN:VTIMEZONE",
    f"TZID:{_ICS_TZID}",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0800",
    "TZOFFSETTO:+0800",
    "TZNAME:CST",
    "END:STANDARD",
    "END:VTIMEZONE",
)


def _ics_escape(text: str) -> str:
    """RFC 5545 转义：逗号/分号/反斜杠/换行。"""
    return (text or "").replace("\\", "\\\\").replace(";", r"\;") \
        .replace(",", r"\,").replace("\n", r"\n")


def _dt_stamp(day_date: date, hhmm: str, fallback: str) -> str:
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
    except (ValueError, IndexError):
        h, m = 8, 30
    return (day_date.strftime("%Y%m%d") + f"T{h:02d}{m:02d}00")


def itinerary_to_ics(itinerary: dict) -> str:
    """Itinerary dict → ICS (RFC 5545) 文本（纯函数，可单测）。

    无 start_date 的行程以「今天」为第 1 天生成占位日程，并在描述里注明；
    ICS 是给日历软件消费的，缺日期时给占位比空文件更可用。
    """
    brief = itinerary.get("brief") or {}
    destination = brief.get("destination", "行程")
    start_raw = brief.get("start_date")
    # model_dump 后 start_date 是 date 对象；跨 JSON 传输后是 ISO 字符串 —— 两种都要吃
    if isinstance(start_raw, date):
        base_date = start_raw
    elif isinstance(start_raw, str) and start_raw:
        try:
            base_date = date.fromisoformat(start_raw)
        except ValueError:
            base_date = date.today()
    else:
        base_date = date.today()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//agent//travel-plan//CN",
        "CALSCALE:GREGORIAN",
        *_ICS_VTIMEZONE_LINES,
    ]
    for day in itinerary.get("days", []):
        day_idx_raw = day.get("day_index", 1)
        try:
            day_idx = int(day_idx_raw)
        except (TypeError, ValueError):
            day_idx = 1
        day_date_raw = day.get("day_date")
        if isinstance(day_date_raw, date):
            day_date = day_date_raw
        elif isinstance(day_date_raw, str) and day_date_raw:
            try:
                day_date = date.fromisoformat(day_date_raw)
            except ValueError:
                day_date = base_date + timedelta(days=day_idx - 1)
        else:
            day_date = base_date + timedelta(days=day_idx - 1)
        for item in day.get("items", []):
            title = item.get("title", "安排")
            start = _dt_stamp(day_date, item.get("start", "08:30"), "083000")
            end = _dt_stamp(day_date, item.get("end", "09:30"), "093000")
            note = item.get("note", "")
            desc_parts = [destination, f"第 {day.get('day_index', 1)} 天"]
            if note:
                desc_parts.append(note)
            lines += [
                "BEGIN:VEVENT",
                f"UID:travel-{day.get('day_index', 1)}-{_ics_escape(title)}-"
                f"{start}@agent.local",
                f"DTSTAMP:{start}",
                f"DTSTART;TZID={_ICS_TZID}:{start}",
                f"DTEND;TZID={_ICS_TZID}:{end}",
                f"SUMMARY:{_ics_escape(title)}",
                f"DESCRIPTION:{_ics_escape('；'.join(desc_parts))}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def ics_content_disposition(destination: str) -> str:
    """构造 RFC 6266 合规的 Content-Disposition。

    HTTP 头只能 latin-1 编码，中文目的地直接内嵌 filename 会 500（D1）。
    ASCII fallback 用固定名，真实文件名经 filename*=UTF-8'' 百分号编码携带。
    """
    encoded = quote(destination, safe="")
    return f"attachment; filename=\"travel-plan.ics\"; filename*=UTF-8''{encoded}.ics"


@router.post("/export/ics", summary="行程导出为 ICS 日历文件",
             responses={200: {"content": {"text/calendar": {}}}})
async def travel_export_ics(request: Request):
    require_identity(request)
    # 手动解析（对齐 chat_stream 先例，规避 FastAPI 中文 payload 自动解析 bug）
    try:
        req = IcsExportRequest(**(await request.json()))
    except Exception as e:
        return Response(
            json.dumps({"error": "invalid_itinerary", "message": str(e)},
                       ensure_ascii=False),
            status_code=422, media_type="application/json",
        )
    from backend.travel.models.itinerary import Itinerary

    try:
        Itinerary.model_validate(req.itinerary)
    except Exception as e:
        return Response(
            json.dumps({"error": "invalid_itinerary", "message": str(e)},
                       ensure_ascii=False),
            status_code=422, media_type="application/json",
        )
    ics_text = itinerary_to_ics(req.itinerary)
    filename = (req.itinerary.get("brief") or {}).get("destination", "trip")
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": ics_content_disposition(filename)},
    )


# ============================================================
# POST /travel/feedback — 行程单反馈（复用既有 feedback 表）
# ============================================================
class TravelFeedbackRequest(BaseModel):
    session_id: str = Field("", max_length=128)
    vote: str = Field(..., pattern="^(positive|negative)$")
    reason: str = Field("", max_length=500,
                        description="不满意的具体点（如「第二天太赶」「门票比实际贵」）")
    destination: str = Field("", max_length=64)
    plan_version: int = Field(0, ge=0)


@router.post("/feedback", summary="行程单反馈（收藏/不满意）")
async def travel_feedback(request: Request):
    identity = require_identity(request)
    try:
        req = TravelFeedbackRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.feedback import add_feedback

    feedback_id = add_feedback(
        session_id=req.session_id,
        vote=req.vote,
        question=f"travel:{req.destination}:v{req.plan_version}",
        reason=req.reason,
        user_id=identity.user_id or "",
    )
    return {"status": "ok", "feedback_id": feedback_id}


# ============================================================
# GET/PUT /travel/preferences — 用户偏好（P1-1）
# ============================================================
@router.get("/preferences", summary="读取用户旅游偏好")
def travel_get_preferences(request: Request):
    from backend.tools.travel import preferences as prefs_store

    identity = require_identity(request)
    return prefs_store.get_preferences(identity.user_id or "")


class TravelPreferencesRequest(BaseModel):
    origin: str = Field("", max_length=64)
    preferences: list[str] = Field(default_factory=list, max_length=16)
    pace: str = Field("", pattern="^(relaxed|moderate|intense|)$")
    diet: str = Field("", max_length=64)
    lodging: str = Field("", max_length=64)
    transport: str = Field("", max_length=64)


@router.put("/preferences", summary="写入/更新用户旅游偏好")
async def travel_put_preferences(request: Request):
    identity = require_identity(request)
    try:
        req = TravelPreferencesRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.tools.travel import preferences as prefs_store

    ok = prefs_store.upsert_preferences(
        identity.user_id or "",
        origin=req.origin,
        preferences=[p for p in req.preferences if p],
        pace=req.pace,
        diet=req.diet,
        lodging=req.lodging,
        transport=req.transport,
    )
    return {"status": "ok" if ok else "skipped"}


# ============================================================
# GET /travel/recommend — 目的地推荐（P1-3）
# ============================================================
@router.get("/recommend", summary="按偏好推荐目的地")
def travel_recommend(request: Request, preferences: str = "", top: int = 3):
    from backend.travel.recommend import recommend_cities

    require_identity(request)
    tags = [p.strip() for p in preferences.split(",") if p.strip()]
    recs = recommend_cities(tags, top=max(1, min(top, 5)))
    return {"recommendations": [r.to_dict() for r in recs]}


# ============================================================
# 行程版本生命周期（方案 v2 §5/§7：带版本的服务端状态）
#
# 四个动作：版本历史 / 确认整份行程 / 恢复历史版本 / 两版差异。
# 「确认整份行程」是 Plan 生命周期动作，与修改某一景点的「应用修改」、
# 交易中的「确认预订」是三个不同动作（方案 §2.3），文案不得混用。
# 越权（user_id 不匹配）与不存在一律 404 —— 不向调用方泄露存在性。
# ============================================================
# ============================================================
# 历史规划（跨会话）：按用户列规划 + 取某会话最新版（恢复用）。
# 与 versions/diff 的区别：那两个是**单个会话内**的版本链；这里把
# travel_plan_versions 当会话索引用 —— 每会话投影最新一行，供前端
# 「历史规划」入口列表与点击恢复（恢复=切回该 conversation 继续）。
# ============================================================
def _travel_message_session_id(
    conversation_id: str, user_id: str, tenant_id: str,
) -> str:
    """映射到无 schema 变更的 memory 会话键，并绑定用户与租户。"""
    conversation_id = (conversation_id or "").strip()
    if not conversation_id or len(conversation_id) > 128:
        raise HTTPException(status_code=422, detail="会话标识无效")
    scope = "\0".join((
        (tenant_id or "").strip() or "default", user_id, conversation_id,
    ))
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"travel:{digest}"


def _travel_message_owner_id(user_id: str, tenant_id: str) -> str:
    """给无 tenant_id 列的共享 memory 表提供租户隔离属主键。"""
    scope = f"{(tenant_id or '').strip() or 'default'}\0{user_id}"
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"travel:{digest[:57]}"  # ChatSession.user_id 列宽为 64


def _run_travel_memory(operation):
    """沿用 memory_manager 后台 loop，避免跨 loop 使用 asyncpg engine。"""
    from backend.memory.manager import memory_manager
    from backend.memory.service import MemoryService

    return memory_manager.run_tool(lambda: operation(MemoryService()))


@router.get("/conversations/{conversation_id}/messages",
            summary="恢复当前用户有权访问的旅游对话消息")
def travel_conversation_messages(conversation_id: str, request: Request):
    identity = require_identity(request)
    user_id = identity.user_id
    tenant_id = getattr(identity, "tenant_id", "") or "default"
    memory_owner_id = _travel_message_owner_id(user_id, tenant_id)
    memory_session_id = _travel_message_session_id(
        conversation_id, user_id, tenant_id,
    )
    result = _run_travel_memory(
        lambda service: service.get_session_messages(
            memory_session_id, user_id=memory_owner_id,
        )
    )
    if result.get("error") == "会话不存在":
        # 新会话和无权会话对外同语义，避免泄露内部存储键是否存在。
        return {"conversation_id": conversation_id, "messages": []}
    if result.get("error"):
        raise HTTPException(status_code=503, detail="旅游对话历史暂不可用")
    return {
        "conversation_id": conversation_id,
        "messages": result.get("messages", []),
    }


@router.put("/conversations/{conversation_id}/messages",
            summary="幂等替换当前用户的旅游对话消息快照")
def replace_travel_conversation_messages(
    conversation_id: str,
    payload: TravelConversationMessagesRequest,
    request: Request,
):
    identity = require_identity(request)
    user_id = identity.user_id
    tenant_id = getattr(identity, "tenant_id", "") or "default"
    memory_owner_id = _travel_message_owner_id(user_id, tenant_id)
    memory_session_id = _travel_message_session_id(
        conversation_id, user_id, tenant_id,
    )
    result = _run_travel_memory(
        lambda service: service.replace_session_messages(
            memory_session_id,
            [message.model_dump() for message in payload.messages],
            user_id=memory_owner_id,
        )
    )
    if result.get("error") == "会话不存在":
        raise HTTPException(status_code=404, detail="会话不存在")
    if result.get("error"):
        raise HTTPException(status_code=503, detail="旅游对话历史暂不可用")
    return {"conversation_id": conversation_id, "saved": result.get("saved", 0)}


@router.get("/plans", summary="当前用户的历史规划列表（每会话最新版）")
def travel_plan_list(request: Request, limit: int = Query(30, ge=1, le=100)):
    from backend.travel.core.plan_service import plan_version_service

    identity = require_identity(request)
    plans = plan_version_service.list_conversations(
        identity.user_id or "", limit=limit,
        tenant_id=getattr(identity, "tenant_id", "") or "default")
    return {"plans": plans}


@router.get("/plans/{conversation_id}/latest",
            summary="会话最新版行程（含完整 itinerary，供恢复历史规划）")
def travel_plan_latest(conversation_id: str, request: Request):
    from backend.travel.core.plan_service import plan_version_service

    identity = require_identity(request)
    latest = plan_version_service.latest_version(
        conversation_id, identity.user_id or "",
        tenant_id=getattr(identity, "tenant_id", "") or "default")
    if not latest:
        # 不存在 / 越权 / 账本不可用一律 404，不泄露存在性
        raise HTTPException(status_code=404, detail="无可用行程版本")
    active_reader = getattr(plan_version_service, "active_version", None)
    active = (active_reader(
        conversation_id, identity.user_id or "",
        tenant_id=getattr(identity, "tenant_id", "") or "default")
        if active_reader else None)
    return {
        "conversation_id": conversation_id,
        "plan_version": latest["plan_version"],
        "plan_status": latest["plan_status"],
        "destination": latest["destination"],
        "created_at": latest["created_at"],
        "itinerary": latest.get("itinerary"),
        "active_plan_version": active.get("plan_version") if active else None,
        "active_plan_status": active.get("plan_status") if active else None,
        "active_itinerary": active.get("itinerary") if active else None,
    }


# 候选池分组顺序（验收 #10）：前端 tab 按此顺序渲染，空组不显示。
# 酒店/交通当前在 candidates 池无生产者（酒店走 live_search 通道、
# 交通无候选数据）——分组保留键位，取不到即空组，前端如实隐藏。
_CANDIDATE_GROUP_ORDER = ("景点", "美食", "酒店")


def _group_candidates(raw: list[dict]) -> dict[str, list[dict]]:
    """把 graph state 的候选池按大类分组（验收 #10 分类候选表）。

    细分类（公园/购物/夜生活）并入「景点」大组；组内 rating 降序、
    每组截前 12 条（池上限 120，全量下发 payload 过大且列表页用不到）。
    """
    buckets: dict[str, list[dict]] = {g: [] for g in _CANDIDATE_GROUP_ORDER}
    for c in raw or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        category = str(c.get("category") or "")
        group = category if category in buckets else "景点"
        buckets[group].append({
            "poi_id": str(c.get("poi_id") or ""),
            "name": str(c.get("name") or ""),
            "category": category,
            "rating": float(c.get("rating") or 0.0),
            "reason": str(c.get("reason") or ""),
            "source": str(c.get("source") or ""),
        })
    for group in buckets:
        buckets[group].sort(key=lambda x: (-x["rating"], x["poi_id"]))
        buckets[group] = buckets[group][:12]
    return buckets


@router.get("/candidates",
            summary="会话候选池（分类候选表，验收 #10）")
def travel_candidates(conversation_id: str, request: Request):
    """左栏分类候选表数据源：最新行程版本关联的候选池，按类别分组。

    数据通路：候选池不在 plan_store 版本账本里，活在域图 checkpoint 的
    state.candidates —— 经域图单例 get_state 读（thread_id 与规划链路同源：
    travel:{tenant}:{user}:{conv} 复合 namespace）。checkpoint 不可达
    （降级 MemorySaver 后重启 / TTL 过期 / disabled）→ ``available=false``
    + 空分组 + 提示，**不伪造**候选。权限对齐 plans 端点：账本查无此人
    （不存在/越权）一律 404。
    """
    from backend.travel.core.plan_service import plan_version_service
    from backend.travel.graph_builder import get_travel_graph

    identity = require_identity(request)
    latest = plan_version_service.latest_version(
        conversation_id, identity.user_id or "",
        tenant_id=getattr(identity, "tenant_id", "") or "default")
    if not latest:
        raise HTTPException(status_code=404, detail="无可用行程版本")

    groups: dict[str, list[dict]] = {g: [] for g in _CANDIDATE_GROUP_ORDER}
    available = False
    try:
        # thread_id 与规划链路同源（单一事实源 _build_invoke_config）：
        # 实测 checkpoint 键是 travel:{tenant}:{user}:{conv} 复合 namespace
        # （STOP C 跨租户隔离），裸 conversation_id 永远读不到。
        from backend.orchestration.graph.travel_graph_node import _build_invoke_config

        snap = get_travel_graph().get_state(_build_invoke_config(
            conversation_id,
            getattr(identity, "tenant_id", "") or "default",
            identity.user_id or ""))
        raw = (getattr(snap, "values", None) or {}).get("candidates") or []
        if raw:
            available = True
            groups = _group_candidates(raw)
    except Exception:  # noqa: BLE001 — 候选表是增强展示，读不到不阻塞页面
        logger.debug("[TravelAPI] 候选池读取失败（checkpoint 不可达）",
                     exc_info=True)
    return {
        "conversation_id": conversation_id,
        "plan_version": latest["plan_version"],
        "destination": latest.get("destination"),
        "groups": groups,
        "available": available,
        "hint": "" if available else "候选池暂不可用（会话状态已过期或未持久化）",
    }


def _version_http_error(e: Exception) -> HTTPException:
    """service 层异常 → HTTP 语义（404 不泄露 / 409 带当前版本号 / 422 语义非法）。"""
    from backend.travel.core.plan_service import (
        PlanVersionConflict,
        PlanVersionInvalid,
        PlanVersionNotFound,
    )

    if isinstance(e, PlanVersionNotFound):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, PlanVersionConflict):
        return HTTPException(
            status_code=409,
            detail={"message": str(e),
                    "current_version": e.current_version},
        )
    if isinstance(e, PlanVersionInvalid):
        return HTTPException(status_code=422, detail=str(e))
    raise e


@router.get("/plans/{conversation_id}/versions", summary="行程版本历史（新→旧）")
def travel_plan_versions(conversation_id: str, request: Request):
    from backend.travel.core.plan_service import plan_version_service

    identity = require_identity(request)
    versions = plan_version_service.list_versions(
        conversation_id, identity.user_id or "",
        tenant_id=getattr(identity, "tenant_id", "") or "default")
    return {"conversation_id": conversation_id, "versions": versions}


class TravelPlanConfirmRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    plan_version: int = Field(..., ge=1,
                              description="要确认的版本号；必须是当前最新版")


@router.post("/plans/confirm", summary="确认整份行程（waiting_confirmation → confirmed）")
async def travel_plan_confirm(request: Request):
    identity = require_identity(request)
    try:
        req = TravelPlanConfirmRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.travel.core.plan_service import plan_version_service

    try:
        # service 内是同步 PG I/O + itinerary JSON 校验，to_thread 避免阻塞事件循环
        return await asyncio.to_thread(
            plan_version_service.confirm,
            req.conversation_id, identity.user_id or "", req.plan_version,
            tenant_id=getattr(identity, "tenant_id", "") or "default")
    except Exception as e:
        raise _version_http_error(e)


class TravelPlanDiscardRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    plan_version: int = Field(..., ge=1,
                              description="要放弃的当前待确认草案版本")


@router.post("/plans/discard", summary="放弃当前草案（waiting_confirmation → discarded）")
async def travel_plan_discard(request: Request):
    identity = require_identity(request)
    try:
        req = TravelPlanDiscardRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.travel.core.plan_service import plan_version_service

    try:
        return await asyncio.to_thread(
            plan_version_service.discard,
            req.conversation_id, identity.user_id or "", req.plan_version,
            tenant_id=getattr(identity, "tenant_id", "") or "default")
    except Exception as e:
        raise _version_http_error(e)


class TravelPlanRestoreRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    target_version: int = Field(..., ge=1,
                                description="要恢复到的历史版本号（内容来源）")
    base_version: int = Field(..., ge=1,
                              description="乐观并发基准：发起请求时看到的当前最新版本号")


@router.post("/plans/restore", summary="恢复历史版本（以旧版内容生成新版本）")
async def travel_plan_restore(request: Request):
    identity = require_identity(request)
    try:
        req = TravelPlanRestoreRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.travel.core.plan_service import plan_version_service

    try:
        return await asyncio.to_thread(
            plan_version_service.restore,
            req.conversation_id, identity.user_id or "",
            target_version=req.target_version, base_version=req.base_version,
            tenant_id=getattr(identity, "tenant_id", "") or "default")
    except Exception as e:
        raise _version_http_error(e)


@router.get("/plans/{conversation_id}/diff", summary="两版行程确定性差异")
def travel_plan_diff(
    conversation_id: str,
    request: Request,
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
):
    from backend.travel.core.plan_service import plan_version_service

    identity = require_identity(request)
    try:
        return plan_version_service.diff(
            conversation_id, identity.user_id or "",
            from_version=from_version, to_version=to_version,
            tenant_id=getattr(identity, "tenant_id", "") or "default")
    except Exception as e:
        raise _version_http_error(e)


# ============================================================
# POST/GET /travel/decisions — 用户决策留痕（M4/G1，2026-10-04）
# ============================================================
class TravelDecisionRequest(BaseModel):
    """前端用户决策上报（草案应用/放弃、画布确认替换、档位切换、删减协商）。

    user_id/tenant_id/created_at 由服务端从身份与数据库取，前端不传
    ——留痕的身份口径以服务端认证为准，不信任请求体自报。
    """
    decision: str = Field(..., min_length=1, max_length=32,
                          description="apply_draft | discard_draft | canvas_replace | tier_switch | budget_negotiate")
    conversation_id: str = Field(..., min_length=1, max_length=128)
    plan_version: int = Field(0, ge=0, le=100000)
    tier_from: str = Field("", max_length=32)
    tier_to: str = Field("", max_length=32)
    payload: dict = Field(default_factory=dict,
                          description="决策上下文（替换条目/协商话术/目标档位等自由 JSON）")
    source: str = Field("", max_length=32)
    client_run_id: str = Field("", max_length=64)


@router.post("/decisions", summary="用户决策留痕（草案应用/放弃/画布替换/档位切换/删减协商）")
async def travel_record_decision(request: Request):
    """落一条决策留痕。**软失败语义**：留痕写失败不挡前端 UI 动作
    （返回 200 + recorded=false，前端照常继续本地更新）——决策留痕是
    审计旁路不是业务门禁。身份取 require_identity，未认证 401。
    """
    identity = require_identity(request)
    try:
        req = TravelDecisionRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.travel.core.decision_store import record_decision

    record_id = await asyncio.to_thread(
        record_decision,
        identity.user_id or "", req.conversation_id, req.decision,
        tenant_id=getattr(identity, "tenant_id", "") or "",
        plan_version=req.plan_version,
        tier_from=req.tier_from, tier_to=req.tier_to,
        payload=req.payload, source=req.source,
        client_run_id=req.client_run_id,
    )
    return {"status": "recorded" if record_id else "skipped", "id": record_id}


@router.get("/decisions", summary="某会话的用户决策留痕链（时间新→旧）")
def travel_list_decisions(request: Request, conversation_id: str = Query(..., min_length=1),
                          limit: int = Query(100, ge=1, le=500)):
    """按 conversation_id 查决策链（强制 user_id scope：越权 = 空列表）。
    v1 只落库+查询端点，管理端页不做（2026-10-03 拍板）。
    """
    identity = require_identity(request)
    from backend.travel.core.decision_store import list_decisions

    return {"decisions": list_decisions(
        identity.user_id or "", conversation_id, limit=limit)}

