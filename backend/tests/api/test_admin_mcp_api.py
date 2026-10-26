"""admin_mcp API 权限门禁与 SSRF 校验测试（不连 DB / 不连 Redis）。

守卫在查库之前执行，故无需真实 PostgreSQL（照 test_gateway_access_logs_api
的权限闸模式）。查询语义与端到端链路见 test_mcp_dynamic_e2e.py（-m pg）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import admin_mcp

ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-admin-1",
    "X-User-Roles": "admin",
    "X-Tenant-Id": "tenant_gate_test",
}
VIEWER_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-viewer-1",
    "X-User-Roles": "viewer",
    "X-Tenant-Id": "tenant_gate_test",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(admin_mcp.router)
    return TestClient(app, raise_server_exceptions=False)


class TestPermissionGates:
    def test_overview_rejects_missing_identity(self, client):
        r = client.get("/admin/mcp/overview")
        assert r.status_code == 403

    def test_overview_rejects_viewer(self, client):
        r = client.get("/admin/mcp/overview", headers=VIEWER_HEADERS)
        assert r.status_code == 403

    def test_overview_allows_admin_without_db(self, client):
        """overview 只读进程内快照，admin 身份即可（无 DB 依赖）。"""
        r = client.get("/admin/mcp/overview", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert r.json()["loaded"] is False  # 测试进程未注入快照

    def test_connections_list_rejects_viewer(self, client):
        r = client.get("/admin/mcp/connections", headers=VIEWER_HEADERS)
        assert r.status_code == 403

    def test_connection_detail_rejects_anonymous(self, client):
        r = client.get("/admin/mcp/connections/mcp_conn_x")
        assert r.status_code == 403

    def test_grant_rejects_viewer(self, client):
        r = client.post("/admin/mcp/tools/1/grants", headers=VIEWER_HEADERS,
                        json={"product_key": "agent"})
        assert r.status_code == 403

    def test_test_invoke_rejects_anonymous(self, client):
        r = client.post("/admin/mcp/tools/1/test-invoke",
                        json={"arguments": {}})
        assert r.status_code == 403


class TestSsrfGuard:
    def test_create_connection_blocks_private_url_in_public_scope(self, client):
        """公网 scope 下的环回/内网地址必须在入库前被拒绝（400）。"""
        r = client.post(
            "/admin/mcp/connections", headers=ADMIN_HEADERS,
            json={"name": "evil-loopback", "base_url": "http://127.0.0.1:8000/mcp",
                  "auth_type": "none", "network_scope": "public"})
        assert r.status_code == 400
        assert "SSRF" in r.json()["message"]

    def test_create_connection_blocks_metadata_url(self, client):
        r = client.post(
            "/admin/mcp/connections", headers=ADMIN_HEADERS,
            json={"name": "cloud-meta", "base_url": "http://169.254.169.254/mcp",
                  "auth_type": "none", "network_scope": "public"})
        assert r.status_code == 400

    def test_create_connection_blocks_private_range(self, client):
        r = client.post(
            "/admin/mcp/connections", headers=ADMIN_HEADERS,
            json={"name": "intranet", "base_url": "http://10.0.0.5:9000/mcp",
                  "auth_type": "none", "network_scope": "public"})
        assert r.status_code == 400

    def test_create_connection_rejects_non_http_scheme(self, client):
        r = client.post(
            "/admin/mcp/connections", headers=ADMIN_HEADERS,
            json={"name": "file-probe", "base_url": "file:///etc/passwd",
                  "auth_type": "none", "network_scope": "public"})
        assert r.status_code == 400

    def test_update_connection_validates_new_url(self, client, monkeypatch):
        """更新时的新 URL 同样过 SSRF 闸（先查资源存在，再校验新值）。"""
        from datetime import datetime

        monkeypatch.setattr(
            admin_mcp, "_load_connection",
            lambda cid, tenant: {
                "id": cid, "name": "conn", "base_url": "https://ok.example.com/mcp",
                "transport": "streamable_http", "auth_type": "none",
                "network_scope": "public", "enabled": True,
                "status": "connected", "last_error": "",
                "last_synced_at": datetime.now(), "discovery_version": 1,
                "tenant_id": tenant, "created_by": "t", "updated_by": "t",
                "updated_at": datetime.now(),
                "secret_fingerprint": None, "secret_last4": None,
                "secret_version": None,
            })
        r = client.put(
            "/admin/mcp/connections/mcp_conn_x", headers=ADMIN_HEADERS,
            json={"base_url": "http://192.168.1.1:8000/mcp",
                  "network_scope": "public"})
        assert r.status_code == 400


class TestInputValidation:
    def test_grant_rejects_unknown_product(self, client):
        r = client.post("/admin/mcp/tools/1/grants", headers=ADMIN_HEADERS,
                        json={"product_key": "super_admin_product"})
        assert r.status_code == 422  # pydantic Literal 拒绝

    def test_review_rejects_bad_action(self, client):
        r = client.post("/admin/mcp/tools/1/review", headers=ADMIN_HEADERS,
                        json={"action": "maybe"})
        assert r.status_code == 422

    def test_connection_detail_requires_idempotency_free_read(self, client):
        """读端点不带幂等键可用；未命中租户的数据走 404（本测试无 DB，
        连接不存在 → 404 由查库阶段返回；这里只确认不被 403 拦）。"""
        r = client.get("/admin/mcp/connections/mcp_conn_none",
                       headers=ADMIN_HEADERS)
        assert r.status_code in (404, 500)  # 无 DB 时查询失败不伪装成功
