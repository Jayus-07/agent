"""test_business_warehouse_schema.py — 业务数据仓库 schema 验证

覆盖：
- schema_loader 加载 7 个业务域
- allowed_tables 用 schema-qualified 全限定名
- 反向索引：schema → tables
- validator 接受 `product.products` 形式
- validator 拒绝未授权 schema（如 `evil.users`）
- validator 接受裸表名（向后兼容）
- schema_config.SCHEMA_CONFIG 一致性
"""
from __future__ import annotations

import pytest

from backend.sql.schema_loader import schema_loader
from backend.sql.sql_validator import sql_validator, ValidationError


# ─────────────────────────────────────────────────────────────
# schema_loader 静态校验
# ─────────────────────────────────────────────────────────────

class TestSchemaLoaderBusinessDomains:
    """业务数据仓库：7 个 schema 域全部加载，19 张表按域分组。"""

    def test_seven_business_domains_loaded(self):
        expected = {"product", "order", "inventory", "customer", "crawler", "finance", "ai"}
        assert schema_loader.allowed_schemas == expected

    def test_eighteen_tables_loaded(self):
        """核心 + 扩展共 18 张表，每张都是 schema-qualified 名。"""
        assert len(schema_loader.allowed_tables) == 18
        for t in schema_loader.allowed_tables:
            assert "." in t, f"表名 {t} 必须 schema-qualified"

    def test_schema_to_tables_mapping(self):
        by_schema = {
            "product": {"product.products", "product.categories", "product.product_tags"},
            "order":   {"order.orders", "order.order_items", "order.refunds"},
            "inventory": {"inventory.inventory", "inventory.warehouses", "inventory.purchase_orders"},
            "customer": {"customer.customers", "customer.customer_behavior"},
            "crawler": {"crawler.competitor_products", "crawler.competitor_price", "crawler.product_reviews"},
            "finance": {"finance.expenses", "finance.daily_profit"},
            "ai":      {"ai.agent_tasks", "ai.agent_trace"},
        }
        for schema_name, expected_tables in by_schema.items():
            assert set(schema_loader.get_tables_in_schema(schema_name)) == expected_tables

    def test_get_table_info_for_schema_qualified(self):
        info = schema_loader.get_table_info(["product.products"])
        assert "表名: product.products" in info
        assert "sale_price" in info
        assert "cost_price" in info

    def test_get_table_info_for_bare_name_works_too(self):
        """用户可以传入 'products'，schema_loader 拆分 schema/table 后展示。"""
        info = schema_loader.get_table_info(["products", "orders"])
        # 找到带 sku 的 products 表
        assert "sku" in info
        assert "order_no" in info

    def test_split_qualified_helper(self):
        assert schema_loader.split_qualified("product.products") == ("product", "products")
        assert schema_loader.split_qualified("products") == ("", "products")
        assert schema_loader.split_qualified("order.orders") == ("order", "orders")


# ─────────────────────────────────────────────────────────────
# sql_validator 多 schema 校验
# ─────────────────────────────────────────────────────────────

class TestValidatorAllowsSchemaQualified:
    """用户/Agent 输出 `schema.table` 应当被接受。"""

    def test_full_qualified_select_passes(self):
        sql = "SELECT product_name, sale_price FROM product.products LIMIT 5"
        safe, tables, _ = sql_validator.validate(sql)
        assert tables == {"product.products"}
        assert "product.products" in safe

    def test_full_qualified_join_passes(self):
        sql = """
        SELECT p.product_name, COUNT(r.id) AS refund_count
        FROM product.products p
        JOIN order.refunds r ON r.product_id = p.id
        GROUP BY p.product_name
        LIMIT 10
        """
        safe, tables, _ = sql_validator.validate(sql)
        assert tables == {"product.products", "order.refunds"}

    def test_user_question_query_passes(self):
        """用户原 SSE 错误流的目标查询。"""
        sql = """
        SELECT p.product_name, p.sale_price, p.brand
        FROM product.products p
        ORDER BY p.sale_price DESC
        LIMIT 5
        """
        safe, _, _ = sql_validator.validate(sql)
        assert "ORDER BY" in safe.upper()


