"""test_cs_admin_api.py — CS Admin API route tests.

Covers:
- GET /cs/conversations — paginated list, filters, keyset cursor
- GET /cs/conversations/{id} — detail with messages, 404 on missing
- GET /cs/conversations/{id}/traces — trace resolution, 503 on DB error
- Backward compatibility: existing observability endpoints unaffected
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_admin


@pytest.fixture
def client(monkeypatch):
    """Minimal FastAPI app with only the cs_admin router."""
    app = FastAPI()
    app.include_router(cs_admin.router)
    return TestClient(app, raise_server_exceptions=False)


def _make_summary(**overrides):
    defaults = dict(
        conversation_id="conv-001",
        user_id="user-1",
        conversation_status="active",
        handling_mode="ai",
        priority="normal",
        message_count=4,
        trace_count=2,
        last_trace_id="trace-abc",
        last_activity_at="2026-09-04T10:00:00+00:00",
        created_at="2026-09-04T09:00:00+00:00",
        summary=None,
    )
    defaults.update(overrides)
    return defaults


class TestListConversations:

    def test_empty_list(self, client, monkeypatch):
        """No conversations → empty items, total=0, has_more=False."""
        async def fake_list(**kwargs):
            return cs_admin.PaginatedConversations(items=[], total=0, has_more=False)

        monkeypatch.setattr(cs_admin, "_async_list_conversations", fake_list)
        resp = client.get("/cs/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    def test_returns_items(self, client, monkeypatch):
        """List endpoint returns conversation summaries."""
        items = [cs_admin.ConversationSummary(**_make_summary())]

        async def fake_list(**kwargs):
            return cs_admin.PaginatedConversations(items=items, total=1, has_more=False)

        monkeypatch.setattr(cs_admin, "_async_list_conversations", fake_list)
        resp = client.get("/cs/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["conversation_id"] == "conv-001"

    def test_db_error_returns_503(self, client, monkeypatch):
        """DB failure → 503 with detail message."""
        async def fake_list(**kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(cs_admin, "_async_list_conversations", fake_list)
        resp = client.get("/cs/conversations")
        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    def test_limit_param_accepted(self, client, monkeypatch):
        """limit query param is forwarded correctly."""
        received_kwargs = {}

        async def fake_list(**kwargs):
            received_kwargs.update(kwargs)
            return cs_admin.PaginatedConversations(items=[], total=0, has_more=False)

        monkeypatch.setattr(cs_admin, "_async_list_conversations", fake_list)
        resp = client.get("/cs/conversations?limit=5")
        assert resp.status_code == 200
        assert received_kwargs.get("limit") == 5

    def test_filter_params_accepted(self, client, monkeypatch):
        """status/handling_mode/user_id/q filters are forwarded."""
        received_kwargs = {}

        async def fake_list(**kwargs):
            received_kwargs.update(kwargs)
            return cs_admin.PaginatedConversations(items=[], total=0, has_more=False)

        monkeypatch.setattr(cs_admin, "_async_list_conversations", fake_list)
        resp = client.get("/cs/conversations?status=active&handling_mode=ai&user_id=u1&q=hello")
        assert resp.status_code == 200
        assert received_kwargs["status"] == "active"
        assert received_kwargs["handling_mode"] == "ai"
        assert received_kwargs["user_id"] == "u1"
        assert received_kwargs["q"] == "hello"


class TestGetConversation:

    def test_returns_detail(self, client, monkeypatch):
        """Existing conversation → 200 with messages."""
        detail = cs_admin.ConversationDetail(
            conversation_id="conv-001",
            user_id="user-1",
            conversation_status="active",
            handling_mode="ai",
            priority="normal",
            channel="web",
            trace_count=1,
            created_at="2026-09-04T09:00:00+00:00",
            messages=[
                cs_admin.MessageDTO(
                    message_id="msg-1",
                    sender_type="user",
                    content="你好",
                    created_at="2026-09-04T09:00:00+00:00",
                ),
            ],
        )

        async def fake_get(conv_id, run_sync):
            return detail

        monkeypatch.setattr(cs_admin, "_async_get_conversation", fake_get)
        resp = client.get("/cs/conversations/conv-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == "conv-001"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["content"] == "你好"

    def test_not_found_returns_404(self, client, monkeypatch):
        """Missing conversation → 404."""
        async def fake_get(conv_id, run_sync):
            return None

        monkeypatch.setattr(cs_admin, "_async_get_conversation", fake_get)
        resp = client.get("/cs/conversations/nonexistent")
        assert resp.status_code == 404

    def test_db_error_returns_503(self, client, monkeypatch):
        """DB failure → 503."""
        async def fake_get(conv_id, run_sync):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(cs_admin, "_async_get_conversation", fake_get)
        resp = client.get("/cs/conversations/conv-001")
        assert resp.status_code == 503


class TestGetConversationTraces:

    def test_returns_traces(self, client, monkeypatch):
        """Traces endpoint resolves trace_ids and returns DTOs."""
        async def fake_trace_ids(conv_id, run_sync):
            return ["trace-1", "trace-2"]

        monkeypatch.setattr(cs_admin, "_async_get_trace_ids", fake_trace_ids)

        fake_store = {
            "trace-1": {"id": "trace-1", "question": "q1", "duration_ms": 100},
            "trace-2": {"id": "trace-2", "question": "q2", "duration_ms": 200},
        }

        class MockStore:
            def get(self, tid):
                return fake_store.get(tid)

        monkeypatch.setattr(
            "backend.observability.trace_store.get_trace_store",
            lambda: MockStore(),
        )

        resp = client.get("/cs/conversations/conv-001/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_id"] == "conv-001"
        assert len(data["traces"]) == 2

    def test_missing_trace_skipped(self, client, monkeypatch):
        """If trace_store.get returns None, that trace is skipped."""
        async def fake_trace_ids(conv_id, run_sync):
            return ["trace-1", "trace-missing"]

        monkeypatch.setattr(cs_admin, "_async_get_trace_ids", fake_trace_ids)

        class MockStore:
            def get(self, tid):
                if tid == "trace-1":
                    return {"id": "trace-1", "question": "q1", "duration_ms": 100}
                return None

        monkeypatch.setattr(
            "backend.observability.trace_store.get_trace_store",
            lambda: MockStore(),
        )

        resp = client.get("/cs/conversations/conv-001/traces")
        assert resp.status_code == 200
        assert len(resp.json()["traces"]) == 1

    def test_db_error_returns_503(self, client, monkeypatch):
        """DB failure during trace_id lookup → 503."""
        async def fake_trace_ids(conv_id, run_sync):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(cs_admin, "_async_get_trace_ids", fake_trace_ids)
        resp = client.get("/cs/conversations/conv-001/traces")
        assert resp.status_code == 503


class TestMyConversations:
    """GET /cs/conversations/my — 用户侧恢复历史（按登录身份过滤）。"""

    @staticmethod
    def _fake_identity(user_id):
        from backend.app.api.identity import Identity

        return Identity(
            user_id=user_id,
            auth_type="jwt" if user_id else "guest",
            source="header" if user_id else "guest",
        )

    def test_guest_returns_401(self, client, monkeypatch):
        """未认证（guest）一律 401，不触发 DB。"""
        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._fake_identity(""),
        )
        resp = client.get("/cs/conversations/my")
        assert resp.status_code == 401

    def test_returns_own_items(self, client, monkeypatch):
        """认证用户 → 返回自己的会话（含消息），user_id/limit 透传 helper。"""
        captured = {}

        async def fake_my(*, user_id, limit):
            captured["user_id"] = user_id
            captured["limit"] = limit
            return cs_admin.MyConversationsResponse(items=[
                cs_admin.MyConversationItem(
                    conversation_id="conv-9",
                    summary="转人工",
                    conversation_status="open",
                    handling_mode="human",
                    created_at="2026-09-17T05:00:00+00:00",
                    last_activity_at="2026-09-17T05:30:00+00:00",
                    messages=[
                        cs_admin.MyConversationMessage(
                            message_id="m1", sender_type="user",
                            content="我想转接人工客服",
                            created_at="2026-09-17T05:00:00+00:00",
                        ),
                        cs_admin.MyConversationMessage(
                            message_id="m2", sender_type="human_agent",
                            content="您好，我是人工坐席",
                            created_at="2026-09-17T05:30:00+00:00",
                        ),
                    ],
                ),
            ])

        monkeypatch.setattr(cs_admin, "_async_my_conversations", fake_my)
        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._fake_identity("9"),
        )
        resp = client.get("/cs/conversations/my?limit=5")
        assert resp.status_code == 200
        assert captured == {"user_id": "9", "limit": 5}
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["conversation_id"] == "conv-9"
        assert items[0]["messages"][1]["sender_type"] == "human_agent"

    def test_db_error_returns_503(self, client, monkeypatch):
        """DB 故障 → 503（与其它读端点语义一致）。"""
        async def fake_my(**kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(cs_admin, "_async_my_conversations", fake_my)
        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._fake_identity("9"),
        )
        resp = client.get("/cs/conversations/my")
        assert resp.status_code == 503


class TestResponseModels:

    def test_message_dto_defaults(self):
        """MessageDTO has sensible defaults."""
        msg = cs_admin.MessageDTO(
            message_id="m1", sender_type="user", content="hi", created_at="2026-09-04T10:00:00",
        )
        assert msg.content_type == "text"
        assert msg.intent_domain is None
        assert msg.trace_id is None

    def test_conversation_summary_defaults(self):
        """ConversationSummary has sensible defaults."""
        s = cs_admin.ConversationSummary(
            conversation_id="c1", user_id="u1",
            conversation_status="active", handling_mode="ai",
            priority="normal", created_at="2026-09-04T10:00:00",
        )
        assert s.message_count == 0
        assert s.trace_count == 0
        assert s.last_trace_id is None
        assert s.summary is None

    def test_paginated_model(self):
        """PaginatedConversations serializes correctly."""
        p = cs_admin.PaginatedConversations(items=[], total=0, has_more=False)
        d = p.model_dump()
        assert d == {"items": [], "total": 0, "has_more": False}


class TestMyMessages:
    """GET /cs/conversations/my/{id}/messages — P3.4 用户侧端点拆分。

    与坐席端差异：登录态强制（401 拒 guest）+ 本人会话精确匹配（403 他人）。
    """

    @staticmethod
    def _identity(user_id):
        from backend.app.api.identity import Identity

        return Identity(
            user_id=user_id,
            auth_type="jwt" if user_id else "guest",
            source="header" if user_id else "guest",
        )

    @staticmethod
    def _patch_db(monkeypatch, owner_user_id):
        """桩掉归属查询：AsyncSessionLocal 返回固定 owner 的假会话。"""

        class _Result:
            def scalar_one_or_none(self):
                return owner_user_id

        class _FakeDB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, q):
                return _Result()

        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal", lambda: _FakeDB()
        )

    def test_guest_401(self, client, monkeypatch):
        """未认证一律 401，不触 DB。"""
        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._identity(""),
        )
        resp = client.get("/cs/conversations/my/conv-1/messages")
        assert resp.status_code == 401

    def test_other_users_conversation_403(self, client, monkeypatch):
        """登录用户只能拉自己的会话（owner 不匹配 → 403）。"""
        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._identity("user-A"),
        )
        self._patch_db(monkeypatch, owner_user_id="user-B")
        resp = client.get("/cs/conversations/my/conv-1/messages")
        assert resp.status_code == 403

    def test_own_conversation_returns_messages(self, client, monkeypatch):
        """本人会话 → 透传 _async_messages_since（since_id/limit）。"""
        captured = {}

        async def fake_since(conversation_id, since_id, limit, run_sync):
            captured.update(
                conversation_id=conversation_id, since_id=since_id, limit=limit
            )
            return cs_admin.HandoffMessagesResponse(
                conversation_id=conversation_id,
                handoff_state="none",
                last_id=3,
                messages=[],
            )

        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._identity("user-A"),
        )
        self._patch_db(monkeypatch, owner_user_id="user-A")
        monkeypatch.setattr(cs_admin, "_async_messages_since", fake_since)

        resp = client.get("/cs/conversations/my/conv-1/messages?since_id=3&limit=50")
        assert resp.status_code == 200
        assert captured == {
            "conversation_id": "conv-1", "since_id": 3, "limit": 50,
        }

    def test_ownership_check_db_error_503(self, client, monkeypatch):
        """归属查询 DB 故障 → 503。"""
        monkeypatch.setattr(
            "backend.app.api.identity.resolve_identity",
            lambda req: self._identity("user-A"),
        )

        class _BrokenDB:
            async def __aenter__(self):
                raise RuntimeError("connection refused")

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal", lambda: _BrokenDB()
        )
        resp = client.get("/cs/conversations/my/conv-1/messages")
        assert resp.status_code == 503
