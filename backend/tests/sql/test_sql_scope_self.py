# -*- coding: utf-8 -*-
"""self scope 测试（demo 显式兼容映射 + fail-closed）。

order.orders.customer_id 是唯一 self_column；纯数字 user_id 显式转整数，
其余一律解析失败拒绝（禁止猜/按用户名查/模糊匹配）。
order_items/refunds/customer_behavior 无直接 self_column → self 拒绝，
本阶段不做跨表 ownership JOIN。
"""
import pytest

from backend.sql.policy import (
    SQLPolicyGuard,
    SQLPolicyError,
    SQL_TABLE_NOT_ALLOWED,
    SQL_SCOPE_UNAVAILABLE,
    resolve_self_value,
)

from tests.sql.conftest import make_ctx


class TestSelfResolver:
    @pytest.mark.parametrize("raw,expected", [
        ("3", 3),
        (" 12 ", 12),
        ("0", 0),
        ("", None),
        ("abc", None),
        ("u-admin", None),
        ("3.5", None),
        ("+3", None),
        ("1e3", None),
        ("999999999999999999999999", None),
    ])
    def test_resolve_self_value(self, raw, expected):
        """纯数字 → int；UUID/用户名/小数/空 → None（demo 显式映射，
        不代表 auth user_id 与 customer_id 是领域同一身份）。"""
        assert resolve_self_value(raw) == expected


class TestSelfInjectionOnOrders:
    def test_numeric_user_id_injects_customer_id(self):
        """self + orders + 数字 user_id → customer_id 参数化注入。"""
        ctx = make_ctx("self", user_id="3")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            'SELECT order_no FROM "order".orders', ctx)
        assert "customer_id = %(sql_scope_self_value)s" in guarded.executable_sql
        assert guarded.params == {"sql_scope_self_value": 3}
        assert "self" in guarded.applied_scopes

    def test_injected_value_is_int_not_string(self):
        """参数值必须是整数（customer_id 为 integer 列，字符串会执行期
        类型错误——历史缺陷回归锁）。"""
        ctx = make_ctx("self", user_id="3")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            'SELECT order_no FROM "order".orders', ctx)
        assert isinstance(guarded.params["sql_scope_self_value"], int)

    def test_non_numeric_user_id_denied(self):
        """self + orders + 非数字 user_id → SQL_SCOPE_UNAVAILABLE。"""
        ctx = make_ctx("self", user_id="u-admin")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                'SELECT order_no FROM "order".orders', ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_empty_user_id_denied(self):
        ctx = make_ctx("self", user_id="")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                'SELECT order_no FROM "order".orders', ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_user_customer_predicate_not_trusted(self):
        """用户自带 customer_id 谓词 → 安全条件仍叠加（AND 交集）。"""
        ctx = make_ctx("self", user_id="3")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            'SELECT order_no FROM "order".orders WHERE customer_id = 4', ctx)
        assert "customer_id = 4" in guarded.executable_sql
        assert ("AND orders.customer_id = %(sql_scope_self_value)s"
                in guarded.executable_sql)
        assert guarded.params["sql_scope_self_value"] == 3


class TestSelfWithoutSelfColumn:
    @pytest.mark.parametrize("qname", [
        '"order".order_items',
        '"order".refunds',
        "customer.customer_behavior",
    ])
    def test_indirect_ownership_denied(self, qname):
        """归属需跨表 join 才能表达的表 → self 直接拒绝，不自动 JOIN
        （规格书 §三十六：安全优先）。"""
        ctx = make_ctx("self", user_id="3")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                f"SELECT id FROM {qname}", ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED

    def test_orders_policy_registry(self):
        """策略登记：orders 是唯一 self_column 表（demo 现状）。"""
        from backend.sql.schema_loader import schema_loader
        tp = schema_loader.get_table_policy("order.orders")
        assert tp.data_domain == "personal"
        assert tp.self_column == "customer_id"
        assert tp.tenant_column is None
        assert tp.department_column is None
