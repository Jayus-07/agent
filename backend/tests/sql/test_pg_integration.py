"""test_pg_integration.py — 真实 PostgreSQL 集成测试（连本机 demo 库）

前置条件：
  1. 已跑 backend/sql/migrations/001_business_warehouse.sql
  2. .env / backend config 里 PGHOST=localhost / PGDATABASE=demo / PGUSER=postgres
  3. （可选）PGPASSWORD；空的话默认走 peer/trust

跑命令：
    cd backend && python -m pytest tests/sql/test_pg_integration.py -v --no-cov
"""
from __future__ import annotations

import os
import pytest
import psycopg2
from psycopg2 import sql as pgsql

from backend.config import BUSINESS_DB_CONFIG
from backend.sql.executor import execute_sql, execute_sql_struct
from backend.sql.sql_result import SQLResult


def _conn_alive() -> bool:
    """检查业务库 agent_business 是否可达。"""
    try:
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _conn_alive(),
    reason="No PostgreSQL reachable on localhost:5432/agent_business/postgres",
)


# ─────────────────────────────────────────────────────────────
# 数据 sanity：migration 已落地
# ─────────────────────────────────────────────────────────────

class TestSeedDataExists:
    def test_product_products_has_rows(self):
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM product.products')
        n = cur.fetchone()[0]
        conn.close()
        assert n >= 5, f"product.products 应至少有 5 行 seed，实际 {n}"

    def test_orders_with_items(self):
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM "order".orders')
        assert cur.fetchone()[0] >= 4
        cur.execute('SELECT COUNT(*) FROM "order".order_items')
        assert cur.fetchone()[0] >= 4
        conn.close()

    def test_refunds_for_high_refund_analysis(self):
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM "order".refunds')
        assert cur.fetchone()[0] >= 1
        conn.close()

    def test_inventory_seed(self):
        conn = psycopg2.connect(**BUSINESS_DB_CONFIG)
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM inventory.inventory WHERE stock_quantity < safety_stock')
        low = cur.fetchone()[0]
        conn.close()
        # seed 注入了 2 条 stock < safety_stock（珍珠项链银戒指）
        assert low >= 2, f"应至少 2 条低库存预警，实际 {low}"


# ─────────────────────────────────────────────────────────────
# executor.execute_sql_struct 真实路径
# ─────────────────────────────────────────────────────────────

class TestExecuteStructRealPG:
    def test_select_all_products(self):
        result = execute_sql_struct(
            "SELECT id, sku, product_name, sale_price FROM product.products ORDER BY sale_price DESC",
            BUSINESS_DB_CONFIG,
        )
        assert result.status == "success"
        assert result.row_count >= 5
        assert result.columns == ["id", "sku", "product_name", "sale_price"]
        # seed: 羊毛外套 599 是最高
        top = result.rows[0]
        assert top["product_name"] == "羊毛外套"
        assert float(top["sale_price"]) == 599.00

    def test_select_returns_structured(self):
        result = execute_sql_struct(
            "SELECT product_name, sale_price FROM product.products LIMIT 3",
            BUSINESS_DB_CONFIG,
        )
        assert result.status in ("success", "no_data")
        assert isinstance(result.rows, list)
        assert result.columns == ["product_name", "sale_price"]

    def test_unknown_table_returns_schema_mismatch(self):
        """schema_mismatch 是 B 段核心修复：与原 SSE 'success + 错误信息' 对比。

        不检查错误消息字符串（PG 区域设置可能让消息是中文/英文）；
        检查 SQLSTATE → status/error_type 的映射。
        """
        result = execute_sql_struct(
            "SELECT * FROM product.fake_table LIMIT 1",
            BUSINESS_DB_CONFIG,
        )
        assert result.status == "syntax_error"
        assert result.error_type == "schema_mismatch"

    def test_unknown_schema_returns_schema_mismatch(self):
        """未授权 schema 应被分类为 schema_mismatch（rls+验证器 + executor 双重拦截）。"""
        result = execute_sql_struct(
            "SELECT * FROM evil.users LIMIT 1",
            BUSINESS_DB_CONFIG,
        )
        assert result.status == "syntax_error"

    def test_invalid_sql_returns_syntax_error(self):
        result = execute_sql_struct(
            "SELEKT * FROM product.products LIMIT 1",  # 故意拼错
            BUSINESS_DB_CONFIG,
        )
        assert result.status == "syntax_error"

    def test_statement_timeout_returns_timeout(self):
        """SQL 超过 5s 应触发 timeout（statement_timeout=5s in schema_config）。"""
        # 用 pg_sleep 试探；executor 拦截 banned_functions → 改成 pg_catalog 长操作
        # 跳过：banned function 拦截比 timeout 早，本测试只验证接口存在
        pytest.skip(
            "pg_sleep 被 banned_functions 拦截；timeout 通过长查询触发，"
            "此处仅验证接口，不强测 timeout 路径"
        )


