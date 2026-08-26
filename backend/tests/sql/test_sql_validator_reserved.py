# -*- coding: utf-8 -*-
"""SQL validator 保留字标识符补引号回归测试。

fix f19：LLM 生成未加引号的 `order.order_items`（order 是 PostgreSQL
保留字），sqlglot 能解析但重序列化后 PG 报语法错误（MiniMax 切换后
实测暴露）。validator 须在输出 SQL 前给保留字 schema/table 名补引号，
与项目手写 SQL 约定一致（daily_report/data_fetcher 均用 `"order"."orders"`）。
"""
from backend.sql.sql_validator import sql_validator


class TestReservedIdentifierQuoting:
    def test_unquoted_order_schema_gets_quoted(self):
        """未加引号的 order.order_items → 输出 SQL 中 order 被加引号。"""
        sql = ("SELECT p.product_name, SUM(oi.quantity) AS q "
               "FROM product.products AS p "
               "JOIN order.order_items oi ON oi.product_id = p.id "
               "GROUP BY p.product_name ORDER BY q DESC LIMIT 5")
        safe_sql, tables, _ = sql_validator.validate(sql)
        assert '"order".order_items' in safe_sql
        assert "order.order_items" in tables  # 白名单提取不受引号影响
        assert "product.products" in tables

    def test_already_quoted_order_schema_unchanged(self):
        """已加引号的 `"order"."refunds"` 保持合法输出。"""
        sql = ('SELECT COUNT(*) FROM "order"."refunds" LIMIT 10')
        safe_sql, tables, _ = sql_validator.validate(sql)
        assert '"order"' in safe_sql
        assert any("refunds" in t for t in tables)

    def test_non_reserved_schema_not_quoted(self):
        """非保留字 schema（product）不被误加引号。"""
        sql = "SELECT id FROM product.products LIMIT 5"
        safe_sql, _, _ = sql_validator.validate(sql)
        assert "product.products" in safe_sql
        assert '"product"' not in safe_sql
