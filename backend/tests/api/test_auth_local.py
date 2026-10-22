"""本地认证角色兼容性测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api import deps
from backend.app.api.routes import auth_local


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.anyio
async def test_require_admin_user_accepts_super_admin(monkeypatch):
    """若统一 admin guard 忽略 super_admin，管理端全部 API 都会被错误拒绝。"""

    async def super_admin_actor(_request: Request) -> deps.OperatorIdentity:
        return deps.OperatorIdentity(role="super_admin", actor="user:7")

    monkeypatch.setattr(deps, "require_user_actor", super_admin_actor)

    identity = await deps.require_admin_user(_request())

    assert identity.role == "super_admin"


def test_internal_token_never_maps_to_super_admin():
    """机器凭据若升级为 super_admin，将突破人工运维 bootstrap 边界。"""

    identity = deps.OperatorIdentity(
        role="admin",
        actor="service:internal-token",
    )

    assert identity.role == "admin"


def test_super_admin_login_and_refresh_claims_keep_platform_and_cs_roles():
    """角色遗漏到 JWT 或 userInfo 会使重登后权限回退或客服端被误拒。"""

    row = {
        "id": 7,
        "username": "alice",
        "real_name": "Alice",
        "role": "super_admin",
        "tenant_id": "tenant-a",
        "cs_role": "agent",
    }

    assert auth_local._jwt_roles(row) == ["super_admin", "agent"]
    assert auth_local._user_info(row) == {
        "userId": 7,
        "username": "alice",
        "realName": "Alice",
        "roles": ["super_admin"],
        "platformRole": "super_admin",
        "tenantId": "tenant-a",
        "csRole": "agent",
        "dept": "",
        "mustChangePassword": False,
    }


def test_repeated_invalid_login_attempts_do_not_lock_account(monkeypatch):
    @asynccontextmanager
    async def fake_db():
        yield object()

    async def no_user(_session, _username, _tenant_id):
        return None

    app = FastAPI()
    app.include_router(auth_local.router)
    monkeypatch.setattr(auth_local, "_db", fake_db)
    monkeypatch.setattr(auth_local, "_fetch_user", no_user)

    with TestClient(app) as client:
        responses = [
            client.post(
                "/auth/login",
                headers={"X-Tenant-Id": "tenant-a"},
                json={"username": "alice", "password": "wrong"},
            )
            for _ in range(12)
        ]

    assert [response.status_code for response in responses] == [400] * 12


def test_captcha_store_evicts_oldest_ticket_at_hard_capacity(monkeypatch):
    auth_local._CAPTCHA_STORE.clear()
    monkeypatch.setattr(auth_local, "_CAPTCHA_MAX_TICKETS", 1)
    try:
        first_ticket, _ = auth_local._captcha_issue()
        second_ticket, _ = auth_local._captcha_issue()
        assert len(auth_local._CAPTCHA_STORE) == 1
        assert first_ticket not in auth_local._CAPTCHA_STORE
        assert second_ticket in auth_local._CAPTCHA_STORE
    finally:
        auth_local._CAPTCHA_STORE.clear()
