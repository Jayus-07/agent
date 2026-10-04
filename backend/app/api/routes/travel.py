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
import json
import threading
import time
from uuid import uuid4
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.api.identity import require_identity
from backend.shared.logger import logger

router = APIRouter(prefix="/travel", tags=["旅游域"])


# ============================================================
# POST /travel/plan — 非流式规划（返回 markdown 行程单 + 结构化行程）
# ============================================================
class TravelPlanRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000,
                         description="本轮用户输入（如「帮我排杭州2天行程，亲子，带娃不累」）")
    session_id: str = Field("", max_length=128)
    conversation_id: str = Field("", max_length=128,
                                 description="跨轮会话标识；同一值可跨轮改单（依赖 checkpointer）")
    # M4/G2-G3：前端轮次标识与来源归因，随消息体透传进 trace.tags
    #（可选字段向后兼容旧前端；source 白名单 card_action/canvas_action/
    # tier_switch/budget_negotiate/manual，原样记录不做枚举校验——
    # 前端口径演进不破后端）
    client_run_id: str = Field("", max_length=64,
                               description="前端生成的轮次标识（client-<ts>-<rand>），trace.tags 关联用")
    source: str = Field("", max_length=32,
                        description="消息来源归因：card_action/canvas_action/tier_switch/budget_negotiate/manual")


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


def _record_plan_version(out: dict, conversation_id: str, user_id: str) -> dict:
    """把成功行程投影进版本账本；与非流式入口共用。"""
    itinerary = out.get("itinerary")
    if itinerary and conversation_id and user_id:
        from backend.travel.core.plan_service import plan_version_service

        out.update(plan_version_service.record_plan_result(
            conversation_id, user_id, itinerary))
    return out


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


