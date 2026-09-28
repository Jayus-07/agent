"""本地认证角色兼容性测试。"""

from __future__ import annotations

import pytest
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
