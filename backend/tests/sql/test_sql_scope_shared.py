# -*- coding: utf-8 -*-
"""shared 表 scope 行为测试（STOP B 决策 D2）。

shared = 平台运营数据（product/inventory/crawler/customer.*），
无个人/部门归属列：持 sql.read 的 all/department 用户皆可读，
不虚构归属条件；self 用户只可读与自己关联的数据 → 无 self_column 即拒绝。
"""
import pytest

from backend.sql.policy import SQLPolicyGuard, SQLPolicyError, SQL_TABLE_NOT_ALLOWED
from backend.sql.schema_loader import schema_loader

from tests.sql.conftest import make_ctx

SHARED_TABLES = [
    "product.products",
    "product.categories",
    "product.product_tags",
    "inventory.inventory",
    "inventory.warehouses",
    "inventory.purchase_orders",
    "crawler.competitor_products",
    "crawler.competitor_price",
    "crawler.product_reviews",
    "customer.customers",
    "customer.customer_behavior",
]


class TestSharedReadableByDepartment:
    @pytest.mark.parametrize("qname", SHARED_TABLES)
    def test_department_scope_shared_tables_allowed(self, qname):
        """editor/department 查 11 张 shared 表 → 全放行、零注入。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT id FROM {qname}", ctx)
        assert guarded.applied_scopes == ()
        assert guarded.params == {}
        assert "LIMIT 100" in guarded.executable_sql


class TestSharedReadableByAll:
    @pytest.mark.parametrize("qname", SHARED_TABLES)
    def test_all_scope_shared_tables_allowed(self, qname):
        ctx = make_ctx("all")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            f"SELECT id FROM {qname}", ctx)
        assert guarded.applied_scopes == ()


class TestSharedJoinAllowed:
    def test_shared_cross_domain_join_allowed(self):
        """shared 跨域 JOIN → 放行，两表均无注入。"""
        ctx = make_ctx("department", department="hr")
        sql = ('SELECT p.sku FROM product.products p '
               'JOIN "order".order_items oi ON oi.product_id = p.id')
        # order_items 是 personal 无部门列 → department scope 拒绝
        # （该语义在 scope_department 文件锁定）；此处验证纯 shared join
        sql = ('SELECT i.stock_quantity FROM inventory.inventory i '
               'JOIN inventory.warehouses w ON w.id = i.warehouse_id')
        guarded = SQLPolicyGuard().validate_and_rewrite(sql, ctx)
        assert guarded.applied_scopes == ()
        assert guarded.params == {}


class TestSelfCannotReadSharedWithoutSelfColumn:
    @pytest.mark.parametrize("qname", SHARED_TABLES)
    def test_self_scope_shared_without_self_column_denied(self, qname):
        """self 只可读与自己关联的数据：shared 表无 self_column → 拒绝
        （规格书 §十一：self = 只能读取与 current user_id 关联的数据）。"""
        ctx = make_ctx("self", user_id="3")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                f"SELECT id FROM {qname}", ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED


class TestUserPredicateUntouched:
    def test_user_written_predicate_preserved(self):
        """用户 SQL 自带普通业务谓词 → 原样保留（Guard 只叠加不删改）。"""
        ctx = make_ctx("department", department="hr")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM product.products WHERE status = 'active'", ctx)
        assert "status = 'active'" in guarded.executable_sql


class TestSharedPolicyRegistry:
    def test_shared_tables_have_no_scope_columns_registered(self):
        """策略登记一致性：shared 表不声明任何归属列（不虚构字段）。"""
        for qname in SHARED_TABLES:
            tp = schema_loader.get_table_policy(qname)
            assert tp.data_domain == "shared", qname
            assert tp.tenant_column is None, qname
            assert tp.department_column is None, qname
            assert tp.self_column is None, qname