def _finish_travel_trace(trace, started_at: float, result: dict,
                         state: dict | None, run_id: str) -> None:
    """收口旅游入口 Trace；观测失败不能改变已生成的业务结果。"""
    try:
        trace.tags.update({
            "travel_status": result.get("status", "failed"),
            "travel_run_id": run_id,
            "travel_destination": _travel_destination(state, result),
        })
        from backend.observability.tracer import trace_collector
        trace_collector.finish(
            trace,
            result.get("final_answer") or "",
            round((time.monotonic() - started_at) * 1000),
            model="travel",
            provider="travel_graph",
        )
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
    from backend.travel.graph_builder import get_travel_graph
    from backend.travel.graph_state import new_travel_graph_input
    from backend.travel.models.graph_result import build_travel_graph_result

    # 鉴权先于 body 解析：未认证不消费请求体（fail-closed）
    identity = require_identity(request)

    req = await _load_travel_plan_request(request)

    conversation_id = req.conversation_id or req.session_id
    run_id = f"travel-{uuid4().hex}"
    from backend.travel.request_runtime import RunStopped

    def worker(control):
        from backend.observability.tracer import trace_collector
        trace_started_at = time.monotonic()
        trace = trace_collector.start(
            req.message, session_id=conversation_id or req.session_id,
            workflow_name="agent",
        )
        _annotate_trace_source(trace, req.client_run_id, req.source)
        graph_input = new_travel_graph_input(
            user_message=req.message, user_id=identity.user_id or "",
            session_id=req.session_id, conversation_id=conversation_id,
        )
        final_state: dict = {}
        out = _plan_error("抱歉，旅游规划服务暂时不可用，请稍后再试。")
        try:
            control.check()
            final_state = get_travel_graph().invoke(
                graph_input, config=_build_invoke_config(
                    conversation_id, getattr(identity, "tenant_id", ""),
                    identity.user_id or ""))
            control.check()
            out = dict(build_travel_graph_result(final_state))
            out = _record_plan_version(out, conversation_id, identity.user_id or "")
            return out
        except RunStopped as exc:
            out = _stopped_result(exc.reason)
            return out
        except Exception:
            logger.exception("[TravelAPI] 域图执行异常")
            return out
        finally:
            _finish_travel_trace(trace, trace_started_at, out, final_state, run_id)

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
    from backend.travel.request_runtime import get_request_executor, RequestRejected
    try:
        return get_request_executor().submit(
            getattr(identity, "tenant_id", ""), identity.user_id or "",
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

    conversation_id = req.conversation_id or req.session_id
    run_id = f"travel-{uuid4().hex}"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=512)
    cancelled = threading.Event()
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
        from backend.travel.request_runtime import RunStopped
        from backend.orchestration.graph.travel_graph_node import _build_invoke_config
        from backend.travel.core.events import emit_travel_event, travel_event_scope
        from backend.travel.graph_state import new_travel_graph_input
        from backend.travel.models.graph_result import build_travel_graph_result
        from backend.travel.graph_builder import get_travel_graph

        graph_input = new_travel_graph_input(
            user_message=req.message,
            user_id=identity.user_id or "",
            session_id=req.session_id,
            conversation_id=conversation_id,
        )
        started_at = time.monotonic()
        from backend.observability.tracer import trace_collector
        trace = trace_collector.start(
            req.message,
            session_id=conversation_id or req.session_id,
            workflow_name="agent",
        )
        _annotate_trace_source(trace, req.client_run_id, req.source)
        trace_result: dict = _plan_error("旅游规划执行失败，请稍后再试。")
        trace_state: dict = {}
        try:
            with travel_event_scope(queue_event):
                control.check()
                emit_travel_event(
                    "run.started", agent="supervisor", run_id=run_id,
                    conversation_id=conversation_id,
                )
                final_state = get_travel_graph().invoke(
                    graph_input, config=_build_invoke_config(
                        conversation_id, getattr(identity, "tenant_id", ""),
                        identity.user_id or ""))
                control.check()
                result = dict(build_travel_graph_result(final_state))
                trace_state = final_state if isinstance(final_state, dict) else {}
                result = _record_plan_version(
                    result, conversation_id, identity.user_id or "")
                trace_result = result
                emit_travel_event(
                    "run.finished", agent="supervisor",
                    status=result.get("status", "failed"),
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                )
                queue_event({
                    "event": "done",
                    "source": "travel",
                    "status": result.get("status", "failed"),
                    "result": result,
                })
        except RunStopped as exc:
            trace_result = _stopped_result(exc.reason)
        except Exception as exc:  # noqa: BLE001 — 真实失败向前端终止
            logger.exception("[TravelAPI] 旅游域 SSE 执行异常")
            with travel_event_scope(queue_event):
                emit_travel_event(
                    "run.finished", agent="supervisor", status="failed",
                    error_type=type(exc).__name__,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                )
                queue_event({
                    "event": "error",
                    "source": "travel",
                    "status": "failed",
                    "message": "旅游规划执行失败，请根据已显示的 Tool 失败信息重试。",
                    "error_type": type(exc).__name__,
                })
        finally:
            _finish_travel_trace(
                trace, started_at, trace_result, trace_state, run_id,
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
@router.get("/plans", summary="当前用户的历史规划列表（每会话最新版）")
def travel_plan_list(request: Request, limit: int = Query(30, ge=1, le=100)):
    from backend.travel.core.plan_service import plan_version_service

    identity = require_identity(request)
    plans = plan_version_service.list_conversations(
        identity.user_id or "", limit=limit)
    return {"plans": plans}


@router.get("/plans/{conversation_id}/latest",
            summary="会话最新版行程（含完整 itinerary，供恢复历史规划）")
def travel_plan_latest(conversation_id: str, request: Request):
    from backend.travel.core.plan_service import plan_version_service

    identity = require_identity(request)
    latest = plan_version_service.latest_version(
        conversation_id, identity.user_id or "")
    if not latest:
        # 不存在 / 越权 / 账本不可用一律 404，不泄露存在性
        raise HTTPException(status_code=404, detail="无可用行程版本")
    return {
        "conversation_id": conversation_id,
        "plan_version": latest["plan_version"],
        "plan_status": latest["plan_status"],
        "destination": latest["destination"],
        "created_at": latest["created_at"],
        "itinerary": latest.get("itinerary"),
    }


def _version_http_error(e: Exception) -> HTTPException:
    """service 层异常 → HTTP 语义（404 不泄露 / 409 带当前版本号 / 422 语义非法）。"""
    from backend.travel.core.plan_service import (
        PlanVersionConflict, PlanVersionInvalid, PlanVersionNotFound,
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
        conversation_id, identity.user_id or "")
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
            req.conversation_id, identity.user_id or "", req.plan_version)
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
            target_version=req.target_version, base_version=req.base_version)
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
            from_version=from_version, to_version=to_version)
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

