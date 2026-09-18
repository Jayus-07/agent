from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.audit.rag20k.eval_reproducibility import (
    PRIMARY_METRICS,
    compare_eval_runs,
)

_REQUIRED_CONTEXT = {
    "dataset": "expanded_100",
    "git_commit": "a" * 40,
    "model": "deepseek-chat",
    "embedding_model": "text-embedding-v3",
    "config_fingerprint": "cfg-001",
    "index_version": "idx-001",
}


def run(**overrides: object) -> dict:
    """构造合法评测报告；``recall`` 等短名或全名 kwargs 覆写主指标。"""
    aliases = {
        "recall": "recall@5",
        "top1": "top1_accuracy",
        "citation": "citation_accuracy",
        "reject": "reject_accuracy",
        "ndcg": "ndcg@10",
    }
    metrics = dict.fromkeys(PRIMARY_METRICS, 0.9)
    context = dict(_REQUIRED_CONTEXT)
    for key, value in overrides.items():
        metric_name = aliases.get(key, key if key in metrics else None)
        if metric_name is not None:
            metrics[metric_name] = value
        else:
            context[key] = value
    return {"context": context, "metrics": metrics}


def test_compare_rejects_different_dataset_or_config_fingerprint() -> None:
    first = compare_eval_runs(run(), run(dataset="v2"))
    second = compare_eval_runs(run(), run(config_fingerprint="cfg-002"))

    assert first.comparable is False
    assert second.comparable is False
    assert first.context_mismatches == ["dataset"]


def test_compare_requires_all_primary_metrics_within_0005() -> None:
    result = compare_eval_runs(run(recall=0.900), run(recall=0.906))

    assert result.passed is False
    assert result.metric_deltas["recall@5"] == pytest.approx(0.006)


def test_identical_runs_are_reproducible() -> None:
    result = compare_eval_runs(run(), run())

    assert result.comparable is True
    assert result.passed is True
    assert set(result.metric_deltas) == set(PRIMARY_METRICS)
    assert all(delta == 0 for delta in result.metric_deltas.values())


def test_missing_context_or_metric_fails(tmp_path: Path) -> None:
    broken_context = run()
    del broken_context["context"]["index_version"]
    first = compare_eval_runs(run(), broken_context)
    assert first.comparable is False
    assert "index_version" in first.context_mismatches

    missing_metric = run()
    del missing_metric["metrics"]["citation_accuracy"]
    second = compare_eval_runs(run(), missing_metric)
    assert second.comparable is True
    assert second.passed is False
    assert second.missing_metrics == ["citation_accuracy"]


def test_tolerance_boundary_is_inclusive() -> None:
    at_limit = compare_eval_runs(run(recall=0.5), run(recall=0.505))
    beyond = compare_eval_runs(run(recall=0.5), run(recall=0.5052))

    assert at_limit.passed is True
    assert beyond.passed is False


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    from backend.scripts.compare_rag_baselines import run as compare_cli

    good = tmp_path / "run-1.json"
    good.write_text(json.dumps(run(), ensure_ascii=False), encoding="utf-8")
    bad_recall = tmp_path / "run-2.json"
    bad_recall.write_text(json.dumps(run(recall=0.906), ensure_ascii=False), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    output = tmp_path / "reproducibility.json"

    assert compare_cli([str(good), str(good), "--output", str(output)]) == 0
    assert compare_cli([str(good), str(bad_recall), "--output", str(output)]) == 2
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False
    assert compare_cli([str(broken), str(good), "--output", str(output)]) == 1
    assert compare_cli([str(tmp_path / "missing.json"), str(good)]) == 1
