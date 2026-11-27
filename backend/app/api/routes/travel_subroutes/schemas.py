"""Travel API 请求/响应 Pydantic 数据模型与请求体加载解析。"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from backend.travel.models.brief import TravelBrief
from .base import facade

class TravelUiContext(BaseModel):
    selected_day: int | None = Field(None, ge=1)
    selected_poi_id: str | None = Field(None, max_length=128)


class TravelPlanRequest(BaseModel):
    # 20000 对齐同文件 TravelConversationMessage.content 上限。V2 只读咨询的
    # message 由前端拼入行程事实 JSON（travelV2Conversation 预算 6500 字符
    # + 指令与问题），实测两天行程即超旧上限 2000 并触发 422，只读通道整体不可用。
    message: str = Field(..., min_length=1, max_length=20000,
                         description="本轮用户输入（如「帮我排杭州2天行程，亲子，带娃不累」）；V2 只读咨询会携带行程事实 JSON，上限按 V2 咨询 prompt 设计预算放宽")
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


TravelPlanRequest.model_rebuild()

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
    return facade._parse_travel_plan_request(request_body)


class IcsExportRequest(BaseModel):
    itinerary: dict = Field(..., description="完整 Itinerary JSON（/plan 返回的 itinerary 字段）")


class TravelFeedbackRequest(BaseModel):
    session_id: str = Field("", max_length=128)
    vote: str = Field(..., pattern="^(positive|negative)$")
    reason: str = Field("", max_length=500,
                        description="不满意的具体点（如「第二天太赶」「门票比实际贵」）")
    destination: str = Field("", max_length=64)
    plan_version: int = Field(0, ge=0)


class TravelPreferencesRequest(BaseModel):
    origin: str = Field("", max_length=64)
    preferences: list[str] = Field(default_factory=list, max_length=16)
    pace: str = Field("", pattern="^(relaxed|moderate|intense|)$")
    diet: str = Field("", max_length=64)
    lodging: str = Field("", max_length=64)
    transport: str = Field("", max_length=64)


class TravelPlanConfirmRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    plan_version: int = Field(..., ge=1,
                              description="要确认的版本号；必须是当前最新版")


class TravelPlanDiscardRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    plan_version: int = Field(..., ge=1,
                              description="要放弃的当前待确认草案版本")


class TravelPlanRestoreRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=128)
    target_version: int = Field(..., ge=1,
                                description="要恢复到的历史版本号（内容来源）")
    base_version: int = Field(..., ge=1,
                              description="乐观并发基准：发起请求时看到的当前最新版本号")


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


facade.register("TravelUiContext", TravelUiContext)
facade.register("TravelPlanRequest", TravelPlanRequest)
facade.register("TravelResponseMetadata", TravelResponseMetadata)
facade.register("TravelConversationMessage", TravelConversationMessage)
facade.register("TravelConversationMessagesRequest", TravelConversationMessagesRequest)
facade.register("_parse_travel_plan_request", _parse_travel_plan_request)
facade.register("_load_travel_plan_request", _load_travel_plan_request)
facade.register("IcsExportRequest", IcsExportRequest)
facade.register("TravelFeedbackRequest", TravelFeedbackRequest)
facade.register("TravelPreferencesRequest", TravelPreferencesRequest)
facade.register("TravelPlanConfirmRequest", TravelPlanConfirmRequest)
facade.register("TravelPlanDiscardRequest", TravelPlanDiscardRequest)
facade.register("TravelPlanRestoreRequest", TravelPlanRestoreRequest)
facade.register("TravelDecisionRequest", TravelDecisionRequest)
