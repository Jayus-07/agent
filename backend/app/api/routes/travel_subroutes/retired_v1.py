"""已退役的旅游 V1 API 端点（保留在源码供算法迁移定位与向下兼容）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.app.api.identity import require_identity
from backend.shared.logger import logger
from .base import *
from .schemas import (
    TravelDecisionRequest,
    TravelPlanConfirmRequest,
    TravelPlanDiscardRequest,
    TravelPlanRequest,
    TravelPlanRestoreRequest,
    TravelPreferencesRequest,
    TravelResponseMetadata,
    TravelUiContext,
)
from .streaming import travel_plan_stream

_retired_v1_router = APIRouter(prefix="/travel", tags=["已退役旅游 V1"])

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
    identity = facade.require_identity(request)

    # audit P3/B4：租户 fail-closed——网关对 JWT 必注入 tenant_id，空租户
    # 仅旧令牌/异常路径可达；对齐 cs_admin._resolve_tenant_scope 口径，
    # 绝不静默回落 'default'（worker 内异常会被吞成计划错误，必须在此拦截）。
    if not getattr(identity, "tenant_id", ""):
        raise HTTPException(403, detail="缺少可信租户身份")

    req = await facade._load_travel_plan_request(request)

    conversation_id = req.conversation_id or req.session_id or f"travel-{uuid4().hex}"
    run_id = f"travel-{uuid4().hex}"
    turn_id = req.client_run_id or f"turn-{uuid4().hex}"
    from backend.travel.request_runtime import RunStopped

    def worker(control):
        from backend.observability.tracer import trace_collector
        trace_started_at = time.monotonic()
        trace = trace_collector.start(
            facade._masked_trace_question(req.message),
            session_id=conversation_id or req.session_id,
            workflow_name="agent",
        )
        facade._annotate_trace_source(trace, req.client_run_id, req.source)
        graph_input = facade._build_travel_graph_input(
            req, identity, conversation_id, turn_id)
        # audit P3/B4：路由层已 fail-closed 校验租户非空，此处不再回退 default
        tenant_id = getattr(identity, "tenant_id", "")
        final_state: dict = {}
        read_only_context: dict[str, Any] = {}
        tool_events: list[dict] = []
        out = facade._plan_error("抱歉，旅游规划服务暂时不可用，请稍后再试。")
        try:
            control.check()
            user_id = identity.user_id or ""
            if req.mode == "read_only":
                read_only_context = facade._load_read_only_plan_context(
                    conversation_id, user_id, tenant_id)
                facade._read_only_graph_input(graph_input, read_only_context)
                graph = get_travel_read_only_graph()
                invoke_conversation_id = f"{conversation_id}:read-only:{turn_id}"
            else:
                restored = facade._restore_from_chat_reference(
                    req, identity, conversation_id, turn_id)
                if restored is not None:
                    out = restored
                    final_state = {"conversation_id": conversation_id}
                    facade._attach_trace_plan_projection(
                        final_state, out, conversation_id, user_id,
                        tenant_id=tenant_id)
                    return out
                pending = facade._latest_pending_draft(
                    conversation_id, user_id, tenant_id)
                if pending:
                    out = facade._plan_error(
                        "当前有待确认草案；请先应用或放弃，再提交行程修改。")
                    out["error_type"] = "draft_pending"
                    return facade._attach_travel_response_metadata(
                        out,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        base_plan_version=req.base_plan_version,
                    )
                facade._seed_cross_turn_base(
                    graph_input, conversation_id, user_id, tenant_id)
                graph = get_travel_graph()
                invoke_conversation_id = conversation_id
            from backend.travel.core.events import travel_event_scope

            with travel_event_scope(
                    lambda event: facade._record_travel_tool_trace_event(
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
                out = facade._record_plan_version(
                    out, conversation_id, user_id, tenant_id)
            base_plan_version = req.base_plan_version
            if req.mode == "read_only" and base_plan_version is None:
                base_plan_version = (
                    read_only_context.get("draft_plan_version")
                    or read_only_context.get("active_plan_version")
                )
            out = facade._attach_travel_response_metadata(
                out,
                conversation_id=conversation_id,
                turn_id=turn_id,
                base_plan_version=base_plan_version,
            )
            facade._attach_trace_plan_projection(
                final_state, out, conversation_id, identity.user_id or "",
                tenant_id=tenant_id)
            return out
        except RunStopped as exc:
            out = facade._stopped_result(exc.reason)
            out = facade._attach_travel_response_metadata(
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
                out = facade._plan_error(str(exc))
                out["error_type"] = "plan_version_conflict"
            elif isinstance(exc, PlanVersionPersistenceError):
                out = facade._plan_error(
                    "行程已生成，但未能安全保存到版本账本；请稍后重试。")
                out["error_type"] = "plan_version_persistence_failed"
            else:
                out = facade._plan_error("抱歉，旅游规划服务暂时不可用，请稍后再试。")
            out = facade._attach_travel_response_metadata(
                out,
                conversation_id=conversation_id,
                turn_id=turn_id,
                base_plan_version=req.base_plan_version,
            )
            return out
        finally:
            facade._finish_travel_trace(
                trace, trace_started_at, out, final_state, run_id, tool_events)

    handle = facade._submit_travel_request(identity, conversation_id, worker)
    future = asyncio.wrap_future(handle.future)
    try:
        while True:
            handle.control.check()
            if hasattr(request, "is_disconnected") and await request.is_disconnected():
                handle.control.cancel()
                return facade._stopped_result("cancelled")
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=0.1)
            except asyncio.TimeoutError:
                continue
    except RunStopped as exc:
        return facade._stopped_result(exc.reason)
    except asyncio.CancelledError:
        handle.control.cancel()
        raise


def travel_get_preferences(request: Request):
    from backend.tools.travel import preferences as prefs_store

    identity = facade.require_identity(request)
    return prefs_store.get_preferences(identity.user_id or "")


async def travel_put_preferences(request: Request):
    identity = facade.require_identity(request)
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


def travel_plan_list(request: Request, limit: int = Query(30, ge=1, le=100)):
    from backend.travel.core.plan_service import plan_version_service

    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    plans = plan_version_service.list_conversations(
        identity.user_id or "", limit=limit,
        tenant_id=tenant_id)
    return {"plans": plans}


def delete_travel_plan(conversation_id: str, request: Request):
    """删除行程记录及其版本账本；租户/用户范围由身份上下文确定。"""
    from backend.travel.core.plan_service import (
        PlanVersionPersistenceError,
        plan_version_service,
    )

    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    try:
        deleted = plan_version_service.delete_conversation(
            conversation_id,
            identity.user_id or "",
            tenant_id=tenant_id,
        )
    except PlanVersionPersistenceError as exc:
        raise HTTPException(status_code=503, detail="行程未能删除，请稍后重试") from exc
    return {"conversation_id": conversation_id, "deleted": deleted}


def travel_plan_latest(conversation_id: str, request: Request):
    from backend.travel.core.plan_service import plan_version_service

    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    latest = plan_version_service.latest_version(
        conversation_id, identity.user_id or "",
        tenant_id=tenant_id)
    if not latest:
        # 不存在 / 越权 / 账本不可用一律 404，不泄露存在性
        raise HTTPException(status_code=404, detail="无可用行程版本")
    active_reader = getattr(plan_version_service, "active_version", None)
    active = (active_reader(
        conversation_id, identity.user_id or "",
        tenant_id=tenant_id)
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

    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    latest = plan_version_service.latest_version(
        conversation_id, identity.user_id or "",
        tenant_id=tenant_id)
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
            tenant_id,
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


def travel_plan_versions(conversation_id: str, request: Request):
    from backend.travel.core.plan_service import plan_version_service

    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    versions = plan_version_service.list_versions(
        conversation_id, identity.user_id or "",
        tenant_id=tenant_id)
    return {"conversation_id": conversation_id, "versions": versions}


async def travel_plan_confirm(request: Request):
    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
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
            tenant_id=tenant_id)
    except Exception as e:
        raise _version_http_error(e)


async def travel_plan_discard(request: Request):
    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    try:
        req = TravelPlanDiscardRequest(**(await request.json()))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"解析失败: {e}")
    from backend.travel.core.plan_service import plan_version_service

    try:
        return await asyncio.to_thread(
            plan_version_service.discard,
            req.conversation_id, identity.user_id or "", req.plan_version,
            tenant_id=tenant_id)
    except Exception as e:
        raise _version_http_error(e)


async def travel_plan_restore(request: Request):
    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
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
            tenant_id=tenant_id)
    except Exception as e:
        raise _version_http_error(e)


def travel_plan_diff(
    conversation_id: str,
    request: Request,
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
):
    from backend.travel.core.plan_service import plan_version_service

    identity = facade.require_identity(request)
    tenant_id = facade._travel_tenant(request, identity)
    try:
        return plan_version_service.diff(
            conversation_id, identity.user_id or "",
            from_version=from_version, to_version=to_version,
            tenant_id=tenant_id)
    except Exception as e:
        raise _version_http_error(e)


async def travel_record_decision(request: Request):
    """落一条决策留痕。**软失败语义**：留痕写失败不挡前端 UI 动作
    （返回 200 + recorded=false，前端照常继续本地更新）——决策留痕是
    审计旁路不是业务门禁。身份取 require_identity，未认证 401。
    """
    identity = facade.require_identity(request)
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


def travel_list_decisions(request: Request, conversation_id: str = Query(..., min_length=1),
                          limit: int = Query(100, ge=1, le=500)):
    """按 conversation_id 查决策链（强制 user_id scope：越权 = 空列表）。
    v1 只落库+查询端点，管理端页不做（2026-10-03 拍板）。
    """
    identity = facade.require_identity(request)
    from backend.travel.core.decision_store import list_decisions

    return {"decisions": list_decisions(
        identity.user_id or "", conversation_id, limit=limit)}


# 注册 V1 规划流
_retired_v1_router.post("/plan/stream", summary="已退役：V1 旅游规划流")(travel_plan_stream)

facade.register("_retired_v1_router", _retired_v1_router)
facade.register("travel_plan", travel_plan)
facade.register("travel_get_preferences", travel_get_preferences)
facade.register("travel_put_preferences", travel_put_preferences)
facade.register("travel_plan_list", travel_plan_list)
facade.register("delete_travel_plan", delete_travel_plan)
facade.register("travel_plan_latest", travel_plan_latest)
facade.register("_CANDIDATE_GROUP_ORDER", _CANDIDATE_GROUP_ORDER)
facade.register("_group_candidates", _group_candidates)
facade.register("travel_candidates", travel_candidates)
facade.register("_version_http_error", _version_http_error)
facade.register("travel_plan_versions", travel_plan_versions)
facade.register("travel_plan_confirm", travel_plan_confirm)
facade.register("travel_plan_discard", travel_plan_discard)
facade.register("travel_plan_restore", travel_plan_restore)
facade.register("travel_plan_diff", travel_plan_diff)
facade.register("travel_record_decision", travel_record_decision)
facade.register("travel_list_decisions", travel_list_decisions)
