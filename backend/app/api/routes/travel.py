"""app/api/routes/travel.py — 旅游域 REST API（2026-09-22 P0-4 + P1）

与 chat SSE 并行的**非流式**旅游域入口。为什么需要它：
  1. 前端行程页需要结构化行程（itinerary JSON）做时间轴卡片、地图打点、
     ICS 导出 —— 走 chat SSE 只能拿到 markdown 文本，结构化信息全丢；
  2. ICS 导出是纯转换（行程 JSON → 日历文本），天然适合独立端点；
  3. 反馈/偏好/推荐是轻量 REST 语义，不适合塞进对话流。

高并发考量：
  - /plan 同步 def，FastAPI 自动进线程池执行；域图 invoke 复用
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

import json
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
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


def _plan_error(message: str, status: str = "failed") -> dict:
    return {"status": status, "final_answer": message, "itinerary": None}


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

    try:
        raw = await request.json()
        req = TravelPlanRequest(**raw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422,
                            detail=f"TravelPlanRequest 解析失败: {e}")

    conversation_id = req.conversation_id or req.session_id
    graph_input = new_travel_graph_input(
        user_message=req.message,
        user_id=identity.user_id or "",
        session_id=req.session_id,
        conversation_id=conversation_id,
    )
    try:
        final_state = get_travel_graph().invoke(
            graph_input, config=_build_invoke_config(conversation_id))
    except Exception:
        logger.exception("[TravelAPI] 域图执行异常")
        return _plan_error("抱歉，旅游规划服务暂时不可用，请稍后再试。")
    result = build_travel_graph_result(final_state)
    return dict(result)


# ============================================================
# POST /travel/export/ics — 行程 → 日历文件（纯转换，无状态）
# ============================================================
class IcsExportRequest(BaseModel):
    itinerary: dict = Field(..., description="完整 Itinerary JSON（/plan 返回的 itinerary 字段）")


_ICS_TIME_FMT = "%Y%m%dT%H%M%S"


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
                f"DTSTART:{start}",
                f"DTEND:{end}",
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
