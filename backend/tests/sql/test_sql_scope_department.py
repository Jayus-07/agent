# -*- coding: utf-8 -*-
"""department scope 测试（STOP B 决策 D2）。

声明 department_column 的表：department scope 参数化注入、
principal.department 为空 fail-closed；personal 表无部门列 → 拒绝；
shared 表放行；用户自带部门谓词不可替代安全注入（AND 叠加）。
"""
import pytest

from backend.sql.policy import (
    SQLPolicyGuard,
    SQLPolicyError,
    SQL_TABLE_NOT_ALLOWED,
    SQL_SCOPE_UNAVAILABLE,
)

from tests.sql.conftest import FIXTURE_DEPT_TABLE, make_ctx


class TestDepartmentInjection:
    def test_department_predicate_parameterized(self, fixture_tables):
        """fixture personal 表（department_column）+ department scope →
        参数化注入。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_DEPT_TABLE}", ctx)
        assert "department = %(sql_scope_department)s" in guarded.executable_sql
        assert guarded.params == {"sql_scope_department": "hr"}
        assert "department" in guarded.applied_scopes

    def test_department_value_not_interpolated(self, fixture_tables):
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_DEPT_TABLE}", ctx)
        assert "'hr'" not in guarded.executable_sql


class TestDepartmentFailClosed:
    def test_null_department_denied(self, fixture_tables):
        """data_scope=department + department=NULL → 拒绝（不得退化为
        不加过滤，规格书 §三十七/§七十）。"""
        ctx = make_ctx("department", department="")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                f"SELECT project_name FROM {FIXTURE_DEPT_TABLE}", ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_personal_without_department_column_denied(self):
        """personal 表（orders/order_items/refunds）无部门列 → department
        scope 拒绝（无访问表达，fail-closed）。"""
        ctx = make_ctx("department", department="hr")
        for qname in ("\"order\".orders", "\"order\".order_items",
                      "\"order\".refunds"):
            with pytest.raises(SQLPolicyError) as ei:
                SQLPolicyGuard().validate_and_rewrite(
                    f"SELECT id FROM {qname}", ctx)
            assert ei.value.code == SQL_TABLE_NOT_ALLOWED, qname

    def test_user_department_predicate_not_trusted(self, fixture_tables):
        """用户 SQL 自带 department='finance' → 照常叠加真实部门条件
        （结果为交集，不能借自有谓词扩大范围，规格书 §三十）。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT project_name FROM {FIXTURE_DEPT_TABLE} "
            "WHERE department = 'finance'", ctx)
        # 用户谓词保留 + 安全谓词叠加
        assert "department = 'finance'" in guarded.executable_sql
        assert ("AND dept_projects.department = %(sql_scope_department)s"
                in guarded.executable_sql)
        assert guarded.params["sql_scope_department"] == "hr"


class TestDepartmentSharedAllowed:
    def test_shared_table_allowed_without_predicate(self):
        """D2：shared 表对 department 用户放行且不虚构部门条件。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            "SELECT id FROM product.products", ctx)
        assert "department" not in guarded.executable_sql
        assert guarded.params == {}


class TestAliasQualified:
    def test_injection_uses_alias(self, fixture_tables):
        """带别名的表注入别名限定列（不是表名，规格书 §三十四）。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT dp.project_name FROM {FIXTURE_DEPT_TABLE} dp", ctx)
        assert "dp.department = %(sql_scope_department)s" in guarded.executable_sql
