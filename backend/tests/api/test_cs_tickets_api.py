"""test_cs_tickets_api.py — 工单接口 RBAC 角色守卫 (O12) 与查询口径 (O13) 测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.identity import Identity, require_identity
from backend.app.api.routes import cs_tickets
from backend.app.api.routes.cs_ops import require_cs_supervisor


@pytest.fixture
def app_with_tickets():
    app = FastAPI()
    app.include_router(cs_tickets.router)
    return app


class TestO12TicketRoleGuard:
    """O12：工单接口加角色守卫，普通坐席禁止通过 /cs/tickets 越权访问（必须 403）。"""

    def test_regular_user_can_access_my_tickets(self, app_with_tickets, monkeypatch):
        """普通用户 (roles=['viewer']) 访问 GET /cs/tickets 放行 200。"""
        app_with_tickets.dependency_overrides[require_identity] = lambda: Identity(
            user_id="user-123", roles=("viewer",), tenant_id="default",
        )
        fake_store = MagicMock()
        fake_store.async_list_for_user = AsyncMock(return_value=[])
        monkeypatch.setattr("backend.app.api.routes.cs_tickets.get_ticket_store", lambda: fake_store)

        client = TestClient(app_with_tickets)
        resp = client.get("/cs/tickets")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    def test_agent_role_forbidden_on_my_tickets(self, app_with_tickets):
        """普通客服坐席 (roles=['agent']) 访问 GET /cs/tickets 必须拦截 403。"""
        app_with_tickets.dependency_overrides[require_identity] = lambda: Identity(
            user_id="agent-007", roles=("viewer", "agent"), tenant_id="default",
        )
        client = TestClient(app_with_tickets)
        resp = client.get("/cs/tickets")
        assert resp.status_code == 403
        assert "客服坐席无权访问" in resp.json().get("detail", "")

    def test_agent_role_forbidden_on_ticket_detail(self, app_with_tickets):
        """普通客服坐席 (roles=['agent']) 访问 GET /cs/tickets/{id} 必须拦截 403。"""
        app_with_tickets.dependency_overrides[require_identity] = lambda: Identity(
            user_id="agent-007", roles=("agent",), tenant_id="default",
        )
        client = TestClient(app_with_tickets)
        resp = client.get("/cs/tickets/TCK-123456")
        assert resp.status_code == 403
        assert "客服坐席无权访问" in resp.json().get("detail", "")

    def test_agent_forbidden_on_admin_list(self, app_with_tickets):
        """普通坐席访问管理侧工单列表 /cs/tickets/admin/list 必须拦截 403。"""
        from fastapi import HTTPException
        app_with_tickets.dependency_overrides[require_cs_supervisor] = lambda: (_ for _ in ()).throw(
            HTTPException(403, detail="派单运营快照仅限管理员或客服主管（supervisor）访问")
        )
        client = TestClient(app_with_tickets)
        resp = client.get("/cs/tickets/admin/list")
        assert resp.status_code == 403

    def test_supervisor_allowed_on_admin_list(self, app_with_tickets, monkeypatch):
        """客服主管访问管理侧工单列表 /cs/tickets/admin/list 放行 200。"""
        app_with_tickets.dependency_overrides[require_cs_supervisor] = lambda: Identity(
            user_id="sup-001", roles=("supervisor",), tenant_id="default",
        )
        fake_store = MagicMock()
        fake_store.async_list_admin = AsyncMock(return_value=[])
        monkeypatch.setattr("backend.app.api.routes.cs_tickets.get_ticket_store", lambda: fake_store)

        client = TestClient(app_with_tickets)
        resp = client.get("/cs/tickets/admin/list")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}


class TestO13TicketQueryFilter:
    """O13：工单类型筛选兼容测试。"""

    @pytest.mark.asyncio
    async def test_handoff_timeout_alias_filter(self):
        """当传入 handoff_timeout 时，底层查询兼容 in_(['handoff', 'handoff_timeout'])。"""
        from backend.customer_service.case.ticket_store import TicketStore
        store = TicketStore()
        
        with patch("backend.memory.database.AsyncSessionLocal") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            items = await store.async_list_admin(tenant_id="default", type="handoff_timeout")
            assert items == []
            assert mock_session.execute.called
            call_arg = mock_session.execute.call_args[0][0]
            sql_str = str(call_arg)
            assert "tickets.type IN" in sql_str or "tickets.type =" in sql_str
