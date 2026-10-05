# -*- coding: utf-8 -*-
"""internal 表 scope 行为测试（STOP B 决策 D4）。

internal（finance.* + ai.*）仅 data_scope=all 可读；
editor/department/self 一律拒绝，且错误对外文案不泄露表存在性。
"""
import pytest

from backend.sql.policy import (
    SQLPolicyGuard,
    SQLPolicyError,
    SQL_TABLE_NOT_ALLOWED,
)
from backend.sql.schema_loader import schema_loader

from tests.sql.conftest import make_ctx

INTERNAL_TABLES = [
    "finance.expenses",
    "finance.daily_profit",
    "ai.agent_tasks",
    "ai.agent_trace",
]


def _probe_sql(qname: str) -> str:
    """探针列取数据字典首列——daily_profit 主键是 date 无 id，固定写 id
    会被列校验先拦（column_undefined），到不了 scope 门，探针失真。"""
    cols = sorted(schema_loader.get_browse_columns(qname))
    return f"SELECT {cols[0] if cols else 'id'} FROM {qname}"


class TestInternalDeniedBelowAll:
    @pytest.mark.parametrize("qname", INTERNAL_TABLES)
    def test_department_scope_denied(self, qname):
        ctx = make_ctx("department", department="hr")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                _probe_sql(qname), ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED

    @pytest.mark.parametrize("qname", INTERNAL_TABLES)
    def test_self_scope_denied(self, qname):
        ctx = make_ctx("self", user_id="3")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                _probe_sql(qname), ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED


class TestInternalAllowedByAll:
    @pytest.mark.parametrize("qname", INTERNAL_TABLES)
    def test_all_scope_allowed(self, qname):
        ctx = make_ctx("all")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            _probe_sql(qname), ctx)
        assert guarded.applied_scopes == ()
        assert "LIMIT 100" in guarded.executable_sql


class TestInternalIndirectAccess:
    def test_internal_table_in_join_denied(self):
        """shared 主表 JOIN internal 表 → 整条拒绝（任一表 internal 即拒）。"""
        ctx = make_ctx("department", department="hr")
        sql = ('SELECT p.sku, e.amount FROM product.products p '
               'JOIN finance.expenses e ON e.id = p.id')
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(sql, ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED

    def test_internal_table_in_subquery_denied(self):
        """internal 表藏在子查询里同样拒绝（全 AST 表引用判定）。"""
        ctx = make_ctx("department", department="hr")
        outer_col = sorted(schema_loader.get_browse_columns("product.products"))[0]
        inner_col = sorted(schema_loader.get_browse_columns("ai.agent_tasks"))[0]
        sql = (f'SELECT {outer_col} FROM product.products WHERE {outer_col} IN '
               f'(SELECT {inner_col} FROM ai.agent_tasks)')
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(sql, ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED


class TestNoExistenceLeak:
    def test_user_text_does_not_leak_table_names(self):
        """对外安全文案不含表名/schema（规格书 §六十三：不泄露表存在性）。"""
        ctx = make_ctx("department", department="hr")
        try:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT id FROM finance.expenses", ctx)
            raise AssertionError("internal 表未被拒绝")
        except SQLPolicyError as e:
            assert "finance" not in e.user_text.lower()
            assert "expenses" not in e.user_text.lower()
            assert "agent" not in e.user_text.lower()
            # 文案是固定安全话术
            assert e.user_text == "该数据不在当前可访问范围内。"
