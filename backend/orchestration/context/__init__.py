"""orchestration.context — 会话级跨轮上下文与 follow-up 解析（P2/D1）。"""
from backend.orchestration.context.conversation_context import (
    ConversationContext,
    ConversationContextStore,
    get_conversation_context_store,
    sync_travel_brief_to_context,
)
from backend.orchestration.context.continuation_resolver import (
    is_continuation_query,
    resolve_continuation,
)
from backend.orchestration.context.follow_up_resolver import (
    CLARIFICATION_QUESTION,
    apply_resolution_to_context,
    resolve_followup,
)
from backend.orchestration.context.routing_context import (
    assemble_routing_context,
    mark_domain_turn,
    set_pending_question,
)

__all__ = [
    "ConversationContext",
    "ConversationContextStore",
    "get_conversation_context_store",
    "sync_travel_brief_to_context",
    "CLARIFICATION_QUESTION",
    "apply_resolution_to_context",
    "resolve_followup",
    "is_continuation_query",
    "resolve_continuation",
    "assemble_routing_context",
    "mark_domain_turn",
    "set_pending_question",
]
