# -*- coding: utf-8 -*-
"""管理端表浏览通道（GET /sql/tables*）权限与行为矩阵（2026-10-01）。

真实链：TestClient → auth 中间件 → sql 路由 → get_principal（override 注入
可信 Principal）→ build_authorization_context → SQLPolicyContext
→ SQLPolicyGuard（真实，表域/scope 注入）→ executor（mock 边界，捕获 SQL
断言）。与 test_sql_http_auth.py 同构——浏览端点不因"服务端拼装 SQL"
而放宽安全语义。
"""
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


def _patch_browse_executor(monkeypatch, calls: dict):
    """mock executor 边界：count 返回总数、rows 返回固定行；捕获 SQL 文本。

    Guard/validator/审计链路全部真实。
    """
    import backend.sql.executor as exec_mod

    def _fake_struct(sql, db_config=None, params=None, timeout=None):
        calls["sqls"].append(sql)
        calls["executor"] += 1
        if "COUNT(*)" in sql:
            return SQLResult.success(
                [{"row_total": 42}], columns=["row_total"], sql=sql, elapsed=0.01)
        return SQLResult.success(
            [{"id": 1, "sku": "SKU-001"}], columns=["id", "sku"],
            sql=sql, elapsed=0.01)

    monkeypatch.setattr(exec_mod, "execute_sql_struct", _fake_struct)


def _override(client, roles):
    import backend.app.api.routes.sql as sql_route

    client.app.dependency_overrides[sql_route.get_principal] = \
        lambda: _principal(roles=roles)


class TestBrowsePermissionMatrix:
    def test_viewer_denied_403_executor_never_called(self, client, monkeypatch):
        """viewer 无 sql.read → 403；executor/审计执行零发生。"""
        calls = {"sqls": [], "executor": 0}
        _patch_browse_executor(monkeypatch, calls)
        _override(client, ("viewer",))
        try:
            resp = client.get("/sql/tables/product/products",
                              headers={"X-API-Key": "test"})
            assert resp.status_code == 403, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_editor_shared_table_200_count_plus_rows(self, client, monkeypatch):
        """editor(department) 浏览 shared 表 → 200；count+rows 两条 SQL
        均过 Guard 并执行；分页字段回传。"""
        calls = {"sqls": [], "executor": 0}
        _patch_browse_executor(monkeypatch, calls)
        _override(client, ("editor",))
        try:
            resp = client.get("/sql/tables/product/products?page=2&page_size=10",
                              headers={"X-API-Key": "test"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "success"
            assert body["qualified_name"] == "product.products"
            assert body["total"] == 42
            assert body["page"] == 2 and body["page_size"] == 10
            assert calls["executor"] == 2  # count + rows
            count_sql, rows_sql = calls["sqls"]
            assert "COUNT(*)" in count_sql and "product.products" in count_sql
            assert "LIMIT 10" in rows_sql and "OFFSET 10" in rows_sql
            # sqlglot 渲染关键字大写，语义匹配不做大小写要求
            assert "ORDER BY ID ASC" in rows_sql.upper()
        finally:
            client.app.dependency_overrides.clear()

    def test_editor_internal_table_denied_executor_zero(self, client, monkeypatch):
        """editor(department) 浏览 internal 表 finance.expenses → 403
        （表域判定，SQL_TABLE_NOT_ALLOWED 语义）；executor 零调用。"""
        calls = {"sqls": [], "executor": 0}
        _patch_browse_executor(monkeypatch, calls)
        _override(client, ("editor",))
        try:
            resp = client.get("/sql/tables/finance/expenses",
                              headers={"X-API-Key": "test"})
            assert resp.status_code == 403, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_super_admin_internal_table_allowed(self, client, monkeypatch):
        """super_admin(data_scope=all) 浏览 internal 表 → 200
        （internal 仅 all 可读；同时回归 super_admin 权限映射补登记）。"""
        calls = {"sqls": [], "executor": 0}
        _patch_browse_executor(monkeypatch, calls)
        _override(client, ("super_admin",))
        try:
            resp = client.get("/sql/tables/finance/expenses",
                              headers={"X-API-Key": "test"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "success"
            assert calls["executor"] == 2
        finally:
            client.app.dependency_overrides.clear()

    def test_unknown_table_404(self, client, monkeypatch):
        """白名单外表 → 404；executor 零调用（不进 Guard/DB）。"""
        calls = {"sqls": [], "executor": 0}
        _patch_browse_executor(monkeypatch, calls)
        _override(client, ("super_admin",))
        try:
            resp = client.get("/sql/tables/public/users",
                              headers={"X-API-Key": "test"})
            assert resp.status_code == 404, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()

    def test_sort_column_not_browsable_400(self, client, monkeypatch):
        """排序列不在可见列 → 400（id 之外的白名单列不存在于该表）。"""
        calls = {"sqls": [], "executor": 0}
        _patch_browse_executor(monkeypatch, calls)
        _override(client, ("super_admin",))
        try:
            resp = client.get(
                "/sql/tables/product/products?sort=category_id;DROP TABLE x",
                headers={"X-API-Key": "test"})
            assert resp.status_code == 400, resp.text
            assert calls["executor"] == 0
        finally:
            client.app.dependency_overrides.clear()


class TestBrowseKillSwitchAndValidation:
    def test_kill_switch_off_503(self, client, monkeypatch):
        """SQL_AGENT_ENABLED=false → 503，目录与浏览一致。"""
        import backend.config as config_mod
        monkeypatch.setattr(config_mod, "SQL_AGENT_ENABLED", False)
        _override(client, ("super_admin",))
        try:
            resp = client.get("/sql/tables", headers={"X-API-Key": "test"})
            assert resp.status_code == 503, resp.text
            resp2 = client.get("/sql/tables/product/products",
                               headers={"X-API-Key": "test"})
            assert resp2.status_code == 503, resp2.text
        finally:
            client.app.dependency_overrides.clear()

    def test_page_size_over_max_422(self, client, monkeypatch):
        """page_size 上限 100（与 validator max_limit 对齐）→ 422。"""
        _override(client, ("super_admin",))
        try:
            resp = client.get("/sql/tables/product/products?page_size=101",
                              headers={"X-API-Key": "test"})
            assert resp.status_code == 422, resp.text
        finally:
            client.app.dependency_overrides.clear()

    def test_catalog_lists_all_whitelisted_tables(self, client, monkeypatch):
        """表目录 = schema_loader 白名单全量 18 表；敏感列剔除语义与
        get_table_info 一致（get_browse_columns 单一实现）。"""
        _override(client, ("editor",))
        try:
            resp = client.get("/sql/tables", headers={"X-API-Key": "test"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            from backend.sql.schema_loader import schema_loader
            qualified = {t["qualified_name"] for t in body["tables"]}
            assert qualified == set(schema_loader.get_all_table_names())
            # 描述元数据出口（供核对答案用）
            products = next(t for t in body["tables"]
                            if t["qualified_name"] == "product.products")
            assert products["description"]
            assert {c["name"] for c in products["columns"]} >= {"id", "sku"}
        finally:
            client.app.dependency_overrides.clear()
