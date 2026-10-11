"""L2 会话 tenant/user 复合隔离回归。"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.memory.models.session import Base, ChatSession
from backend.memory.repository.session_repo import (
    SessionOwnerMismatch,
    SessionRepository,
)
from backend.memory.service import MemoryService


def test_repository_and_service_scope_same_user_sessions_by_tenant_in_sqlite(monkeypatch):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False,
            )
            from backend.memory import service as service_module

            monkeypatch.setattr(service_module, "AsyncSessionLocal", factory)
            async with factory() as db:
                repo = SessionRepository(db)
                await repo.get_or_create("same-session", "same-user", "tenant-a")
                await repo.save_turn("same-session", "A 的问题", "A 的回答")

                with pytest.raises(SessionOwnerMismatch):
                    await repo.get_or_create(
                        "same-session", "same-user", "tenant-b",
                    )

                scoped_id = MemoryService._scoped_session_id(
                    "same-session", "same-user", "tenant-b",
                )
                await repo.get_or_create(scoped_id, "same-user", "tenant-b")
                await repo.save_turn(scoped_id, "B 的问题", "B 的回答")
                await db.commit()

                owner_a = await repo.get_owner("same-session")
                owner_b = await repo.get_owner(scoped_id)
                sessions_a = await repo.list_all(
                    "same-user", tenant_id="tenant-a",
                )
                sessions_b = await repo.list_all(
                    "same-user", tenant_id="tenant-b",
                )
                messages_a = await repo.load_messages("same-session")
                messages_b = await repo.load_messages(scoped_id)
                legacy_id = MemoryService._scoped_session_id(
                    "legacy-session", "same-user",
                )
                await repo.get_or_create(legacy_id, "same-user", "tenant-b")
                await db.commit()
                service = MemoryService()
                assert await service._scoped_session_id_for_owner(
                    repo, "legacy-session", "same-user", "tenant-b",
                ) == legacy_id
                assert await service._scoped_session_id_for_owner(
                    repo, "legacy-session", "same-user", "tenant-a",
                ) != legacy_id

            read_a = await service.get_session_messages(
                "same-session", "same-user", "tenant-a",
            )
            read_b = await service.get_session_messages(
                scoped_id, "same-user", "tenant-b",
            )
            cross_tenant_read = await service.get_session_messages(
                "same-session", "same-user", "tenant-b",
            )
            cross_tenant_rename = await service.rename_session(
                "same-session", "越权改名", "same-user", "tenant-b",
            )
            cross_tenant_delete = await service.delete_session(
                "same-session", "same-user", "tenant-b",
            )
            list_a = await service.list_sessions("same-user", tenant_id="tenant-a")
            list_b = await service.list_sessions("same-user", tenant_id="tenant-b")

            assert owner_a == ("tenant-a", "same-user")
            assert owner_b == ("tenant-b", "same-user")
            assert [row["session_id"] for row in sessions_a] == ["same-session"]
            assert [row["session_id"] for row in sessions_b] == [scoped_id]
            assert [message.content for message in messages_a] == ["A 的问题", "A 的回答"]
            assert [message.content for message in messages_b] == ["B 的问题", "B 的回答"]
            assert [message["content"] for message in read_a["messages"]] == [
                "A 的问题", "A 的回答",
            ]
            assert [message["content"] for message in read_b["messages"]] == [
                "B 的问题", "B 的回答",
            ]
            assert cross_tenant_read["error"] == "会话不存在"
            assert cross_tenant_rename["error"] == "会话不存在"
            assert cross_tenant_delete["error"] == "会话不存在"
            assert [row["session_id"] for row in list_a["sessions"]] == [
                "same-session",
            ]
            assert {row["session_id"] for row in list_b["sessions"]} == {
                scoped_id, legacy_id,
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scoped_session_id_is_stable_per_tenant_and_bounded():
    source = "s" * 128
    tenant_a = MemoryService._scoped_session_id(source, "same-user", "tenant-a")
    tenant_b = MemoryService._scoped_session_id(source, "same-user", "tenant-b")
    default = MemoryService._scoped_session_id("short", "same-user")

    assert tenant_a == MemoryService._scoped_session_id(
        source, "same-user", "tenant-a",
    )
    assert tenant_a != tenant_b
    assert len(tenant_a) <= 128
    assert default == MemoryService._scoped_session_id("short", "same-user", "default")
    assert default.startswith("short::u:")
