# -*- coding: utf-8 -*-
"""SQL 评测模块测试。

覆盖：
  1. 数据集可加载、用例结构合法（module=sql、tier、id 唯一）
  2. 所有 gold SQL 通过 sql_validator（离线 CI 的核心门禁）
  3. 离线 runner 全部 pass/skip（对抗用例无 gold SQL）
  4. runner 的表提取与结果集比对辅助函数
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.evaluation.dataset import load_dataset
from backend.evaluation.models import TestCase as EvaluationCase
from backend.evaluation.runners.sql import (
    _eval_positive_case,
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

    def test_results_match_decimal_and_float_only_when_numerically_equal(self):
        cols = ["v"]
        gold = [{"v": Decimal("1.5000")}]
        assert _results_match(gold, cols, [{"v": 1.5}], cols, ordered=True)
        assert not _results_match(
            gold, cols, [{"v": 1.50001}], cols, ordered=True,
        )

    def test_results_match_preserves_large_decimal_precision(self):
        cols = ["amount"]
        gold = [{"amount": Decimal("123456789012345.12")}]
        different = [{"amount": Decimal("123456789012345.13")}]

        assert not _results_match(gold, cols, different, cols, ordered=True)

    def test_results_match_distinguishes_null_from_empty_string(self):
        cols = ["value"]

        assert _results_match([{"value": None}], cols, [{"value": None}], cols, True)
        assert not _results_match([{"value": None}], cols, [{"value": ""}], cols, True)

    def test_results_match_length_mismatch(self):
        cols = ["v"]
        assert not _results_match([{"v": 1}], cols, [{"v": 1}, {"v": 2}], cols, ordered=True)
        assert not _results_match([{"v": 1}], ["v"], [{"v": 1, "w": 2}], ["v", "w"], ordered=True)


@pytest.mark.parametrize(
    ("failure", "expected_category", "expected_validity"),
    [
        ("bad_gold", "dataset", "INVALID_DATASET"),
        ("permission", "environment", "INVALID_ENV"),
        ("database", "infrastructure", "INVALID_INFRA"),
    ],
)
def test_gold_sql_failure_cannot_fall_back_to_router_pass(
    monkeypatch, failure, expected_category, expected_validity,
):
    from backend.evaluation.validity import classify_report_validity
    from backend.sql import executor
    from backend.sql.sql_validator import ValidationError, sql_validator

    case = EvaluationCase(
        id="SC-GOLD-FAIL",
        question="列出商品",
        module="sql",
        expected={
            "gold_sql": "SELECT product_name FROM product.products",
            "expected_tables": ["product.products"],
            "should_reject": False,
        },
    )
    generated = SimpleNamespace(
        status="success",
        sql_text="SELECT product_name FROM product.products",
        rows=[{"product_name": "测试商品"}],
        columns=["product_name"],
        row_count=1,
        error=None,
    )

    if failure == "bad_gold":
        def reject_gold(_sql):
            raise ValidationError("gold_sql validation failed", layer=1)

        monkeypatch.setattr(sql_validator, "validate", reject_gold)
    else:
        monkeypatch.setattr(
            sql_validator,
            "validate",
            lambda _sql: (
                "SELECT product_name FROM product.products",
                {"product.products"},
                None,
            ),
        )
        status, error_type, error = {
            "permission": (
                "permission_denied", "privilege", "permission denied",
            ),
            "database": ("failed", "connection", "connection refused"),
        }[failure]
        monkeypatch.setattr(
            executor,
            "execute_sql_struct",
            lambda *_args: SimpleNamespace(
                status=status, error_type=error_type, error=error,
            ),
        )

    evaluated = _eval_positive_case(case, generated, case.expected)

    assert evaluated.status == "error"
    assert evaluated.error_stage == "sql_gold"
    assert evaluated.metrics["execution_match"] is None
    assert evaluated.actual["gold_failure_category"] == expected_category
    verdict = classify_report_validity(SimpleNamespace(
        module="sql",
        mode="live",
        metadata={},
        results=[evaluated],
    ))
    assert verdict.validity.value == expected_validity


def test_sql_v5_candidate_is_loadable_and_schema_valid():
    from backend.evaluation.dataset import load_dataset_file
    from backend.evaluation.preflight import run_schema_preflight

    filename = "sql_v5/cases.jsonl"
    cases = load_dataset_file(filename, default_module="sql")
    preflight = run_schema_preflight(["sql"], dataset_path=filename)

    assert len(cases) == 45
    assert {case.module for case in cases} == {"sql"}
    assert {case.metadata["dataset_version"] for case in cases} == {"5.0"}
    assert preflight.passed
    assert preflight.identity["case_counts"] == {"sql": 45}


def test_explicit_sql_dataset_uses_sql_runner_and_records_version(monkeypatch):
    from backend.evaluation import service
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.dataset.loader import DATASET_DIR, file_content_hash
    from backend.evaluation.models import EvalResult

    dispatched = {}

    def fake_run_module(module, cases, **kwargs):
        dispatched["module"] = module
        dispatched["case_ids"] = [case.id for case in cases]
        dispatched["live"] = kwargs["live"]
        return [
            EvalResult(
                case_id=case.id,
                module=module,
                status="skip",
                expected=case.expected,
                actual={"skip_code": "test_dispatch_only"},
            )
            for case in cases
        ]

    monkeypatch.setattr(service, "_run_module", fake_run_module)
    monkeypatch.setattr(service, "_inject_token_totals", lambda *_args: None)
    monkeypatch.setattr(service, "_attach_provenance", lambda _config, report: report)

    report = service.EvaluationService()._evaluate_cases(
        EvalConfig(
            module="sql",
            dataset="sql_v5/cases.jsonl",
            no_ragas=True,
        ),
        "sql-candidate-dispatch-test",
        0,
    )

    assert dispatched["module"] == "sql"
    assert len(dispatched["case_ids"]) == 45
    assert dispatched["live"] is False
    assert report.module == "sql"
    assert report.metadata["dataset_file"] == "sql_v5/cases.jsonl"
    assert report.metadata["dataset_version"] == "5.0"
    assert report.metadata["dataset_content_hash"] == file_content_hash(
        DATASET_DIR / "sql_v5" / "cases.jsonl",
    )


def test_explicit_versioned_dataset_rejects_manifest_hash_drift(tmp_path, monkeypatch):
    import json

    from backend.evaluation.dataset import loader

    candidate_dir = tmp_path / "sql_v3"
    candidate_dir.mkdir()
    (candidate_dir / "cases.jsonl").write_text(
        '{"id":"SC-TEST","question":"测试","module":"sql",'
        '"expected":{"gold_sql":"SELECT 1"}}\n',
        encoding="utf-8",
    )
    (candidate_dir / "manifest.json").write_text(
        json.dumps({"module": "sql", "content_hash": "0000000000000000"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "DATASET_DIR", tmp_path)

    with pytest.raises(ValueError, match="内容校验失败"):
        loader.load_dataset_file("sql_v3/cases.jsonl", default_module="sql")
