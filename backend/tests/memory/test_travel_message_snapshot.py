"""旅游会话快照复用 chat_messages，重试替换而不重复追加。"""
from __future__ import annotations

import uuid

import psycopg2
import pytest
from sqlalchemy import text

from backend.config.database import MEMORY_DB_CONFIG
from backend.memory.database import AsyncSessionLocal
from backend.memory.service import MemoryService

pytestmark = pytest.mark.asyncio


def _require_memory_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM chat_messages LIMIT 1")
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过消息快照验收: {exc}")


async def test_replacing_same_travel_snapshot_does_not_duplicate_messages() -> None:
    _require_memory_pg()
    session_id = f"travel-message-test-{uuid.uuid4().hex}"
    user_id = f"travel-message-test-{uuid.uuid4().hex}"
    service = MemoryService()
    first = [
        {"role": "user", "content": "帮我安排杭州两天"},
        {"role": "assistant", "content": "已生成杭州行程草案"},
    ]
    replacement = [
        {"role": "user", "content": "预算改为 2000 元"},
        {"role": "assistant", "content": "已按 2000 元更新草案"},
    ]

    try:
        assert await service.replace_session_messages(session_id, first, user_id) == {
            "saved": 2,
        }
        assert await service.replace_session_messages(session_id, first, user_id) == {
            "saved": 2,
        }
        restored = await service.get_session_messages(session_id, user_id)
        assert [(item["role"], item["content"]) for item in restored["messages"]] == [
            ("user", "帮我安排杭州两天"),
            ("assistant", "已生成杭州行程草案"),
        ]

        await service.replace_session_messages(session_id, replacement, user_id)
        restored = await service.get_session_messages(session_id, user_id)
        assert [(item["role"], item["content"]) for item in restored["messages"]] == [
            ("user", "预算改为 2000 元"),
            ("assistant", "已按 2000 元更新草案"),
        ]
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM chat_sessions WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
            await db.commit()
