"""test_sql_unmanaged_table_message.py — 「未纳管表」拒绝的对外语义

背景（2026-10-08）：validator 的 `table_forbidden` 覆盖三种输入——同库其它
子系统的表（`public.selection_tasks` 等）、模型编造的表名、系统表。原实现把
它和「敏感列/危险函数」一起压成「查询未通过安全校验，请调整问题后重试。」，
用户（尤其演示场景）会把「这类数据本就不由 SQL 助手纳管」误判成缺陷。

收口口径：
  - `table_forbidden` → status=validation_error / error_type=`table_not_managed`
    / 文案说明**能力边界**（7 个业务域），且不回显表名（沿用 policy.py 的
    「详细原因只进日志」纪律）
  - 其余校验拒绝（敏感列/危险函数/非 SELECT 等）文案与 error_type 不变
  - 真权限/数据域拒绝的文案不动（`该数据不在当前可访问范围内。`，
    见 tests/sql/test_sql_scope_internal.py 的断言）
"""
from __future__ import annotations

import pytest

from backend.sql import sql_agent as sql_agent_module
from backend.sql.sql_agent import SQLAgent


# ─────────────────────────────────────────────────────────────
# 映射层
# ─────────────────────────────────────────────────────────────

def test_table_forbidden_maps_to_unmanaged_message():
    result = sql_agent_module._validation_failure_result("table_forbidden")
    assert result.status == "validation_error"
    assert result.error_type == "table_not_managed"
    assert "不在 SQL 助手的可查询范围内" in (result.error or "")
    # 不回显表名（详细原因只进日志）
    assert "public" not in (result.error or "")
    assert "ai." not in (result.error or "")


@pytest.mark.parametrize("reason", ["sensitive_column", "dangerous_function", "non_select", ""])
def test_other_validator_reasons_keep_legacy_message(reason):
    result = sql_agent_module._validation_failure_result(reason)
    assert result.status == "validation_error"
    assert result.error_type == "validation"
    assert result.error == "查询未通过安全校验，请调整问题后重试。"


# ─────────────────────────────────────────────────────────────
# 端到端（真实 validator，只替换 router/generator 两个外部边界）
# ─────────────────────────────────────────────────────────────

def _agent() -> SQLAgent:
    return SQLAgent(db_config={}, max_retries=0)


def test_undeclared_table_from_generator_gets_capability_message(monkeypatch):
    """模型生成 public.* 表（同库真实存在，但不归 SQL Agent 管）。"""
    monkeypatch.setattr(
        sql_agent_module, "select_tables",
        lambda question, **kwargs: ["product.products"],
    )
    monkeypatch.setattr(
        sql_agent_module, "generate_sql",
        lambda question, tables, feedback=None: "SELECT * FROM public.selection_tasks",
    )

    result = _agent().ask_struct("选品任务的分数是多少")

    assert result.status == "validation_error"
    assert result.error_type == "table_not_managed"
    assert "不在 SQL 助手的可查询范围内" in (result.error or "")


def test_declared_table_still_passes_validation(monkeypatch):
    """反向守卫：正常纳管表不得被新文案误伤（走真实 validator）。"""
    monkeypatch.setattr(
        sql_agent_module, "select_tables",
        lambda question, **kwargs: ["product.products"],
    )
    monkeypatch.setattr(
        sql_agent_module, "generate_sql",
        lambda question, tables, feedback=None: "SELECT id, sku FROM product.products",
    )
    monkeypatch.setattr(
        sql_agent_module, "execute_sql_struct",
        lambda sql, db_config, params=None: sql_agent_module.SQLResult.success(
            rows=[{"id": 1, "sku": "SQDEMO-001"}], columns=["id", "sku"], sql=sql
        ),
    )

    result = _agent().ask_struct("列出商品编码")

    assert result.status == "success"
    assert result.row_count == 1
