# -*- coding: utf-8 -*-
"""STOP C HTTP 通道权限矩阵（规格 §八 Case HTTP-01~08 + §四十四 header spoof 单元级）。

真实链：TestClient → auth 中间件 → sql 路由 → get_principal（override 注入
可信 Principal）→ build_authorization_context → SQLPolicyContext → SQLAgent
策略链 → SQLPolicyGuard（真实）→ executor（mock 边界，计数断言）。

身份只能经 Principal 进入（生产为网关验签头推导）；请求头/请求体伪造的
角色/scope 字段不得影响授权判定。
"""
from unittest.mock import patch as mp

import pytest
from fastapi.testclient import TestClient

from backend.security.principal import Principal
from backend.sql.sql_result import SQLResult


def _principal(roles=("editor",), user_id="3", department="hr") -> Principal:
    return Principal(
        user_id=user_id, user_name="", tenant_id="", department=department,
        roles=tuple(roles), permissions=None,
        subject_type="employee", authenticated=bool(roles),
        auth_type="gateway", source="header",
    )


@pytest.fixture()
def client(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw

    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    from backend.app.server import app
    return TestClient(app, raise_server_exceptions=False)


def _patch_sql_backend(monkeypatch, calls: dict):
    """mock agent 的 LLM 生成与 executor 边界；select_tables/validator/Guard 真实。"""
    import backend.sql.sql_agent as agent_mod

    def _fake_generate(question, tables, feedback=None):
        calls["generate"] += 1
        return calls.get("sql") or "SELECT 1 AS ok"

    def _fake_executor(sql, db_config=None, params=None, timeout=None):
        calls["executor"] += 1
        return SQLResult.success([{"ok": 1}], columns=["ok"], sql=sql,
                                 elapsed=0.01)

    monkeypatch.setattr(agent_mod, "generate_sql", _fake_generate)
    monkeypatch.setattr(agent_mod, "execute_sql_struct", _fake_executor)


class TestHttpPermissionMatrix:
    def test_http01_viewer_denied_403_executor_never_called(self, client, monkeypatch):
        """viewer 无 sql.read → 403；executor/LLM 零调用（预检在 LLM 之前）。"""
        calls = {"generate": 0, "executor": 0}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("viewer",))
        try:
            resp = client.post("/sql/query", json={"question": "查商品"},
                               headers={"X-API-Key": "test"})
            assert resp.status_code == 403, resp.text
            assert calls["executor"] == 0
            assert calls["generate"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_http02_editor_department_shared_table_allowed(self, client, monkeypatch):
        """editor(department) 查 shared 表 product.products → 200 success。"""
        calls = {"generate": 0, "executor": 0,
                 "sql": "SELECT sku FROM product.products LIMIT 5"}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("editor",))
        try:
            resp = client.post(
                "/sql/query",
                json={"question": "列出几个商品 sku"},
                headers={"X-API-Key": "test"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "success", body
            assert calls["executor"] == 1
        finally:
            client.app.dependency_overrides.clear()

    def test_http03_editor_finance_denied_executor_zero(self, client, monkeypatch):
        """editor 查 finance.expenses → SQL_TABLE_NOT_ALLOWED 语义，
        permission_denied；executor 零调用。"""
        calls = {"generate": 0, "executor": 0,
                 "sql": "SELECT amount FROM finance.expenses LIMIT 5"}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("editor",))
        try:
            resp = client.post("/sql/query",
                               json={"question": "查费用"},
                               headers={"X-API-Key": "test"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "permission_denied", body
            assert body["error_type"] == "row_security"
            assert "finance" not in (body.get("error") or "")
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_http04_admin_finance_allowed(self, client, monkeypatch):
        """admin(all) 查 finance.expenses → success。"""
        calls = {"generate": 0, "executor": 0,
                 "sql": "SELECT amount FROM finance.expenses LIMIT 5"}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("admin",))
        try:
            resp = client.post("/sql/query",
                               json={"question": "查费用"},
                               headers={"X-API-Key": "test"})
            body = resp.json()
            assert resp.status_code == 200, resp.text
            assert body["status"] == "success", body
            assert calls["executor"] == 1
        finally:
            client.app.dependency_overrides.clear()

    def test_http05_admin_ai_internal_allowed(self, client, monkeypatch):
        """admin(all) 查 ai.agent_tasks（internal 域）→ success。"""
        calls = {"generate": 0, "executor": 0,
                 "sql": "SELECT id FROM ai.agent_tasks LIMIT 5"}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("admin",))
        try:
            resp = client.post("/sql/query",
                               json={"question": "查 agent 任务"},
                               headers={"X-API-Key": "test"})
            body = resp.json()
            assert body["status"] == "success", body
            assert calls["executor"] == 1
        finally:
            client.app.dependency_overrides.clear()

    def test_http06_unknown_role_denied(self, client, monkeypatch):
        """未知角色 → 权限码为空 → 403（fail-closed）。"""
        calls = {"generate": 0, "executor": 0}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("superuser",))
        try:
            resp = client.post("/sql/query", json={"question": "查商品"},
                               headers={"X-API-Key": "test"})
            assert resp.status_code == 403, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_http07_unauthenticated_denied(self, client, monkeypatch):
        """身份不存在（guest/匿名 Principal）→ 403。"""
        calls = {"generate": 0, "executor": 0}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=(), user_id="")
        try:
            resp = client.post("/sql/query", json={"question": "查商品"},
                               headers={"X-API-Key": "test"})
            assert resp.status_code == 403, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_http08_forged_scope_header_cannot_inject(self, client, monkeypatch):
        """客户端伪造 X-Data-Scope / X-Roles 头：授权仍由 override 的可信
        Principal 决定（生产=网关验签头）；editor 无法经头注入 all/admin。
        viewer 即使带 X-Roles: admin 头依旧 403。"""
        calls = {"generate": 0, "executor": 0}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("viewer",))
        try:
            resp = client.post(
                "/sql/query",
                json={"question": "查商品"},
                headers={"X-API-Key": "test",
                         "X-Roles": "admin", "X-Data-Scope": "all",
                         "X-User-Id": "1"})
            assert resp.status_code == 403, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_kill_switch_returns_503(self, client, monkeypatch):
        """SQL_AGENT_ENABLED=false → 503 服务不可用（非权限语义）；
        agent 与 executor 均不触达。"""
        calls = {"generate": 0, "executor": 0}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        monkeypatch.setattr("backend.config.SQL_AGENT_ENABLED", False)
        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("admin",))
        try:
            resp = client.post("/sql/query", json={"question": "查商品"},
                               headers={"X-API-Key": "test"})
            assert resp.status_code == 503, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_policy_context_is_built_on_http_channel(self, client, monkeypatch):
        """HTTP 通道 policy 非 None 且 source_channel="http"（规格 §十六）。"""
        import backend.sql.sql_agent as agent_mod

        seen = {}
        real = agent_mod.SQLAgent.ask_struct

        def spy(self, question, current_user_id=None, policy=None):
            seen["policy"] = policy
            return real(self, question, current_user_id=current_user_id,
                        policy=policy)

        monkeypatch.setattr(agent_mod.SQLAgent, "ask_struct", spy)
        calls = {"generate": 0, "executor": 0,
                 "sql": "SELECT sku FROM product.products LIMIT 5"}
        _patch_sql_backend(monkeypatch, calls)
        import backend.app.api.routes.sql as sql_route

        client.app.dependency_overrides[sql_route.get_principal] = \
            lambda: _principal(roles=("editor",))
        try:
            resp = client.post("/sql/query", json={"question": "查商品"},
                               headers={"X-API-Key": "test"})
            assert resp.status_code == 200, resp.text
            assert seen["policy"] is not None
            assert seen["policy"].source_channel == "http"
        finally:
            client.app.dependency_overrides.clear()
