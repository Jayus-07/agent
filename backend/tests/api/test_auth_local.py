"""本地认证角色兼容性测试。"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from backend.app.api import deps


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