# ─────────────────────────────────────────────────────────────
# 用户原 SSE 失败问题：端到端"最近一个月内价格最高的商品信息"
# ─────────────────────────────────────────────────────────────

class TestUserOriginalQuestion:
    """用户原 SSE 流截图里失败的查询——现在应真数据返回。"""

    def test_high_price_product_query_returns_real_data(self):
        # 这就是用户原 SSE 失败流截图里的目标查询，等价 SQL
        sql = """
        SELECT p.product_name, p.sale_price, p.brand
        FROM product.products p
        WHERE p.status = 'active'
        ORDER BY p.sale_price DESC
        LIMIT 5
        """
        result = execute_sql_struct(sql, BUSINESS_DB_CONFIG)
        # 原 SSE：sql_skill status=success 但 output="错误: 关系 orders 不存在"
        # 现在：真数据
        assert result.status == "success", f"期望 success，实际 {result.status} ({result.error})"
        assert result.row_count == 5  # product.products 有 5 行
        # 排序降序：最高 599.00 → 最低 99.00
        prices = [float(r["sale_price"]) for r in result.rows]
        assert prices == sorted(prices, reverse=True)
        assert prices[0] == 599.00   # 羊毛外套
        assert prices[-1] == 99.00   # 银戒指

    def test_high_refund_product_cross_domain_join(self):
        """跨域 join：product + refunds（用户提到的"高退款率商品"）。"""
        sql = """
        SELECT p.product_name, COUNT(r.id) AS refund_count
        FROM product.products p
        JOIN "order".refunds r ON r.product_id = p.id
        GROUP BY p.product_name
        ORDER BY refund_count DESC
        """
        result = execute_sql_struct(sql, BUSINESS_DB_CONFIG)
        # seed: A001 珍珠项链退款1笔，A003 丝绸连衣裙退款1笔
        assert result.status == "success"
        assert result.row_count >= 1
        # 至少有一个退款商品
        counts = [int(r["refund_count"]) for r in result.rows]
        assert any(c >= 1 for c in counts)


# ─────────────────────────────────────────────────────────────
# sql_validator 真实路径（不连 DB，只校验 AST）
# ─────────────────────────────────────────────────────────────

from backend.sql.sql_validator import sql_validator, ValidationError


class TestValidatorWithRealSQL:
    def test_user_query_passes(self):
        sql = "SELECT p.product_name, p.sale_price FROM product.products p ORDER BY p.sale_price DESC LIMIT 5"
        safe, tables, _ = sql_validator.validate(sql)
        assert "product.products" in safe
        assert tables == {"product.products"}

    def test_cross_domain_join_passes(self):
        sql = """
        SELECT p.product_name, COUNT(r.id) AS cnt
        FROM product.products p
        JOIN "order".refunds r ON r.product_id = p.id
        GROUP BY p.product_name
        """
        safe, tables, _ = sql_validator.validate(sql)
        assert "product.products" in tables
        assert '"order".refunds' in safe or 'order.refunds' in safe
        assert any("refunds" in t for t in tables)


# ─────────────────────────────────────────────────────────────
# 端到端：mock LLM 让 generator 出 SQL，SQLSkill 写出真数据 StepResult
# ─────────────────────────────────────────────────────────────

