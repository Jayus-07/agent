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

    def test_limit_all_forced_to_max(self):
        """LIMIT ALL 解析不出数值（expression 非数字字面量）→ 强制覆写
        为 max_limit，不得原样放行（旧实现按 0 处理 → 无界扫描）。"""
        sql = "SELECT id FROM product.products LIMIT ALL"
        safe_sql, _, _ = sql_validator.validate(sql)
        assert "LIMIT ALL" not in safe_sql.upper()
        assert "LIMIT 100" in safe_sql


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


class TestSensitiveColumnResolution:
    """2026-09-21 审查 #9 回归：敏感列匹配必须解析别名 + 三段式取对位。

    旧实现两个洞（sensitive_columns 生产为空故潜伏）：
      ① 三段式 schema.table.column 取 sens_table=parts[0]，拿到的是
         schema 名 → `customers.phone` 永远匹配不上；
      ② 不解析别名 → `c.phone`（c 为 customers 别名）永不匹配。
    """

    def _enable(self, monkeypatch, refs):
        monkeypatch.setattr(sql_validator, "sensitive_columns", set(refs))

    def test_qualified_column_on_sensitive_table_rejected(self, monkeypatch):
        self._enable(monkeypatch, {"customer.customers.phone"})
        sql = "SELECT phone FROM customer.customers LIMIT 5"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3

    def test_aliased_column_resolved_to_real_table(self, monkeypatch):
        self._enable(monkeypatch, {"customer.customers.phone"})
        sql = "SELECT c.phone FROM customer.customers c LIMIT 5"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3

    def test_two_part_config_matches_bare_schema_table(self, monkeypatch):
        """两段式配置 table.column 也能命中（别名解析后比对表名）。"""
        self._enable(monkeypatch, {"customers.phone"})
        sql = "SELECT c.phone FROM customer.customers c LIMIT 5"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3

    def test_where_clause_reference_rejected(self, monkeypatch):
        self._enable(monkeypatch, {"customer.customers.phone"})
        sql = "SELECT name FROM customer.customers c WHERE c.phone = '123' LIMIT 5"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 3

    def test_non_sensitive_column_still_passes(self, monkeypatch):
        self._enable(monkeypatch, {"customer.customers.phone"})
        safe_sql, _, _ = sql_validator.validate(
            "SELECT c.name FROM customer.customers c LIMIT 5"
        )
        assert "name" in safe_sql


class TestLayer1ExplicitRejects:
    """Layer 1 显式拒绝 SELECT INTO / FOR UPDATE（建议项 2026-09-21）。

    以前只靠 READ ONLY 事务兜底；现在 AST 层直接拒绝——INTO 会建表落盘，
    锁子句会持有行锁直到事务结束。
    """

    def test_select_into_rejected(self):
        sql = "SELECT id, name INTO new_products FROM product.products"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 1

    def test_for_update_rejected(self):
        sql = "SELECT id FROM product.products WHERE id = 1 FOR UPDATE"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 1

    def test_for_share_rejected(self):
        sql = "SELECT id FROM product.products WHERE id = 1 FOR SHARE"
        with pytest.raises(ValidationError) as exc_info:
            sql_validator.validate(sql)
        assert exc_info.value.layer == 1

    def test_plain_select_still_passes(self):
        safe_sql, _, _ = sql_validator.validate(
            "SELECT id FROM product.products LIMIT 5"
        )
        assert "LIMIT 5" in safe_sql
