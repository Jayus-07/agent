"""Travel 主路由器（挂载现行端点与退役 V1 兼容路由）。"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from backend.app.api.identity import require_identity
from .base import facade
from .schemas import TravelFeedbackRequest
from .streaming import travel_v2_plan_stream
from .ics import travel_export_ics
from .conversation import (
    travel_conversation_messages,
    replace_travel_conversation_messages,
)
from .retired_v1 import _retired_v1_router

router = APIRouter(prefix="/travel", tags=["旅游域"])

async def travel_city_guide(request: Request, destination: str, force: bool = False):
    """M3-g：城市指南轻端点，不进域图。

    结果按目的地缓存 TTL 7 天（拍板口径）；知乎关闭/配额尽自动降级。
    """
    facade.require_identity(request)
    from backend.travel.services.city_guide_service import get_city_guide

    return get_city_guide(destination, force=force)


async def travel_feedback(request: Request):
    identity = facade.require_identity(request)
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


def travel_recommend(request: Request, preferences: str = "", top: int = 3):
    from backend.travel.recommend import recommend_cities

    facade.require_identity(request)
    tags = [p.strip() for p in preferences.split(",") if p.strip()]
    recs = recommend_cities(tags, top=max(1, min(top, 5)))
    return {"recommendations": [r.to_dict() for r in recs]}


# 注册城市指南、反馈与推荐端点
router.get("/city-guide", summary="城市指南（四级内容链：文档摘要→RAG→知乎→暂无）")(travel_city_guide)
router.post("/feedback", summary="行程单反馈（收藏/不满意）")(travel_feedback)
router.get("/recommend", summary="按偏好推荐目的地")(travel_recommend)

# 注册 V2 核心规划流端点
router.post("/v2/plan/stream", summary="旅游 V2 Agent 规划流")(travel_v2_plan_stream)

# 注册 ICS 导出端点
router.post(
    "/export/ics",
    summary="行程导出为 ICS 日历文件",
    responses={200: {"content": {"text/calendar": {}}}},
)(travel_export_ics)

# 注册会话消息端点
router.get(
    "/conversations/{conversation_id}/messages",
    summary="恢复当前用户有权访问的旅游对话消息",
)(travel_conversation_messages)

router.put(
    "/conversations/{conversation_id}/messages",
    summary="幂等替换当前用户的旅游对话消息快照",
)(replace_travel_conversation_messages)

facade.register("router", router)
facade.register("travel_city_guide", travel_city_guide)
facade.register("travel_feedback", travel_feedback)
facade.register("travel_recommend", travel_recommend)
