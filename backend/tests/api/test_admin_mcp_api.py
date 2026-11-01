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
    def test_overview_rejects_missing_identity(self, client, monkeypatch):
        # 44d2e53 起 internal token 未配置时 fail-closed 503 挡在身份层
        # 之前——显式豁免（仅测试进程）放行无头请求，锁定 403 语义。
        from backend.app.api import deps
        monkeypatch.setattr(deps, "ALLOW_UNAUTHENTICATED", True)
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

    def test_connection_detail_rejects_anonymous(self, client, monkeypatch):
        from backend.app.api import deps
        monkeypatch.setattr(deps, "ALLOW_UNAUTHENTICATED", True)
        r = client.get("/admin/mcp/connections/mcp_conn_x")
        assert r.status_code == 403

    def test_grant_rejects_viewer(self, client):
        r = client.post("/admin/mcp/tools/1/grants", headers=VIEWER_HEADERS,
                        json={"product_key": "agent"})
        assert r.status_code == 403

    def test_test_invoke_rejects_anonymous(self, client, monkeypatch):
        from backend.app.api import deps
        monkeypatch.setattr(deps, "ALLOW_UNAUTHENTICATED", True)
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


class TestEgressAllowlist:
    """G3 出口白名单（MCP_EGRESS_ALLOWLIST）：连接声明面 host 收窄。

    拒绝路径在查库之前返回 400（与 SSRF 闸同模式，无需 DB）；
    放行路径在函数级断言（命中即不抛）。
    """

    ALLOWLIST = "mcp.example.com,*.trusted.io"

    def test_create_rejects_unapproved_host(self, client, monkeypatch):
        # 公网 IP 字面量过 SSRF（字面 IP 直接判定网段、无 DNS 依赖），
        # 由 allowlist 拒绝——专测本层而非 SSRF。
        monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", self.ALLOWLIST)
        r = client.post(
            "/admin/mcp/connections", headers=ADMIN_HEADERS,
            json={"name": "shadow", "base_url": "https://8.8.8.8/mcp",
                  "transport": "streamable_http", "auth_type": "none",
                  "network_scope": "public"})
        assert r.status_code == 400
        assert "出口白名单" in r.json()["message"]

    def test_update_rejects_unapproved_host(self, client, monkeypatch):
        from datetime import datetime

        monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", self.ALLOWLIST)
        monkeypatch.setattr(
            admin_mcp, "_load_connection",
            lambda cid, tenant: {
                "id": cid, "name": "conn", "base_url": "https://mcp.example.com/mcp",
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
            json={"base_url": "https://8.8.8.8/mcp"})
        assert r.status_code == 400
        assert "出口白名单" in r.json()["message"]

    def test_scope_only_update_skips_allowlist(self, client, monkeypatch):
        """仅改 network_scope（URL 为存量值）不触发白名单——存量连接
        不被新白名单追溯（语义见 _validate_egress_allowlist 注释）。"""
        from datetime import datetime

        monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", self.ALLOWLIST)
        monkeypatch.setattr(
            admin_mcp, "_load_connection",
            lambda cid, tenant: {
                "id": cid, "name": "conn", "base_url": "https://legacy.host.io/mcp",
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
            json={"network_scope": "private"})
        # 校验放行后进入写路径（无 DB → 500 桩），但绝不能是白名单 400
        assert r.status_code != 400 or "出口白名单" not in r.json().get("message", "")

    def test_validate_function_exact_wildcard_case_insensitive(self, monkeypatch):
        from backend.app.api.routes.admin_mcp import _validate_egress_allowlist

        monkeypatch.setenv("MCP_EGRESS_ALLOWLIST", self.ALLOWLIST)
        # 命中：精确 / 后缀通配 / 大小写不敏感 —— 全部不抛
        _validate_egress_allowlist("https://mcp.example.com/mcp")
        _validate_egress_allowlist("https://api.trusted.io/mcp")
        _validate_egress_allowlist("https://MCP.EXAMPLE.COM/mcp")
        # 仅后缀欺骗（evil-example.com ≠ *.example.com）必须拒绝
        with pytest.raises(ValueError, match="出口白名单"):
            _validate_egress_allowlist("https://evil-example.com/mcp")
        # 通配不等于裸域（*.trusted.io 不含 trusted.io 本身）
        with pytest.raises(ValueError, match="出口白名单"):
            _validate_egress_allowlist("https://trusted.io/mcp")

    def test_validate_function_noop_when_unset(self, monkeypatch):
        from backend.app.api.routes.admin_mcp import _validate_egress_allowlist

        monkeypatch.delenv("MCP_EGRESS_ALLOWLIST", raising=False)
        # 未配置 = 不启用：任意 host 不抛（历史行为零变化）
        _validate_egress_allowlist("https://anything.example.net/mcp")


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
