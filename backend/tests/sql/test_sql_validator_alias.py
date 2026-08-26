# -*- coding: utf-8 -*-
"""SQL validator 未定义别名拦截回归测试。

fix f21：LLM 生成引用未 JOIN 表别名的 SQL（如 `oi.order_id` 却没有
JOIN order_items AS oi），PG 报「对于表 oi，丢失 FROM 子句」，而
syntax_error 不可重试直接兜底（MiniMax 切换后实测暴露）。validator
应在 Layer 2 拦截为 ValidationError，走既有重试链路带错误反馈重新生成。
"""
import pytest

from backend.sql.sql_validator import sql_validator, ValidationError


class TestUndefinedAliasRejected:
    def test_undefined_alias_raises_validation_error(self):
        """引用未在 FROM/JOIN 定义的别名 oi → ValidationError(layer=2)。"""
        sql = ("SELECT r.product_id, CAST(COUNT(r.id) AS DECIMAL) "
               "/ NULLIF(COUNT(DISTINCT oi.order_id), 0) AS refund_rate "
               'FROM "order".refunds r GROUP BY r.product_id LIMIT 10')
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 2
        assert "oi" in str(exc_info.value)

    def test_defined_alias_passes(self):
        """所有别名均有 FROM/JOIN 定义 → 校验通过。"""
        sql = ("SELECT p.product_name, COUNT(r.id) AS refund_count "
               "FROM product.products p "
               'JOIN "order".refunds r ON r.product_id = p.id '
               "GROUP BY p.product_name LIMIT 10")
        safe_sql, tables, _ = sql_validator.validate(sql)
        assert "product.products" in tables

    def test_subquery_alias_passes(self):
        """子查询别名 t 视为已定义。"""
        sql = ("SELECT t.cnt FROM (SELECT COUNT(*) AS cnt "
               "FROM product.products) t LIMIT 5")
        safe_sql, _, _ = sql_validator.validate(sql)
        assert "cnt" in safe_sql

    def test_cte_alias_not_falsely_flagged(self):
        """CTE 别名 bounds 不被 f21 误判为未定义别名。

        注：CTE 作为 FROM 源本身会被 Layer 2 表名白名单拒绝
        （既有行为，非 f21 引入），此处只验证错误不出自别名层。
        """
        sql = ('WITH bounds AS (SELECT MAX(created_at) AS latest FROM "order".orders) '
               "SELECT latest FROM bounds LIMIT 5")
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert "别名" not in str(exc_info.value)
