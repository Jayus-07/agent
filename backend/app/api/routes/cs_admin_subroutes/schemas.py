"""CS Admin 数据契约与 Schema 定义。"""
from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field

class ConfirmActionBody(BaseModel):
    """确认卡片点击请求。decision: confirm | cancel"""
    session_id: str
    decision: str
    proposal_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    client_action_id: UUID | None = None


class RefundDraftUpdateBody(BaseModel):
    proposal_id: str
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class MessageDTO(BaseModel):
    message_id: str
    sender_type: str
    content: str
    content_type: str = "text"
    intent_domain: str | None = None
    intent_name: str | None = None
    confidence: float | None = None
    trace_id: str | None = None
    # M2：card 消息附件（[{"type":"order_ref","order_no":...}]），随轮询/
    # 对账响应透传给坐席端渲染订单卡（与 WS message.created 同构）
    attachments: list[dict] | None = None
    created_at: str


class ConversationSummary(BaseModel):
    conversation_id: str
    user_id: str
    conversation_status: str
    handling_mode: str
    priority: str
    message_count: int = 0
    trace_count: int = 0
    last_trace_id: str | None = None
    last_activity_at: str | None = None
    created_at: str
    summary: str | None = None


class ConversationDetail(BaseModel):
    conversation_id: str
    user_id: str
    conversation_status: str
    handling_mode: str
    priority: str
    channel: str
    summary: str | None = None
    context_summary: str | None = None
    handoff_id: str | None = None
    trace_count: int = 0
    last_trace_id: str | None = None
    created_at: str
    last_activity_at: str | None = None
    messages: list[MessageDTO]


class PaginatedConversations(BaseModel):
    items: list[ConversationSummary]
    total: int
    has_more: bool


class MyConversationMessage(BaseModel):
    message_id: str
    sender_type: str
    content: str
    content_type: str = "text"
    # M2：card 附件随会话回放透传（用户端历史会话渲染订单卡）
    attachments: list[dict] | None = None
    created_at: str


class MyConversationItem(BaseModel):
    conversation_id: str
    summary: str | None = None
    conversation_status: str
    latest_handoff_closed: bool = False
    handling_mode: str
    created_at: str
    last_activity_at: str | None = None
    messages: list[MyConversationMessage]


class MyConversationsResponse(BaseModel):
    items: list[MyConversationItem]


class RatingRequest(BaseModel):
    rating: int
    comment: str | None = None


class CSStatsResponse(BaseModel):
    """管理端统计汇总（满意度 + 意图分布 + 转人工率）"""
    session_count: int = 0
    message_count: int = 0
    rated_count: int = 0
    avg_rating: float | None = None
    rating_dist: dict[int, int] = {}
    intent_dist: list[dict] = []   # [{name, count}]
    handoff_count: int = 0


class MyMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    # M2：人工服务态发送订单卡等结构化附件（[{"type":"order_ref","order_no":...}]）；
    # 非空时 content_type=card 落库并随 message.created 广播。上限 4 条防滥用。
    attachments: list[dict] | None = None


class HandoffQueueItem(BaseModel):
    conversation_id: str
    user_id: str
    handoff_state: str
    # STOP CS-A P0-5/E4：主管重派 API 以 handoff_id 寻址，队列项透出该键
    handoff_id: str | None = None
    trigger_type: str | None = None
    trigger_reason: str | None = None
    updated_at: str
    last_message_preview: str | None = None


class HandoffQueueResponse(BaseModel):
    items: list[HandoffQueueItem]
    total: int


class ClaimRequest(BaseModel):
    # P7：浏览器不再提交可信 agent_id —— JWT 通道由服务端从
    # cs_agents.auth_user_id 反查；仅服务间 API-Key 通道仍需声明值。
    agent_id: str = ""


class AgentMessageRequest(BaseModel):
    agent_id: str = ""
    content: str


class TypingRequest(BaseModel):
    """坐席「输入中」上报体（agent_id 可选，仅作审计留痕预留）。"""

    agent_id: str = ""


class AgentMessageResponse(BaseModel):
    message_id: str
    sender_type: str
    content: str
    created_at: str


class HandoffMeta(BaseModel):
    """转人工等待元信息（A 案排队透明化，2026-10-08）：支撑用户侧倒计时。"""

    handoff_id: str
    handoff_state: str
    # 总等待截止（ISO）；前端据此渲染「剩余 N 秒，超时将自动登记工单」
    total_deadline_at: str | None = None
    # 前面还有几单（同租户 waiting_human 先到序）；0 = 下一个就轮到本单
    queue_position: int = 0


class HandoffMessagesResponse(BaseModel):
    conversation_id: str
    handoff_state: str
    last_id: int
    messages: list[MessageDTO]
    # 双向输入中指示（2026-09-18）：坐席正在输入（TTL 5s 瞬态，读 Redis）
    agent_typing: bool = False
    # 等待元信息（仅存在未关闭 handoff 时返回；none 态为 null）
    handoff_meta: HandoffMeta | None = None

