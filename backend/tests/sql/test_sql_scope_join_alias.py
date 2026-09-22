# -*- coding: utf-8 -*-
"""JOIN / 别名 / 自连接 scope 测试：逐表逐别名注入，scope 不因 JOIN 丢失。"""
import pytest

from tests.sql.conftest import FIXTURE_TENANT_TABLE, make_ctx

from backend.sql.policy import SQLPolicyGuard, SQLPolicyError, SQL_TABLE_NOT_ALLOWED


class TestJoinInjection:
    def test_two_joined_tables_each_injected(self, fixture_tables):
        """JOIN 两张声明 tenant_column 的表 → 两表各自注入条件。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f'SELECT a.id FROM {FIXTURE_TENANT_TABLE} a '
            f'JOIN {FIXTURE_TENANT_TABLE} b ON a.id = b.id', ctx)
        sql_text = guarded.executable_sql
        assert "a.tenant_id = %(sql_scope_tenant_id)s" in sql_text
        assert "b.tenant_id = %(sql_scope_tenant_id)s" in sql_text
        # 共享同值占位符（去重到单一参数）
        assert guarded.params == {"sql_scope_tenant_id": "tenant-a"}

    def test_mixed_domain_join_department_denied_by_personal_branch(
            self, fixture_tables):
        """shared JOIN personal（无部门列）→ department 用户整条拒绝。"""
        ctx = make_ctx("department", department="hr")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                'SELECT dp.id FROM demo.dept_projects dp '
                'JOIN "order".orders o ON o.id = dp.id', ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED

    def test_mixed_domain_join_all_allowed(self, fixture_tables):
        """同结构 JOIN 对 admin/all → 放行（personal 表 all 全量）。"""
        ctx = make_ctx("all")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            'SELECT dp.id FROM demo.dept_projects dp '
            'JOIN "order".orders o ON o.id = dp.id', ctx)
        assert guarded.params == {}


class TestAliasHandling:
    def test_alias_used_not_table_name(self, fixture_tables):
        """注入用别名限定（规格书 §三十四）：a.tenant_id 而非表名前缀。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f'SELECT tp.id FROM {FIXTURE_TENANT_TABLE} tp', ctx)
        assert "tp.tenant_id = %(sql_scope_tenant_id)s" in guarded.executable_sql
        assert "demo.tenant_projects.tenant_id" not in guarded.executable_sql

    def test_table_name_fallback_without_alias(self, fixture_tables):
        """无别名表 → 表名作列限定符（与 row_security 同语义）。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f'SELECT id FROM {FIXTURE_TENANT_TABLE}', ctx)
        assert "tenant_projects.tenant_id = %(sql_scope_tenant_id)s" \
            in guarded.executable_sql

    def test_self_join_both_aliases_injected(self, fixture_tables):
        """自连接：同一受保护表多个别名各自注入（P1-11 同款语义回归）。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f'SELECT a.id FROM {FIXTURE_TENANT_TABLE} a '
            f'JOIN {FIXTURE_TENANT_TABLE} b ON a.tenant_id = b.tenant_id', ctx)
        sql_text = guarded.executable_sql
        assert "a.tenant_id = %(sql_scope_tenant_id)s" in sql_text
        assert "b.tenant_id = %(sql_scope_tenant_id)s" in sql_text
        assert "tenant" in guarded.applied_scopes
