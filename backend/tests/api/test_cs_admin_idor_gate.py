"""STOP CS-A P0-1 回归：CS Admin IDOR 权限闸端点级测试。

审计（2026-10-06 §十三）实测 viewer 可 200 读取全库列表/stats/他人会话
详情/trace 关联 —— 本文件锁定修复后的权限矩阵：

    viewer A → 自己 conversation        200
    viewer A → viewer B conversation    403
    viewer A → B traces                 403
    viewer   → 全库 list                403
    viewer   → global stats             403
    assigned agent → assigned conv      200
    unassigned agent → conv             403
    admin → conversation                200
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.app.api.routes import cs_admin


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cs_admin.router)
    return TestClient(app, raise_server_exceptions=False)


def _identity(user_id: str, roles=("viewer",), tenant_id: str = "tenant-a"):
    from backend.app.api.identity import Identity

    return Identity(
        user_id=user_id, user_name=user_id, auth_type="jwt",
        source="header", roles=tuple(roles), tenant_id=tenant_id,
    )


def _patch_identity(monkeypatch, ident):
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity", lambda req: ident,
    )


def _conv_db(user_id="user-a", tenant_id="tenant-a", assigned=None):
    """会话归属行桩（AsyncSessionLocal.first()）。"""

    class _Result:
        def first(self):
            return SimpleNamespace(
                user_id=user_id, tenant_id=tenant_id,
                assigned_agent_id=assigned,
            )

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, _q):
            return _Result()

    return _DB()


def _stub_detail(monkeypatch, conv_id="conv-own"):
    async def fake_get(cid, run_sync):
        return cs_admin.ConversationDetail(
            conversation_id=cid, user_id="user-a",
            conversation_status="open", handling_mode="ai",
            priority="medium", channel="web",
            created_at="2026-10-07T00:00:00+00:00", messages=[],
        )

    monkeypatch.setattr(cs_admin, "_async_get_conversation", fake_get)


def _stub_traces(monkeypatch):
    async def fake_ids(cid, run_sync):
        return ["trace-1"]

    monkeypatch.setattr(cs_admin, "_async_get_trace_ids", fake_ids)
    monkeypatch.setattr(
        "backend.observability.trace_store.get_trace_store",
        lambda: SimpleNamespace(get=lambda tid: None),
    )


class TestViewerMatrix:
    """普通用户（viewer，无客服域角色）。"""

    def test_viewer_list_403(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("user-a"))
        resp = client.get("/cs/conversations?limit=5")
        assert resp.status_code == 403

    def test_viewer_global_stats_403(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("user-a"))
        resp = client.get("/cs/conversations/stats")
        assert resp.status_code == 403

    def test_viewer_own_conversation_200(self, client, monkeypatch):
        """owner 读自己的会话：闸外放行（_ensure_conversation_access owner 分支）。"""
        _patch_identity(monkeypatch, _identity("user-a"))
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(user_id="user-a"),
        )
        _stub_detail(monkeypatch)
        resp = client.get("/cs/conversations/conv-own")
        assert resp.status_code == 200
        assert resp.json()["conversation_id"] == "conv-own"

    def test_viewer_other_conversation_403(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("user-a"))
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(user_id="user-b"),
        )
        resp = client.get("/cs/conversations/conv-b")
        assert resp.status_code == 403

    def test_viewer_other_tenant_403(self, client, monkeypatch):
        """同 user_id 不同租户：双因子校验拒绝（P0-2 口径）。"""
        _patch_identity(monkeypatch, _identity("user-a", tenant_id="tenant-a"))
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(user_id="user-a", tenant_id="tenant-b"),
        )
        resp = client.get("/cs/conversations/conv-x")
        assert resp.status_code == 403

    def test_viewer_other_traces_403(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("user-a"))
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(user_id="user-b"),
        )
        resp = client.get("/cs/conversations/conv-b/traces")
        assert resp.status_code == 403

    def test_viewer_own_traces_200(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("user-a"))
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(user_id="user-a"),
        )
        _stub_traces(monkeypatch)
        resp = client.get("/cs/conversations/conv-own/traces")
        assert resp.status_code == 200


class TestAgentMatrix:
    """坐席只放行「当前被分配」的会话（复用 _ensure_conversation_access）。"""

    def _patch_bound_agent(self, monkeypatch, agent_id):
        async def fake_lookup(*, tenant_id, user_id):
            return agent_id

        monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", fake_lookup)

    def test_assigned_agent_200(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("seat-1", roles=("agent",)))
        self._patch_bound_agent(monkeypatch, "agent-1")
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(assigned="agent-1"),
        )
        _stub_detail(monkeypatch)
        resp = client.get("/cs/conversations/conv-1")
        assert resp.status_code == 200

    def test_unassigned_agent_403(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("seat-2", roles=("agent",)))
        self._patch_bound_agent(monkeypatch, "agent-2")
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(assigned="agent-1"),
        )
        resp = client.get("/cs/conversations/conv-1")
        assert resp.status_code == 403


class TestAdminAndService:

    def test_admin_other_conversation_200(self, client, monkeypatch):
        _patch_identity(monkeypatch, _identity("op-1", roles=("admin",)))
        monkeypatch.setattr(
            "backend.memory.database.AsyncSessionLocal",
            lambda: _conv_db(user_id="user-b"),
        )
        _stub_detail(monkeypatch)
        resp = client.get("/cs/conversations/conv-1")
        assert resp.status_code == 200

    def test_service_channel_list_ok(self, client, monkeypatch):
        """服务间 api-key 通道：operator 闸放行（跨租户服务视图，tenant=None）。"""
        called = {}

        async def fake_list(**kwargs):
            called.update(kwargs)
            return cs_admin.PaginatedConversations(items=[], total=0, has_more=False)

        monkeypatch.setattr(cs_admin, "_async_list_conversations", fake_list)
        resp = client.get(
            "/cs/conversations?limit=1", headers={"X-Auth-Type": "api-key"},
        )
        assert resp.status_code == 200
        assert called["tenant_id"] is None  # 服务通道=跨租户视图

    def test_supervisor_list_403_without_tenant(self, client, monkeypatch):
        """JWT 通道缺租户 → fail-closed 403（不回落 default）。"""
        _patch_identity(
            monkeypatch,
            _identity("sup-1", roles=("supervisor",), tenant_id=""),
        )
        resp = client.get("/cs/conversations?limit=1")
        assert resp.status_code == 403
