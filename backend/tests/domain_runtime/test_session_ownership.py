# -*- coding: utf-8 -*-
"""test_session_ownership.py — C4 session 属主隔离回归（Platform Readiness STOP C）

P0 修复：chat_sessions.session_id 全局唯一且 get_or_create 无属主校验，
任何用户可用他人 session_id「收养」会话——读取全部 L2 历史并把新消息
写入同一历史（实机复现：两租户 14 条 chat_messages 混流）。

修复语义：
- SessionRepository.get_or_create：属主不匹配 → SessionOwnerMismatch（fail-closed）
- MemoryService 三入口（start_session/end_turn/save_messages）：捕获后派生
  隔离存储键 `{session}::u:{sha1(user)[:8]}` 重试——B 的读写落到自己的键下，
  A 的历史零暴露，无需 schema 变更。
集成行为由 scripts/e2e_tenant_collision.py 隔离实例实机复验。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from backend.memory.models.session import ChatSession
from backend.memory.repository.session_repo import (
    SessionOwnerMismatch,
    SessionRepository,
)


class _FakeExec:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """只覆盖 get_or_create 用到的 execute/add/flush 面。"""

    def __init__(self, existing: ChatSession | None):
        self.existing = existing
        self.added: list[ChatSession] = []

    async def execute(self, _stmt):
        return _FakeExec(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


class TestGetOrCreateOwnership:
    def test_owner_match_returns_row(self):
        row = ChatSession(session_id="s1", user_id="u1")
        repo = SessionRepository(_FakeSession(row))
        assert asyncio.run(repo.get_or_create("s1", "u1")) is row

    def test_owner_mismatch_raises(self):
        row = ChatSession(session_id="s1", user_id="u1")
        repo = SessionRepository(_FakeSession(row))
        with pytest.raises(SessionOwnerMismatch):
            asyncio.run(repo.get_or_create("s1", "u2"))

    def test_new_session_created_for_any_user(self):
        repo = SessionRepository(_FakeSession(None))
        row = asyncio.run(repo.get_or_create("s9", "u9"))
        assert row.session_id == "s9" and row.user_id == "u9"


class TestScopedSessionId:
    def test_deterministic_per_user_and_distinct_across_users(self):
        from backend.memory.service import MemoryService

        a1 = MemoryService._scoped_session_id("sess", "uA")
        a2 = MemoryService._scoped_session_id("sess", "uA")
        b = MemoryService._scoped_session_id("sess", "uB")
        assert a1 == a2
        assert a1 != b
        assert a1.startswith("sess::u:")
