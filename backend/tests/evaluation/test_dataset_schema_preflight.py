"""全模块数据集契约与付费 Runner 前置校验。"""

from __future__ import annotations

import pytest

from backend.evaluation.dataset import load_dataset, validate_dataset
from backend.evaluation.models import MODULE_KINDS
from backend.evaluation.models import TestCase as EvaluationCase


def test_all_registered_canonical_datasets_pass_schema_validation():
    for module in MODULE_KINDS:
        cases = load_dataset(module)
        errors = validate_dataset(cases, expected_module=module)
        assert cases, f"{module} 数据集为空"
        assert errors == [], f"{module}: {errors}"


@pytest.mark.parametrize(
    "module,query_type",
    [("sql", "join_aggregate"), ("e2e", "direct_single_skill_sql")],
)
def test_non_rag_query_types_are_module_specific(module, query_type):
    expected = (
        {"gold_sql": "SELECT 1", "expected_tables": ["public.test"]}
        if module == "sql"
        else {}
    )
    case = EvaluationCase(
        id="domain-query-type",
        question="检查模块专属查询类型",
        module=module,
        expected=expected,
        metadata={"query_type": query_type},
    )

    assert validate_dataset([case]) == []


def test_rag_query_type_still_uses_rag_taxonomy():
    case = EvaluationCase(
        id="invalid-rag-query-type",
        question="检查 RAG 查询类型",
        module="rag",
        metadata={"query_type": "join_aggregate"},
    )

    assert any("invalid RAG query_type" in error for error in validate_dataset([case]))


@pytest.mark.parametrize(
    "query_type",
    [
        "single_doc",
        "multi_doc",
        "single_hop",
        "single_hop_specific",
        "multi_hop",
        "multi_hop_abstract",
        "multi_hop_specific",
        "table",
        "procedural",
        "no_evidence",
        "permission_denied",
        "negative",
        "adversarial",
        "ambiguous",
        "version_conflict",
        "chunk_level",
        "long_doc",
        "comparative",
        "aggregation",
        "conditional_reasoning",
        "negation_exclusion",
        "implicit_condition",
    ],
)
def test_rag_validator_accepts_every_managed_upload_query_type(query_type):
    from typing import get_args

    from backend.evaluation.dataset.query_types import RagQueryType

    case = EvaluationCase(
        id=f"managed-{query_type}",
        question="检查托管题集查询类型",
        module="rag",
        expected={"should_reject": query_type in {"no_evidence", "permission_denied"}},
        metadata={"query_type": query_type},
    )

    assert query_type in get_args(RagQueryType)
    assert validate_dataset([case]) == []


@pytest.mark.parametrize("source", ["trace", "unanswered_question", "ragas_synthesis"])
def test_curator_source_contract_accepts_existing_provenance_values(source):
    case = EvaluationCase(
        id=f"source-{source}",
        question="检查候选来源",
        module="rag",
        metadata={"source": source},
    )

    assert validate_dataset([case]) == []


@pytest.mark.parametrize(
    "expected,fragment",
    [
        ({"should_reject": False, "expected_tables": ["public.test"]}, "gold_sql"),
        (
            {"should_reject": True, "expected_tables": [], "expected_status": [""]},
            "expected_status",
        ),
        (
            {"should_reject": "true", "expected_tables": [], "expected_status": ["no_table"]},
            "should_reject",
        ),
    ],
)
def test_sql_contract_rejects_incomplete_or_mistyped_cases(expected, fragment):
    case = EvaluationCase(
        id="bad-sql-contract",
        question="检查 SQL 数据契约",
        module="sql",
        expected=expected,
    )

    assert any(fragment in error for error in validate_dataset([case]))


def test_malformed_metadata_types_report_errors_without_crashing():
    case = EvaluationCase(
        id="bad-metadata-types",
        question="检查元数据类型",
        module="e2e",
        metadata={"answer_type": [], "query_type": {}, "tier": [], "source": {}},
    )

    errors = validate_dataset([case])
    assert any("answer_type must be a string" in error for error in errors)
    assert any("query_type must be a string" in error for error in errors)
    assert any("tier must be a string" in error for error in errors)
    assert any("source must be a string" in error for error in errors)


def test_malformed_ground_truth_reports_errors_without_crashing():
    case = EvaluationCase(
        id="bad-ground-truth",
        question="检查真值类型",
        module="rag",
        expected={"ground_truth_context": [{"text": 123}]},
    )

    errors = validate_dataset([case])

    assert any("ground_truth_context[0].text must be a non-empty string" in error for error in errors)


def test_curator_does_not_write_an_invalid_case(tmp_path):
    from backend.evaluation.curator import append_case

    case = EvaluationCase(
        id="invalid-sql-case",
        question="缺少 gold SQL 的正向题",
        module="sql",
        expected={"should_reject": False, "expected_tables": ["public.test"]},
    )

    result = append_case(case, dataset_dir=tmp_path)

    assert result["appended"] is False
    assert result["reason"].startswith("数据集校验失败")
    assert not (tmp_path / "sql" / "cases.jsonl").exists()


def test_trace_bridge_case_can_be_promoted_into_dataset(tmp_path):
    from backend.evaluation.curator import append_case
    from backend.evaluation.trace_bridge import build_test_case_from_trace

    case = build_test_case_from_trace({
        "id": "trace-1",
        "question": "请说明退货政策",
        "answer_preview": "请查看退货政策。",
        "workflow_name": "rag_agent",
    })

    assert validate_dataset([case]) == []
    assert append_case(case, dataset_dir=tmp_path)["appended"] is True


def test_all_modules_are_validated_before_any_runner(monkeypatch):
    from backend.evaluation import dataset, service
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.preflight import PreflightError

    real_loader = dataset.load_dataset

    def load_with_invalid_sql(module, selection=None):
        cases = real_loader(module, selection=selection)
        if module == "sql":
            target = next(case for case in cases if case.id == "SC-001")
            return [
                target.model_copy(
                    update={"expected": {"should_reject": False, "expected_tables": ["product.products"]}}
                ),
                *[case for case in cases if case.id != "SC-001"],
            ]
        return cases

    monkeypatch.setattr(dataset, "load_dataset", load_with_invalid_sql)
    monkeypatch.setattr(service.EvaluationService, "_ensure_runners", lambda self: None)
    monkeypatch.setattr(service, "_run_module", lambda *args, **kwargs: pytest.fail("Runner 不得启动"))
    monkeypatch.setattr("backend.evaluation.run_guards.BUDGET_BLOCK_ENABLED", False)

    with pytest.raises(PreflightError, match="dataset_schema"):
        service.EvaluationService()._evaluate(EvalConfig(module="all", live=True))
