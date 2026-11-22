# -*- coding: utf-8 -*-
"""validator CTE 三层误拒回归（sql_v7 SC-107/SC-112 实测暴露，2026-11-21 修复）。

修复前三个独立缺陷都会拦掉合法 CTE 查询：
- Layer 2 表白名单：CTE 名（`FROM bounds`）与表值函数别名列定义产生的空名
  节点（`generate_series(...) AS calendar(day_value)`）被当成真实表提取；
- Layer 3 列白名单：CTE 别名引用（`FROM days d` 后的 `d.refund_date`）被
  当成真实表列去查数据字典；
- 函数正向白名单：缺 `generate_series`（postgres 方言下 sqlglot 解析为
  专属类，三种遍历口径键都缺失）。

回归锁定两件事：CTE 语句全链路通过；真实越权表 / 幻觉列仍拒绝（修复
只排除「不是库表的节点」，不放宽安全边界）。
"""
import pytest

from backend.sql.policy import SQLPolicyGuard
from backend.sql.schema_loader import schema_loader
from backend.sql.sql_validator import ValidationError

from tests.sql.conftest import make_ctx

# sql_v7 数据集原文（backend/evaluation/datasets/sql_v7/cases.jsonl），
# 双向锁：数据集改动导致本用例失败时应先确认是否配套改测试。
SC_107_GOLD = """WITH days AS (
    SELECT day_value::date AS refund_date
    FROM generate_series(
        CURRENT_DATE - INTERVAL '6 days',
        CURRENT_DATE,
        INTERVAL '1 day'
    ) AS calendar(day_value)
), refunds_by_day AS (
    SELECT created_at::date AS refund_date, COUNT(*) AS refund_count
    FROM "order".refunds
    WHERE created_at >= CURRENT_DATE - INTERVAL '6 days'
      AND created_at < CURRENT_DATE + INTERVAL '1 day'
    GROUP BY created_at::date
)
SELECT d.refund_date, COALESCE(r.refund_count, 0) AS refund_count
FROM days d
LEFT JOIN refunds_by_day r ON r.refund_date = d.refund_date
ORDER BY d.refund_date"""

SC_112_GOLD = """WITH bounds AS (
    SELECT date_trunc('month', CURRENT_DATE)::date AS current_month_start,
           CURRENT_DATE + 1 AS current_month_end,
           (date_trunc('month', CURRENT_DATE) - INTERVAL '1 month')::date AS previous_month_start
), periods AS (
    SELECT current_month_start,
           current_month_end,
           previous_month_start,
           LEAST(
               previous_month_start + (current_month_end - current_month_start),
               current_month_start
           ) AS previous_month_end
    FROM bounds
), sales AS (
    SELECT COALESCE(SUM(o.total_amount) FILTER (
               WHERE o.created_at >= p.current_month_start
                 AND o.created_at < p.current_month_end
           ), 0) AS current_month_sales,
           COALESCE(SUM(o.total_amount) FILTER (
               WHERE o.created_at >= p.previous_month_start
                 AND o.created_at < p.previous_month_end
           ), 0) AS previous_period_sales
    FROM "order".orders o
    CROSS JOIN periods p
)
SELECT current_month_sales,
       previous_period_sales,
       CASE WHEN previous_period_sales > 0
            THEN ROUND(
                (current_month_sales - previous_period_sales)
                / previous_period_sales * 100,
                2
            )
            ELSE NULL
       END AS mom_growth_percent
FROM sales"""


class TestCTEValidatorRegression:
    def test_sc107_generate_series_cte_passes(self):
        """表值函数别名列定义 + CTE 别名 JOIN + 引号保留字 schema 全链路通过。"""
        SQLPolicyGuard().validate_and_rewrite(SC_107_GOLD, make_ctx("all"))

    def test_sc112_multi_cte_passes(self):
        """多层 CTE（bounds → periods → sales）+ FILTER 聚合全链路通过。"""
        SQLPolicyGuard().validate_and_rewrite(SC_112_GOLD, make_ctx("all"))

    def test_cte_body_real_table_outside_allowlist_still_denied(self):
        """CTE 不能用来绕过表白名单：定义体内越权表照常拒绝。"""
        with pytest.raises(ValidationError, match="禁止访问表"):
            SQLPolicyGuard().validate_and_rewrite(
                "WITH cte AS (SELECT id FROM crawler.competitor_products) "
                "SELECT id FROM cte",
                make_ctx("all"),
            )

    def test_cte_hallucinated_column_still_denied(self):
        """幻觉列不能借 CTE 放行：不在任何真实表、也不在 CTE 列引用中 → 拒绝。"""
        with pytest.raises(ValidationError, match="未找到列"):
            SQLPolicyGuard().validate_and_rewrite(
                "WITH days AS (SELECT created_at FROM \"order\".refunds) "
                "SELECT no_such_column FROM days",
                make_ctx("all"),
            )


class TestCTEColumnScopeTightening:
    """L3 列放行口径收紧（2026-11-22）。

    旧口径「列名出现在任意 CTE 体内的列引用中即放行」会把体内+外层
    同名的幻觉列一并放行到执行层（实测 `WITH x AS (SELECT unit_price
    FROM product.products) SELECT unit_price FROM x` 漏过 L3），收紧为
    仅放行表值函数/子查询的别名列定义；CTE 输出别名由 select_aliases
    覆盖，不受影响。
    """

    def test_cte_body_and_outer_hallucinated_column_denied(self):
        """体内+外层同名幻觉列：L3 结构化拒绝，不再漏到执行层。"""
        with pytest.raises(ValidationError, match="未找到列"):
            SQLPolicyGuard().validate_and_rewrite(
                "WITH x AS (SELECT unit_price FROM product.products) "
                "SELECT unit_price FROM x",
                make_ctx("all"),
            )


class TestSensitiveColumnFailClosed:
    """_check_sensitive_columns 列名命中即确定性拒绝（fail-closed）。

    2026-11-22 修复：full_ref 赋值原在条件分支内，限定引用到非敏感表的
    真实同名列会先通过 allowlist、再在此处 UnboundLocalError 而非结构化
    拒绝；修复后拒绝语义不变。敏感层遍历全 AST（含 CTE 体内），CTE 包装
    不能绕过。
    """

    def test_qualified_to_other_table_fails_closed(self):
        """限定引用到非敏感表的真实同名列：确定性拒绝而非 UnboundLocalError。"""
        schema_loader.sensitive_columns.add("customer.customers.id")
        try:
            with pytest.raises(ValidationError, match="禁止查询敏感列"):
                SQLPolicyGuard().validate_and_rewrite(
                    "SELECT p.id FROM product.products p",
                    make_ctx("all"),
                )
        finally:
            schema_loader.sensitive_columns.discard("customer.customers.id")

    def test_cte_body_qualified_sensitive_reference_denied(self):
        """CTE 体内限定引用他表同名列（该列在别表被登记敏感）：敏感检查
        覆盖全 AST（含 CTE 体），借 CTE 包装不能绕过。用 id 列构造：
        product.products.id 在自身 browse_columns 中放行 allowlist，
        customer.customers.id 登记敏感后由敏感层拒绝。"""
        schema_loader.sensitive_columns.add("customer.customers.id")
        try:
            with pytest.raises(ValidationError, match="禁止查询敏感列"):
                SQLPolicyGuard().validate_and_rewrite(
                    "WITH x AS (SELECT p.id FROM product.products p) "
                    "SELECT id FROM x",
                    make_ctx("all"),
                )
        finally:
            schema_loader.sensitive_columns.discard("customer.customers.id")
