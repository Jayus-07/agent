"""为客服记忆 API 适配既有客服会话事实源。

会话列表、消息、重命名和删除均复用 customer_service.conversations/messages；
此模块只把客服存储映射为现有前端 memory API 的响应形状。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.customer_service.managers.conversation_manager import ConversationManager
from backend.customer_service.managers.message_manager import MessageManager
from backend.customer_service.models.message import CSMessage
from backend.memory.database import AsyncSessionLocal
from backend.shared.logger import logger


def _run(coroutine):
    """在客服数据库专属事件循环中执行 async ORM 操作。"""
    from backend.customer_service._db_loop import run_sync

    try:
        return run_sync(coroutine)
    finally:
        if getattr(coroutine, "cr_frame", None) is not None:
            coroutine.close()


def _parse_cursor(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def list_sessions(
    *, user_id: str, tenant_id: str, limit: int = 50,
    before: str | None = None, before_session_id: str | None = None,
) -> dict:
    return _run(_list_sessions(
        user_id=user_id,
        tenant_id=tenant_id,
        limit=limit,
        before=before,
        before_session_id=before_session_id,
    ))


async def _list_sessions(
    *, user_id: str, tenant_id: str, limit: int,
    before: str | None, before_session_id: str | None,
) -> dict:
    cursor = _parse_cursor(before)
    safe_limit = max(1, min(int(limit), 200))
    async with AsyncSessionLocal() as db:
        try:
            conversations = ConversationManager(db)
            rows = await conversations.list_by_owner(
                user_id,
                tenant_id,
                limit=safe_limit + 1,
                before=cursor,
                before_conversation_id=before_session_id,
            )
            has_more = len(rows) > safe_limit
            rows = rows[:safe_limit]
            conversation_ids = [row.conversation_id for row in rows]
            counts: dict[str, int] = {}
            first_messages: dict[str, str] = {}
            if conversation_ids:
                count_rows = await db.execute(
                    select(CSMessage.conversation_id, func.count(CSMessage.id))
                    .where(
                        CSMessage.conversation_id.in_(conversation_ids),
                        CSMessage.private.is_(False),
                    )
                    .group_by(CSMessage.conversation_id)
                )
                counts = {key: int(count) for key, count in count_rows.all()}

                ranked_messages = (
                    select(
                        CSMessage.conversation_id.label("conversation_id"),
                        CSMessage.content.label("content"),
                        func.row_number().over(
                            partition_by=CSMessage.conversation_id,
                            order_by=(CSMessage.created_at.asc(), CSMessage.id.asc()),
                        ).label("position"),
                    )
                    .where(
                        CSMessage.conversation_id.in_(conversation_ids),
                        CSMessage.sender_type == "user",
                        CSMessage.private.is_(False),
                    )
                    .subquery()
                )
                first_rows = await db.execute(
                    select(
                        ranked_messages.c.conversation_id,
                        ranked_messages.c.content,
                    ).where(ranked_messages.c.position == 1)
                )
                first_messages = dict(first_rows.all())

            total = (
                await conversations.count_by_owner(user_id, tenant_id)
                if before is None else len(rows)
            )
            result = {
                "sessions": [
                    {
                        "session_id": row.conversation_id,
                        "title": (
                            (row.summary or "").strip()
                            or (first_messages.get(row.conversation_id, "").strip()[:80])
                            or "客服会话"
                        ),
                        "message_count": counts.get(row.conversation_id, 0),
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                        "context_summary": row.context_summary,
                    }
                    for row in rows
                ],
                "total": total,
                "has_more": has_more,
            }
            await db.commit()
            return result
        except Exception as exc:
            await db.rollback()
            logger.error("[CSMemoryHistory] list sessions failed: %s", exc)
            return {"sessions": [], "total": 0, "error": str(exc)}


def get_session_messages(*, session_id: str, user_id: str, tenant_id: str) -> dict:
    return _run(_get_session_messages(
        session_id=session_id, user_id=user_id, tenant_id=tenant_id,
    ))


async def _get_session_messages(
    *, session_id: str, user_id: str, tenant_id: str,
) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            conversations = ConversationManager(db)
            conversation = await conversations.get_by_owner(
                session_id, user_id, tenant_id,
            )
            if conversation is None:
                return {"session_id": session_id, "messages": [], "error": "会话不存在"}
            messages = await MessageManager(db).load_messages(session_id)
            result = {
                "session_id": session_id,
                "messages": [
                    {
                        "id": message.message_id,
                        "role": message.role or message.sender_type,
                        "content": message.content,
                        "created_at": (
                            message.created_at.isoformat()
                            if message.created_at else None
                        ),
                    }
                    for message in messages
                ],
            }
            await db.commit()
            return result
        except Exception as exc:
            await db.rollback()
            logger.error("[CSMemoryHistory] get session failed: %s", exc)
            return {"session_id": session_id, "messages": [], "error": str(exc)}


def delete_session(*, session_id: str, user_id: str, tenant_id: str) -> dict:
    return _run(_delete_session(
        session_id=session_id, user_id=user_id, tenant_id=tenant_id,
    ))


async def _delete_session(
    *, session_id: str, user_id: str, tenant_id: str,
) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            deleted = await ConversationManager(db).delete_by_owner(
                session_id, user_id, tenant_id,
            )
            await db.commit()
            if not deleted:
                return {"ok": False, "error": "会话不存在"}
            return {"ok": True, "session_id": session_id}
        except Exception as exc:
            await db.rollback()
            logger.error("[CSMemoryHistory] delete session failed: %s", exc)
            return {"ok": False, "error": str(exc)}


def rename_session(
    *, session_id: str, user_id: str, tenant_id: str, title: str,
) -> dict:
    return _run(_rename_session(
        session_id=session_id, user_id=user_id, tenant_id=tenant_id, title=title,
    ))


async def _rename_session(
    *, session_id: str, user_id: str, tenant_id: str, title: str,
) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            updated = await ConversationManager(db).rename_by_owner(
                session_id, user_id, tenant_id, title.strip(),
            )
            await db.commit()
            if not updated:
                return {"ok": False, "error": "会话不存在"}
            return {"ok": True, "session_id": session_id, "title": title.strip()}
        except Exception as exc:
            await db.rollback()
            logger.error("[CSMemoryHistory] rename session failed: %s", exc)
            return {"ok": False, "error": str(exc)}
