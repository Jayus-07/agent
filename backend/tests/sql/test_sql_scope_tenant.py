# -*- coding: utf-8 -*-
"""tenant scope 注入测试（STOP B 决策 D1）。

真实 18 表均无租户列（单租户仓库，结构隔离）；本组用 fixture 表证明
Guard 的 tenant_column 注入能力位：声明即注入、all 也不例外（tenant
永远优先）、tenant_id 缺失 fail-closed。
"""
import pytest

from backend.sql.policy import SQLPolicyGuard, SQLPolicyError, SQL_SCOPE_UNAVAILABLE

from tests.sql.conftest import FIXTURE_TENANT_TABLE, make_ctx


class TestTenantInjection:
    def test_all_scope_still_injects_tenant_predicate(self, fixture_tables):
        """data_scope=all + 声明 tenant_column → 仍注入（tenant 永远优先，
        all ≠ 跨 tenant）。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_TENANT_TABLE}", ctx)
        assert "tenant_id = %(sql_scope_tenant_id)s" in guarded.executable_sql
        assert guarded.params == {"sql_scope_tenant_id": "tenant-a"}
        assert "tenant" in guarded.applied_scopes

    def test_department_scope_tenant_predicate_present(self, fixture_tables):
        """department scope + tenant_column → tenant 条件不因 scope 而丢。"""
        ctx = make_ctx("department", department="hr", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_TENANT_TABLE}", ctx)
        assert "tenant_id = %(sql_scope_tenant_id)s" in guarded.executable_sql
        assert guarded.params["sql_scope_tenant_id"] == "tenant-a"

    def test_existing_where_preserved_and_anded(self, fixture_tables):
        """用户自带 WHERE → tenant 条件 AND 叠加，不替换原谓词。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_TENANT_TABLE} "
            "WHERE project_name = 'TENANT-A-HR-111'", ctx)
        assert "project_name = 'TENANT-A-HR-111'" in guarded.executable_sql
        assert ("AND tenant_projects.tenant_id = %(sql_scope_tenant_id)s"
                in guarded.executable_sql)


class TestTenantFailClosed:
    def test_missing_tenant_id_denied(self, fixture_tables):
        """上下文缺 tenant_id → SQL_SCOPE_UNAVAILABLE（不得静默放行）。"""
        ctx = make_ctx("all", tenant_id="")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                f"SELECT project_name FROM {FIXTURE_TENANT_TABLE}", ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_parameterized_not_interpolated(self, fixture_tables):
        """tenant 值必须走参数通道：SQL 文本中出现的是占位符而非字面值
        （防拼接注入，规格书 §二十九）。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_TENANT_TABLE}", ctx)
        assert "'tenant-a'" not in guarded.executable_sql
        assert "%(sql_scope_tenant_id)s" in guarded.executable_sql


class TestNoTenantColumnNoInjection:
    def test_shared_table_without_tenant_column_untouched(self):
        """未声明 tenant_column（真实 18 表现状）→ 不注入、不虚构租户条件。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM product.products", ctx)
        assert "tenant_id" not in guarded.executable_sql
        assert guarded.params == {}