class TestValidatorAllowsBareNamesBackward:
    """LLM 偶尔会只输出裸表名（旧习惯），应容错识别。"""

    def test_bare_products_resolves(self):
        sql = "SELECT product_name FROM products LIMIT 3"
        safe, tables, _ = sql_validator.validate(sql)
        # 命中 reverse-resolve：找到 schema-qualified 的 key
        assert any(t == "products" for t in tables)

    def test_bare_orders_resolves(self):
        sql = "SELECT order_no FROM orders LIMIT 3"
        _, tables, _ = sql_validator.validate(sql)
        assert "orders" in tables


class TestValidatorRejectsForbidden:
    """安全：阻止未授权 schema、表、敏感列。"""

    def test_unknown_schema_rejected(self):
        sql = "SELECT * FROM evil.users LIMIT 1"
        with pytest.raises(ValidationError) as exc:
            sql_validator.validate(sql)
        assert exc.value.layer == 2
        assert "禁止访问" in str(exc.value)

    def test_unknown_table_rejected(self):
        sql = "SELECT * FROM product.fake_table LIMIT 1"
        with pytest.raises(ValidationError) as exc:
            sql_validator.validate(sql)
        assert exc.value.layer == 2

    def test_unknown_bare_table_rejected(self):
        sql = "SELECT * FROM ghost_table LIMIT 1"
        with pytest.raises(ValidationError) as exc:
            sql_validator.validate(sql)
        assert exc.value.layer == 2

    def test_write_statement_rejected(self):
        sql = "DELETE FROM product.products WHERE id = 1"
        with pytest.raises(ValidationError) as exc:
            sql_validator.validate(sql)
        assert exc.value.layer == 1

    def test_limit_over_max_rejected(self):
        sql = "SELECT * FROM product.products LIMIT 50000"
        with pytest.raises(ValidationError) as exc:
            sql_validator.validate(sql)
        assert exc.value.layer == 5
        assert "超过" in str(exc.value)

    def test_banned_function_rejected(self):
        sql = "SELECT pg_sleep(10), * FROM product.products LIMIT 1"
        with pytest.raises(ValidationError) as exc:
            sql_validator.validate(sql)
        # pg_sleep 命中函数黑名单
        assert exc.value.layer == 4


class TestValidatorNormalizesPostgres:
    """验证后 SQL 走 postgres dialect 标准化。"""

    def test_alias_preserved(self):
        sql = "SELECT p.sale_price AS 价格 FROM product.products p LIMIT 3"
        safe, _, _ = sql_validator.validate(sql)
        # 别名应保留
        assert "p" in safe


# ─────────────────────────────────────────────────────────────
# schema_config 一致性
# ─────────────────────────────────────────────────────────────

class TestSchemaConfigSelfConsistent:
    """schema_config 里定义的所有表，必须与 schema_loader 加载结果完全一致。"""

    def test_no_orphan_tables(self):
        from backend.sql.data.schema_config import SCHEMA_CONFIG
        declared = set(SCHEMA_CONFIG["tables"].keys())
        loaded = schema_loader.allowed_tables
        assert declared == loaded, (
            f"declared - loaded: {declared - loaded}; "
            f"loaded - declared: {loaded - declared}"
        )

    def test_max_limit_positive(self):
        assert schema_loader.max_limit > 0
        assert schema_loader.max_limit <= 1000

    def test_query_timeout_positive(self):
        assert schema_loader.query_timeout > 0

    def test_banned_functions_uppercased(self):
        for fn in schema_loader.banned_functions:
            assert fn == fn.upper(), f"banned function {fn} not uppercased"
