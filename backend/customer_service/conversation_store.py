"""customer_service/conversation_store.py — CS turn ↔ trace persistence

Sync facade that records a CS conversation turn (user question + assistant answer)
together with its trace_id into PostgreSQL.  Uses the dedicated ``cs-db-loop``
event loop (``_db_loop.run_sync``) so graph nodes (sync) never block on DB I/O.

All public methods are fire-and-forget: exceptions are caught and logged,
never propagated to the caller.
"""
from __future__ import annotations

from backend.shared.logger import logger


def record_cs_turn(
    conversation_id: str,
    user_id: str,
    question: str,
    answer: str,
    trace_id: str | None = None,
    cs_route: dict | None = None,
) -> None:
    """Persist one user+assistant message pair with optional trace linkage.

    Idempotent upsert: get_or_create the conversation, then save both messages.
    Safe to call from sync graph-node context — internally dispatches to the
    background event loop.
    """
    try:
        from backend.customer_service._db_loop import run_sync
        run_sync(_async_record_turn(
            conversation_id, user_id, question, answer, trace_id, cs_route,
        ))
    except Exception:
        logger.debug("[ConversationStore] record_cs_turn failed", exc_info=True)


async def _async_record_turn(
    conversation_id: str,
    user_id: str,
    question: str,
    answer: str,
    trace_id: str | None,
    cs_route: dict | None,
) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import update
    from sqlalchemy import select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.message import CSMessage
    from backend.customer_service.managers.conversation_manager import ConversationManager
    from backend.customer_service.managers.message_manager import MessageManager
    from backend.memory.database import AsyncSessionLocal

    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        conv_mgr = ConversationManager(db)
        msg_mgr = MessageManager(db)

        conv, created = await conv_mgr.get_or_create(
            conversation_id, user_id,
        )

        intent_domain = None
        intent_name = None
        confidence = None
        if cs_route:
            intent_name = cs_route.get("intent")
            confidence = cs_route.get("confidence")
            domain = cs_route.get("domain")
            if domain:
                intent_domain = domain if isinstance(domain, str) else str(domain)

        await msg_mgr.save_turn(
            conversation_id, question, answer,
            intent_domain=intent_domain,
            intent_name=intent_name,
            confidence=confidence,
            trace_id=trace_id,
        )

        values: dict = {
            "last_activity_at": now,
            "updated_at": now,
        }
        if trace_id:
            values["last_trace_id"] = trace_id
            values["trace_count"] = (conv.trace_count or 0) + 1

        await db.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(**values)
        )

        await db.commit()

    # 发布消息事件到 Kafka（fire-and-forget，Kafka 未启用/不可用时静默跳过）
    try:
        from backend.config.messaging import TOPIC_MESSAGE_EVENTS
        from backend.infra.messaging.kafka import publish_event
        publish_event(
            TOPIC_MESSAGE_EVENTS,
            "message.created",
            conversation_id,
            user_id,
            {
                "trace_id": trace_id,
                "intent_domain": intent_domain,
                "intent_name": intent_name,
                "confidence": confidence,
            },
        )
    except Exception:
        logger.debug("[ConversationStore] event publish failed", exc_info=True)