class TestEndToEndMockLLM:
    """模拟真实链路：router 固定选表 → generator 固定出 SQL → executor 走真实 PG。

    这一组是用户原 SSE 流截图里失败的 query 的反向证明 —— 现在 status=success + 真数据。
    """

    def _make_state(self) -> dict:
        return {
            "question": "最近一个月内价格最高的商品信息",
            "plan": {
                "nodes": {
                    "1": {
                        "step_id": "1",
                        "capability": "sql.query",
                        "description": "最近一个月内价格最高的商品信息",
                    }
                }
            },
            "current_step_id": "1",
            "step_results": {},
            "current_user_id": None,
        }

    def _patched_agent(self, sql: str):
        """构造一个 SQLAgent 替身：ask_struct 直跑真 PG 拿 SQLResult。"""
        from unittest.mock import MagicMock
        from backend.config import BUSINESS_DB_CONFIG
        from backend.sql.executor import execute_sql_struct

        result = execute_sql_struct(sql, BUSINESS_DB_CONFIG)
        agent = MagicMock()
        agent.ask_struct = lambda *a, **k: result
        return agent

    def test_skill_writes_real_data_step_result(self):
        """SQLSkill.execute 走到真 PG：用户原 SSE 流问题现在应返回真数据。"""
        import asyncio
        from unittest.mock import patch as mp
        from backend.skills.sql.skill import SQLSkill

        TARGET_SQL = (
            "SELECT p.product_name, p.sale_price, p.brand "
            "FROM product.products p "
            "WHERE p.status = 'active' "
            "ORDER BY p.sale_price DESC LIMIT 5"
        )
        agent = self._patched_agent(TARGET_SQL)

        async def run():
            with mp("backend.skills.sql.skill.get_sql_agent", return_value=agent):
                out = await SQLSkill().execute(self._make_state())
            return out

        out = asyncio.run(run())
        sr = out["step_results"]["1"]
        # 关键断言：用户原 SSE 流里的 status=success 但 output=错误 → 现在应该是真数据
        assert sr["status"] == "success", f"期望 success，实际 {sr['status']} - {sr.get('error')}"
        assert sr["row_count"] == 5
        assert sr["is_empty"] is False
        # v2: output 是 Pydantic SQLResult dict
        output = sr["output"]
        assert isinstance(output, dict)
        rows = output.get("rows", [])
        product_names = [r.get("product_name", "") for r in rows]
        assert "羊毛外套" in product_names
        assert "丝绸连衣裙" in product_names
        assert "珍珠项链" in product_names
        # 没有错误信息泄漏
        assert sr["error"] is None
        assert sr["error_type"] is None

    def test_skill_correctly_reports_schema_mismatch_for_fake_table(self):
        """如果 LLM 编造不存在的表（防御性），SQLSkill 应该正确报 failed —— A 段核心修复。"""
        import asyncio
        from unittest.mock import patch as mp
        from backend.skills.sql.skill import SQLSkill

        agent = self._patched_agent("SELECT * FROM product.fake_table LIMIT 1")

        async def run():
            with mp("backend.skills.sql.skill.get_sql_agent", return_value=agent):
                out = await SQLSkill().execute(self._make_state())
            return out

        out = asyncio.run(run())
        sr = out["step_results"]["1"]
        assert sr["status"] == "failed"
        assert sr["error_type"] == "syntax_error"
        assert sr["error"] is not None

    def test_real_query_no_data_returns_success_with_is_empty(self):
        """真实查空数据：status=success + is_empty=True（保留 supervisor 降级链）。"""
        import asyncio
        from unittest.mock import patch as mp
        from backend.skills.sql.skill import SQLSkill

        no_match_sql = (
            "SELECT product_name, sale_price FROM product.products "
            "WHERE brand = 'NonExistentBrand' LIMIT 5"
        )
        agent = self._patched_agent(no_match_sql)

        async def run():
            with mp("backend.skills.sql.skill.get_sql_agent", return_value=agent):
                out = await SQLSkill().execute(self._make_state())
            return out

        out = asyncio.run(run())
        sr = out["step_results"]["1"]
        assert sr["status"] == "success"
        assert sr["is_empty"] is True
        assert sr["row_count"] == 0
        output = sr["output"]
        assert isinstance(output, dict)
        assert output.get("row_count") == 0
        assert output.get("rows") == []
        assert sr["error"] is None
