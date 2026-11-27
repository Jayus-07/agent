"""智能客服管理端 API 路由门面。

本模块遵照单一职责原则（SRP）解耦，物理实现拆分至 `cs_admin_subroutes` 子领域模块：
- `schemas.py`: 请求与响应数据契约
- `deps.py`: 身份鉴权、租户范围与安全门禁
- `confirm.py`: 事务确认与退款申请路由
- `conversations.py`: 会话管理、监控统计与事件回放路由
- `handoff.py`: 坐席工作台、转接队列与认领流转路由
- `user_messages.py`: 客户侧会话与交互路由
- `base.py`: 动态门面透明代理与公共常量

本文件作为统一路由聚合入口与向后兼容门面，保留历史导出符号与 Monkeypatch 穿透能力。
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.routes.cs_admin_subroutes import (
    base,
    confirm,
    conversations,
    deps,
    handoff,
    schemas,
    user_messages,
)
from backend.app.api.routes.cs_admin_subroutes.base import (
    _CS_OPERATOR_ROLES,
    logger,
)

# 重新导出所有契约、依赖、函数与路由符号（保持 100% 外部导入及 monkeypatch 兼容）
ConfirmActionBody = schemas.ConfirmActionBody
RefundDraftUpdateBody = schemas.RefundDraftUpdateBody
MessageDTO = schemas.MessageDTO
ConversationSummary = schemas.ConversationSummary
ConversationDetail = schemas.ConversationDetail
PaginatedConversations = schemas.PaginatedConversations
MyConversationMessage = schemas.MyConversationMessage
MyConversationItem = schemas.MyConversationItem
MyConversationsResponse = schemas.MyConversationsResponse
RatingRequest = schemas.RatingRequest
CSStatsResponse = schemas.CSStatsResponse
MyMessageBody = schemas.MyMessageBody
HandoffQueueItem = schemas.HandoffQueueItem
HandoffQueueResponse = schemas.HandoffQueueResponse
ClaimRequest = schemas.ClaimRequest
AgentMessageRequest = schemas.AgentMessageRequest
TypingRequest = schemas.TypingRequest
AgentMessageResponse = schemas.AgentMessageResponse
HandoffMeta = schemas.HandoffMeta
HandoffMessagesResponse = schemas.HandoffMessagesResponse
_use_java_source = deps._use_java_source
_is_service_channel = deps._is_service_channel
_is_cs_operator = deps._is_cs_operator
_require_cs_operator = deps._require_cs_operator
_resolve_tenant_scope = deps._resolve_tenant_scope
_require_operator_and_tenant = deps._require_operator_and_tenant
_resolve_agent_identity = deps._resolve_agent_identity
_resolve_agent_identity_and_tenant = deps._resolve_agent_identity_and_tenant
_lookup_bound_agent_id = deps._lookup_bound_agent_id
_ensure_conversation_access = deps._ensure_conversation_access
_assert_current_agent = deps._assert_current_agent
_load_conversation_access_row = deps._load_conversation_access_row
_ensure_pending_proposal_access = deps._ensure_pending_proposal_access
_proxy_to_java = deps._proxy_to_java
_resolve_ws_agent_identity = deps._resolve_ws_agent_identity
_ensure_my_conversation = deps._ensure_my_conversation
_ensure_cs_agent = deps._ensure_cs_agent
_load_assigned_agent_id = deps._load_assigned_agent_id
confirm_pending_action = confirm.confirm_pending_action
update_my_refund_draft = confirm.update_my_refund_draft
list_my_refund_requests = confirm.list_my_refund_requests
get_my_refund_request = confirm.get_my_refund_request
list_conversations = conversations.list_conversations
cs_stats = conversations.cs_stats
replay_conversation_events = conversations.replay_conversation_events
rate_conversation = conversations.rate_conversation
get_conversation_pending_proposal = conversations.get_conversation_pending_proposal
get_conversation_customer_context = conversations.get_conversation_customer_context
get_conversation = conversations.get_conversation
get_conversation_traces = conversations.get_conversation_traces
get_conversation_messages = conversations.get_conversation_messages
_async_list_conversations = conversations._async_list_conversations
_async_cs_stats = conversations._async_cs_stats
_async_get_conversation = conversations._async_get_conversation
_async_get_trace_ids = conversations._async_get_trace_ids
_async_messages_since = conversations._async_messages_since
get_handoff_queue = handoff.get_handoff_queue
issue_agent_ws_ticket = handoff.issue_agent_ws_ticket
close_conversation = handoff.close_conversation
claim_conversation = handoff.claim_conversation
post_agent_message = handoff.post_agent_message
post_agent_typing = handoff.post_agent_typing
_async_handoff_queue = handoff._async_handoff_queue
_async_claim = handoff._async_claim
_async_agent_message = handoff._async_agent_message
list_my_conversations = user_messages.list_my_conversations
post_my_message = user_messages.post_my_message
get_my_pending_action = user_messages.get_my_pending_action
get_my_conversation_messages = user_messages.get_my_conversation_messages
post_my_typing = user_messages.post_my_typing
_async_save_my_message = user_messages._async_save_my_message
_async_my_conversations = user_messages._async_my_conversations

# 组装主路由与确认路由
router = APIRouter(prefix="/cs/conversations", tags=["智能客服-管理"])
confirm_router = APIRouter(prefix="/cs", tags=["智能客服-确认"])

# 挂载子路由到 confirm_router
confirm_router.include_router(confirm.router)

# 挂载子路由到 router (确保特定路径优先匹配，避免 /{conversation_id} 劫持)
router.include_router(user_messages.router)
router.include_router(handoff.router)
router.include_router(conversations.router)
