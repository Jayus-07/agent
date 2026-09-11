# -*- coding: utf-8 -*-
"""SQL validator 加固回归测试。

覆盖两个加固点：
  1. LIMIT 强制校验只看顶层 SELECT —— 旧实现用 stmt.find(exp.Limit)
     遍历整棵 AST，子查询里的 LIMIT 会被误当成外层限制，外层查询
     实际无界扫描。
  2. 敏感列表上禁止 `*` / `t.*` 投影 —— Layer 3 只拦显式列名，
     SELECT * 会把敏感列一并带出。
"""
import pytest

from backend.sql.sql_validator import sql_validator, ValidationError


class TestLimitEnforcement:
    def test_subquery_limit_does_not_satisfy_outer(self):
        """子查询有 LIMIT 时，外层查询仍必须补 LIMIT。"""
        sql = ("SELECT t.id FROM (SELECT id FROM product.products "
               "ORDER BY id LIMIT 5) t")
        safe_sql, _, _ = sql_validator.validate(sql)
        # 外层补上了 max_limit=100；若回归，外层无 LIMIT
        assert "LIMIT 100" in safe_sql

    def test_top_level_limit_kept(self):
        """顶层 LIMIT ≤ max_limit 时保留原值。"""
        sql = "SELECT id FROM product.products LIMIT 10"
        safe_sql, _, _ = sql_validator.validate(sql)
        assert "LIMIT 10" in safe_sql

    def test_top_level_limit_over_max_rejected(self):
        """顶层 LIMIT 超过 max_limit → ValidationError(layer=5)。"""
        sql = "SELECT id FROM product.products LIMIT 500"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 5

    def test_cte_as_table_rejected_by_allowlist_first(self):
        """既有行为：CTE 作为 FROM 源会被 Layer 2 表名白名单拒绝
        （见 test_sql_validator_alias.py 注释），到不了 LIMIT 层。"""
        sql = ('WITH top5 AS (SELECT id FROM product.products LIMIT 5) '
               "SELECT id FROM top5")
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 2


class TestStarProjectionHardening:
    @pytest.fixture(autouse=True)
    def _enable_sensitive_columns(self, monkeypatch):
        """临时启用一条敏感列配置（生产 schema_config 默认为空）。"""
        monkeypatch.setattr(
            sql_validator, "sensitive_columns", {"customer.customers.phone"}
        )

    def test_select_star_on_sensitive_table_rejected(self):
        """敏感表上 SELECT * → ValidationError(layer=3)。"""
        sql = "SELECT * FROM customer.customers LIMIT 5"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3
        assert "SELECT *" in str(exc_info.value)

    def test_qualified_star_on_sensitive_table_rejected(self):
        """敏感表上 SELECT c.* 同样拒绝。"""
        sql = "SELECT c.* FROM customer.customers c LIMIT 5"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3

    def test_star_on_subquery_over_sensitive_table_rejected(self):
        """子查询包一层也不能绕过：外层 SELECT * 仍被拒。"""
        sql = ("SELECT * FROM (SELECT name FROM customer.customers) t "
               "LIMIT 5")
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3

    def test_count_star_on_sensitive_table_passes(self):
        """COUNT(*) 不是列投影，允许。"""
        safe_sql, _, _ = sql_validator.validate(
            "SELECT COUNT(*) FROM customer.customers"
        )
        assert "COUNT" in safe_sql

    def test_explicit_columns_on_sensitive_table_pass(self):
        """显式列出非敏感列 → 通过。"""
        safe_sql, _, _ = sql_validator.validate(
            "SELECT name FROM customer.customers LIMIT 5"
        )
        assert "name" in safe_sql

    def test_star_on_non_sensitive_table_passes(self):
        """无敏感列配置的表上 SELECT * 不受影响（默认行为）。"""
        sql = "SELECT * FROM product.products LIMIT 5"
        safe_sql, _, _ = sql_validator.validate(sql)
        assert "product" in safe_sql.lower()
