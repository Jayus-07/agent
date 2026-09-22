# -*- coding: utf-8 -*-
"""子查询 scope 测试：外层与子查询是独立 SELECT scope，各注入各的；
相关子查询别名解析正确；CTE 别名引用不误当真实表。
"""
import pytest
import sqlglot
from sqlglot import exp

from tests.sql.conftest import FIXTURE_TENANT_TABLE, make_ctx

from backend.sql.policy import (
    SQLPolicyGuard,
    SQLPolicyError,
    SQL_TABLE_NOT_ALLOWED,
    _collect_scope_table_refs,
    _collect_table_refs,
)


class TestSubqueryInjection:
    def test_in_subquery_both_scopes_injected(self, fixture_tables):
        """IN 子查询：外层与子查询各自注入 tenant 条件（scope 不因嵌套丢失）。"""
        ctx = make_ctx("all", tenant_id="tenant-a")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f'SELECT id FROM {FIXTURE_TENANT_TABLE} WHERE id IN '
            f'(SELECT t.id FROM {FIXTURE_TENANT_TABLE} t '
            "WHERE t.project_name = 'x')", ctx)
        sql_text = guarded.executable_sql
        # 子查询别名条件 + 外层条件，共享同一参数占位符
        assert sql_text.count("%(sql_scope_tenant_id)s") == 2
        assert "t.tenant_id = %(sql_scope_tenant_id)s" in sql_text
        assert "tenant_projects.tenant_id = %(sql_scope_tenant_id)s" in sql_text
        assert guarded.params == {"sql_scope_tenant_id": "tenant-a"}

    def test_correlated_subquery_alias(self, fixture_tables):
        """相关子查询：子查询侧用其别名注入，不串用外层别名。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f'SELECT dp.project_name FROM demo.dept_projects dp WHERE EXISTS '
            f'(SELECT 1 FROM demo.dept_projects dp2 '
            f'WHERE dp2.id = dp.id)', ctx)
        sql_text = guarded.executable_sql
        assert "dp2.department = %(sql_scope_department)s" in sql_text
        assert "dp.department = %(sql_scope_department)s" in sql_text
        assert guarded.params == {"sql_scope_department": "hr"}


class TestCteReferenceSkipped:
    def test_cte_alias_not_collected_as_table(self):
        """CTE 别名（recent）不进真实表引用集合；CTE body 内的真实表单独成
        scope。（既有 validator 对 CTE-as-FROM 是 Layer 2 拒绝——此直测锁定
        引擎在该行为下的分组正确性。）"""
        sql = ('WITH recent AS (SELECT id, tenant_id FROM '
               'demo.tenant_projects) SELECT id FROM recent')
        parsed = sqlglot.parse(sql, read="postgres")
        stmt = parsed[0]
        cte_names = {cte.alias.lower() for cte in stmt.find_all(exp.CTE)}
        refs = _collect_table_refs(stmt, cte_names)
        # 只有 CTE body 里的真实表；recent（CTE 别名）不在集合
        assert refs == {"demo.tenant_projects"}

        scopes = [
            _collect_scope_table_refs(sel, cte_names)
            for sel in stmt.find_all(exp.Select)
        ]
        qualified_seen = {q for s in scopes for q, _ in s}
        assert "recent" not in qualified_seen


class TestSubqueryDenyPropagation:
    def test_personal_table_in_subquery_denied_for_department(self):
        """子查询里的 personal 表（无部门列）对 department 用户同样拒绝。"""
        ctx = make_ctx("department", department="hr")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                'SELECT id FROM product.products WHERE id IN '
                '(SELECT product_id FROM "order".order_items)', ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED
