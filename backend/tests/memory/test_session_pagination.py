"""L1 会话列表同时间戳下使用 session_id 游标稳定翻页。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, update

from backend.memory.database import AsyncSessionLocal
from backend.memory.models.session import ChatMessage, ChatSession
from backend.memory.repository.session_repo import SessionRepository
from backend.tests.memory.conftest import require_memory_pg

pytestmark = pytest.mark.asyncio

_RUN = uuid.uuid4().hex[:12]
_USER = f"session-page-{_RUN}"
_SESSIONS = (f"session-page-{_RUN}-a", f"session-page-{_RUN}-b")


@pytest.fixture(autouse=True)
async def _isolated_sessions():
    require_memory_pg()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ChatMessage).where(
            ChatMessage.session_id.in_(_SESSIONS),
        ))
        await db.execute(delete(ChatSession).where(
            ChatSession.session_id.in_(_SESSIONS),
            ChatSession.user_id == _USER,
        ))
        await db.commit()


async def test_equal_updated_at_pages_by_session_id_without_skipping():
    fixed_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        for session_id in _SESSIONS:
            await repo.get_or_create(session_id, _USER)
        await db.execute(update(ChatSession).where(
            ChatSession.session_id.in_(_SESSIONS),
        ).values(created_at=fixed_time, updated_at=fixed_time))
        await db.commit()

        first_page = await repo.list_all(_USER, limit=1)
        assert len(first_page) == 1
        first = first_page[0]
        second_page = await repo.list_all(
            _USER,
            limit=1,
            before=first["updated_at"],
            before_session_id=first["session_id"],
        )
        second_page_with_aware_cursor = await repo.list_all(
            _USER,
            limit=1,
            before=fixed_time.isoformat(),
            before_session_id=first["session_id"],
        )

    assert first["session_id"] == max(_SESSIONS)
    assert [row["session_id"] for row in second_page] == [min(_SESSIONS)]
    assert [row["session_id"] for row in second_page_with_aware_cursor] == [
        min(_SESSIONS),
    ]
