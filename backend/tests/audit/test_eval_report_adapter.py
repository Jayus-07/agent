from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

import pytest

from backend.audit.rag20k.eval_report_adapter import (
    build_comparison_input,
)
def _report(n_cases: int = 4, *, with_reject: bool = True) -> dict:
    results = []
    for i in range(n_cases):
        metrics = {"recall@5": 0.8 + i * 0.01, "mrr": 0.7, "ndcg@10": 0.75}
        if with_reject and i == 0:
            metrics["reject_accuracy"] = 1.0
        results.append({"case_id": f"g-{i:04d}", "metrics": metrics})
    return {
        "metadata": {
            "evaluation_scope": {"kb_id": "rag_eval_kb", "fixture_set": "expanded_100"},
            "dataset_version": "v4",
            "selection": "expanded_100",
            "run_id": "run-x",
        },
        "prompt_versions": {"rag.qa": 2},
        "results": results,
    }


def _meta() -> dict:
    return {
        "git_sha": "a" * 40,
        "dataset_version": {"rag": "v4"},
        "prompt_versions": {"rag.qa": 2},
    }


def test_builds_context_and_mean_metrics() -> None:
    run = build_comparison_input(
        _report(),
        _meta(),
        model="deepseek-chat",
        embedding_model="text-embedding-v3",
        index_version="idx-001",
    )

    assert run["context"]["dataset"] == "expanded_100@v4"
    assert run["context"]["git_commit"] == "a" * 40
    assert run["context"]["model"] == "deepseek-chat"
    assert run["context"]["index_version"] == "idx-001"
    # recall@5 均值 = (0.80+0.81+0.82+0.83)/4
    assert run["metrics"]["recall@5"] == pytest.approx(0.815)
    assert run["metrics"]["mrr"] == pytest.approx(0.7)
    # 未在任何 case 出现的主指标不得伪造为 0
    assert "top1_accuracy" not in run["metrics"]
    assert run["context"]["missing_primary_metrics"] == ["top1_accuracy", "citation_accuracy"]


def test_results_fingerprint_changes_when_case_errors() -> None:
    """任一 case 出错（指标缺席集合变化）都会改变结果指纹 → 双跑大声不可比。"""
    first = build_comparison_input(
        _report(), _meta(), model="m", embedding_model="e", index_version="i"
    )
    broken = _report()
    broken["results"][2]["metrics"] = {}
    second = build_comparison_input(
        broken, _meta(), model="m", embedding_model="e", index_version="i"
    )

    assert first["context"]["results_fingerprint"] != second["context"]["results_fingerprint"]


def test_missing_prerequisites_are_rejected() -> None:
    meta = _meta()
    del meta["git_sha"]
    with pytest.raises(ValueError, match="git_sha"):
        build_comparison_input(
            _report(), meta, model="m", embedding_model="e", index_version="i"
        )

    with pytest.raises(ValueError, match="index_version"):
        build_comparison_input(
            _report(), _meta(), model="m", embedding_model="e", index_version=""
        )

    inconsistent_meta = _meta()
    inconsistent_meta["dataset_version"]["rag"] = "v3"
    with pytest.raises(ValueError, match="dataset_version"):
        build_comparison_input(
            _report(),
            inconsistent_meta,
            model="m",
            embedding_model="e",
            index_version="i",
        )


def test_fixture_set_fallback_when_selection_missing() -> None:
    """rag 报告以 evaluation_scope.fixture_set 承载套件名，必须可作为 dataset 身份。"""
    report = _report()
    del report["metadata"]["selection"]

    run = build_comparison_input(
        report, _meta(), model="m", embedding_model="e", index_version="i"
    )

    assert run["context"]["dataset"] == "expanded_100@v4"


def test_run_id_must_not_leak_into_config_fingerprint() -> None:
    """run_id 是每次运行的身份，不是口径——两次运行仅 run_id 不同仍须可比。"""
    kwargs = dict(model="m", embedding_model="e", index_version="i")
    first = build_comparison_input(_report(), _meta(), **kwargs)
    second_report = _report()
    second_report["metadata"]["run_id"] = "run-other"
    second = build_comparison_input(second_report, _meta(), **kwargs)

    assert (
        first["context"]["config_fingerprint"]
        == second["context"]["config_fingerprint"]
    )


def test_non_finite_primary_values_are_excluded() -> None:
    """拒答类 case 的 recall 等是 NaN（0/0），必须按缺席处理而不是毒化均值。"""
    report = _report()
    report["results"][1]["metrics"]["recall@5"] = float("nan")

    run = build_comparison_input(
        report, _meta(), model="m", embedding_model="e", index_version="i"
    )

    assert run["metrics"]["recall@5"] == pytest.approx((0.8 + 0.82 + 0.83) / 3)
    assert run["context"]["case_count"] == 4


def test_context_is_deterministic_for_identical_inputs() -> None:
    kwargs = dict(model="m", embedding_model="e", index_version="i")
    first = build_comparison_input(_report(), _meta(), **kwargs)
    second = build_comparison_input(_report(), _meta(), **kwargs)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert len(first["context"]["config_fingerprint"]) == 64
