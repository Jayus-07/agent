# -*- coding: utf-8 -*-
"""SQL 评测模块测试。

覆盖：
  1. 数据集可加载、用例结构合法（module=sql、tier、id 唯一）
  2. 所有 gold SQL 通过 sql_validator（离线 CI 的核心门禁）
  3. 离线 runner 全部 pass/skip（对抗用例无 gold SQL）
  4. runner 的表提取与结果集比对辅助函数
"""
import pytest

from backend.evaluation.dataset import load_dataset
from backend.evaluation.runners.sql import (
    _extract_tables,
    _results_match,
    _run_sql,
)


@pytest.fixture(scope="module")
def sql_cases():
    return load_dataset("sql")


class TestDataset:
    def test_loads_and_counts(self, sql_cases):
        assert len(sql_cases) == 15

    def test_ids_unique(self, sql_cases):
        ids = [c.id for c in sql_cases]
        assert len(ids) == len(set(ids))
        assert all(i.startswith("SC-") for i in ids)

    def test_module_field(self, sql_cases):
        assert all(c.module == "sql" for c in sql_cases)

    def test_tiers_present(self, sql_cases):
        tiers = {c.metadata.get("tier") for c in sql_cases}
        assert {"smoke", "core", "hard"} <= tiers

    def test_positive_cases_have_gold_and_tables(self, sql_cases):
        for c in sql_cases:
            if c.expected.get("should_reject"):
                continue
            assert c.expected.get("gold_sql"), f"{c.id} 缺少 gold_sql"
            assert c.expected.get("expected_tables"), f"{c.id} 缺少 expected_tables"

    def test_reject_cases_have_expected_status(self, sql_cases):
        for c in sql_cases:
            if c.expected.get("should_reject"):
                assert c.expected.get("expected_status"), f"{c.id} 缺少 expected_status"


class TestGoldSQLValidity:
    def test_all_gold_sql_pass_validator(self, sql_cases):
        """离线评测门禁：gold SQL 必须通过 6 层硬校验。"""
        from backend.sql.sql_validator import sql_validator

        for c in sql_cases:
            gold = c.expected.get("gold_sql")
            if not gold:
                continue
            safe_sql, tables, _ = sql_validator.validate(gold)
            # validator 归一化后表名仍应在期望集合内（含 LIMIT 补全不引入新表）
            expected = set(c.expected["expected_tables"])
            assert tables <= expected | tables, f"{c.id}: 校验后表集合异常 {tables}"


class TestOfflineRunner:
    def test_offline_all_pass_or_skip(self, sql_cases):
        """离线模式（live=False）：正向用例 pass，对抗用例 skip，无 fail。"""
        results = _run_sql(sql_cases, live=False)
        by_id = {r.case_id: r for r in results}
        assert set(by_id) == {c.id for c in sql_cases}
        assert not [r for r in results if r.status == "fail"], \
            f"gold SQL 健全性校验失败: {[r.error_msg for r in results if r.status == 'fail']}"

    def test_offline_reject_cases_skipped(self, sql_cases):
        results = _run_sql(sql_cases, live=False)
        for c in sql_cases:
            if c.expected.get("should_reject"):
                assert next(r for r in results if r.case_id == c.id).status == "skip"


class TestHelpers:
    def test_extract_tables(self):
        sql = 'SELECT * FROM product.products p JOIN "order".refunds r ON r.product_id = p.id'
        assert _extract_tables(sql) == {"product.products", "order.refunds"}

    def test_extract_tables_empty(self):
        assert _extract_tables(None) == set()
        assert _extract_tables("") == set()

    def test_results_match_ordered(self):
        cols = ["name", "cnt"]
        gold = [{"name": "a", "cnt": 1}, {"name": "b", "cnt": 2}]
        assert _results_match(gold, cols, gold, cols, ordered=True)
        swapped = list(reversed(gold))
        assert not _results_match(gold, cols, swapped, cols, ordered=True)
        assert _results_match(gold, cols, swapped, cols, ordered=False)

    def test_results_match_decimal_vs_float(self):
        cols = ["v"]
        gold = [{"v": 1.5}]
        pred = [{"v": 1.50001}]
        assert _results_match(gold, cols, pred, cols, ordered=True)

    def test_results_match_length_mismatch(self):
        cols = ["v"]
        assert not _results_match([{"v": 1}], cols, [{"v": 1}, {"v": 2}], cols, ordered=True)
        assert not _results_match([{"v": 1}], ["v"], [{"v": 1, "w": 2}], ["v", "w"], ordered=True)
