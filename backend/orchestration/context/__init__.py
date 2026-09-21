"""orchestration.context — 会话级跨轮上下文与 follow-up 解析（P2/D1）。"""
from backend.orchestration.context.conversation_context import (
    ConversationContext,
    ConversationContextStore,
    get_conversation_context_store,
    sync_travel_brief_to_context,
)
from backend.orchestration.context.follow_up_resolver import (
    CLARIFICATION_QUESTION,
    apply_resolution_to_context,
    resolve_followup,
)

__all__ = [
    "ConversationContext",
    "ConversationContextStore",
    "get_conversation_context_store",
    "sync_travel_brief_to_context",
    "CLARIFICATION_QUESTION",
    "apply_resolution_to_context",
    "resolve_followup",
]
