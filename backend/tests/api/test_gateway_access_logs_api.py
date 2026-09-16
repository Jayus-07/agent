"""test_gateway_access_logs_api.py — 网关访问审计明细端点（A3 敏感接口收口）

GET /observability/gateway-access-logs 返回全站访问审计（谁、从哪个 IP、访问了
什么端点、结果如何），属高敏感数据。收口前仅凭服务级 X-API-Key（前端
NEXT_PUBLIC_API_KEY，编译进浏览器 bundle 的公开值）即可匿名读取，任何登录用户
（含 viewer）也能读全部记录。2026-09-16 起收口为**仅 admin**。

本文件只测权限闸——守卫在查库之前执行，故无需真实 PostgreSQL：
  · 非 admin（viewer）→ 403
  · 无身份头（服务 Key 通道）→ 403
  · admin → 放行进入查询阶段（此处不 mock DB，仅断言不再是 403）

窗口/过滤/分页等查询语义依赖真实表，未在此覆盖。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import observability as obs

ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-admin-1",
    "X-User-Roles": "admin",
}
VIEWER_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-viewer-1",
    "X-User-Roles": "viewer",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(obs.router)
    return TestClient(app, raise_server_exceptions=False)


def test_access_logs_rejects_viewer(client):
    r = client.get("/observability/gateway-access-logs?hours=6", headers=VIEWER_HEADERS)
    assert r.status_code == 403


def test_access_logs_rejects_service_key_channel(client):
    """无身份头 = 服务级 API Key 通道（该 Key 下发到浏览器）→ 不得读审计。"""
    r = client.get("/observability/gateway-access-logs?hours=6")
    assert r.status_code == 403


def test_access_logs_audit_mode_allows_viewer(client, monkeypatch):
    """SENSITIVE_API_GUARD_MODE=audit：仅记日志，行为与收口前一致（灰度回退）。

    放行后会进入查询阶段——此处把会话工厂打成"表不存在"，断言走的是
    「数据源不可用」降级（200 + available=false）而非被闸拦（403），
    既不连真实库，也顺带覆盖降级分支。
    """
    from sqlalchemy.exc import ProgrammingError

    class _NoTableSession:
        async def __aenter__(self):
            raise ProgrammingError("SELECT 1", {}, Exception("undefined_table"))

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(obs, "AsyncSessionLocal", lambda: _NoTableSession())
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "audit")

    r = client.get("/observability/gateway-access-logs?hours=6", headers=VIEWER_HEADERS)
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_access_logs_rejects_unknown_role(client):
    """JWT 里 roles 不含 admin（如 editor）同样拒绝。"""
    r = client.get(
        "/observability/gateway-access-logs?hours=6",
        headers={"X-Auth-Type": "jwt", "X-User-Id": "u-2", "X-User-Roles": "editor"},
    )
    assert r.status_code == 403
