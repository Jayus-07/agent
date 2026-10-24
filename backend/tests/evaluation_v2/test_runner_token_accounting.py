from types import SimpleNamespace

import pytest

from backend.evaluation.metrics.retrieval import mrr, ndcg_at_k, recall_at_k
from backend.evaluation_v2 import repository
from backend.evaluation_v2.runner import _case_result_payload


def test_case_result_persists_answer_generation_token_usage():
    result = SimpleNamespace(
        actual={"trace_id": "trace-1", "trace": {"prompt_tokens": 123, "completion_tokens": 45}},
        metrics={"recall@5": 0.8},
        case_id="d21af908-53ce-4a8e-8eae-5391a7c7416a",
        status="pass",
        error_stage=None,
        duration_ms=250,
    )

    payload = _case_result_payload(result, "evaluation-run:run-1")

    assert payload["scores"] == {
        "recall@5": 0.8,
        "answer_generation_prompt_tokens": 123,
        "answer_generation_completion_tokens": 45,
        "answer_generation_total_tokens": 168,
    }
    assert payload["trace_id"] == "trace-1"
    assert payload["artifact_uri"] == "evaluation-run:run-1"


def test_case_result_does_not_fabricate_token_usage_when_trace_is_missing_or_invalid():
    for trace in (
        None,
        {"prompt_tokens": True, "completion_tokens": 2},
        {"prompt_tokens": 1, "completion_tokens": -2},
        {"prompt_tokens": 3},
    ):
        result = SimpleNamespace(
            actual={"trace": trace},
            metrics={"recall@5": 0.8},
            case_id="d21af908-53ce-4a8e-8eae-5391a7c7416a",
            status="pass",
            error_stage=None,
            duration_ms=250,
        )

        payload = _case_result_payload(result, "evaluation-run:run-1")

        assert payload["scores"] == {"recall@5": 0.8}


def test_v2_runner_and_compare_keep_zero_hit_qrels_in_macro_denominator(monkeypatch):
    qrels = {
        "case-hit": {"actual": ["doc-1", "doc-2"], "expected": ["doc-1", "doc-3"]},
        "case-no-hit": {"actual": [], "expected": ["doc-9"]},
    }

    def persisted_metrics(case_id, actual):
        expected = qrels[case_id]["expected"]
        result = SimpleNamespace(
            actual={},
            metrics={
                "recall@5": recall_at_k(actual, expected, 5),
                "mrr": mrr(actual, expected),
                "ndcg@10": ndcg_at_k(actual, expected, 10),
            },
            case_id=case_id,
            status="pass",
            error_stage=None,
            duration_ms=1,
        )
        return _case_result_payload(result, "evaluation-run:synthetic-qrels")

    baseline = {
        "case-hit": persisted_metrics("case-hit", ["doc-1", "doc-2"]),
        "case-no-hit": persisted_metrics("case-no-hit", []),
    }
    candidate = {
        "case-hit": persisted_metrics("case-hit", ["doc-3", "doc-1"]),
        "case-no-hit": persisted_metrics("case-no-hit", []),
    }
    expected_snapshot = {
        "dataset_hash": "fixed-qrels-hash",
        "cases": [{"case_id": case_id, "slice_tags": {}} for case_id in qrels],
    }
    runs = {
        run_id: {
            "run_id": run_id,
            "suite_version_id": "suite-v1",
            "suite_hash": "suite-hash",
            "corpus_hash": "corpus-hash",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "snapshot_json": expected_snapshot,
        }
        for run_id in ("baseline", "candidate")
    }
    results = {
        run_id: [
            {
                "case_id": case_id,
                "result_status": payload["status"],
                "scores_json": payload["scores"],
            }
            for case_id, payload in scores.items()
        ]
        for run_id, scores in (("baseline", baseline), ("candidate", candidate))
    }
    monkeypatch.setattr(repository, "get_run", lambda _tenant, run_id: runs[run_id])
    monkeypatch.setattr(repository, "list_run_results", lambda _tenant, run_id, **_kwargs: results[run_id])

    comparison = repository.compare_runs("tenant-a", "baseline", "candidate")
    metrics = {item["key"]: item for item in comparison["metrics"]}

    assert baseline["case-no-hit"]["scores"] == {
        "recall@5": 0.0,
        "mrr": 0.0,
        "ndcg@10": 0.0,
    }
    assert metrics["recall@5"]["baseline"] == 0.25
    assert metrics["recall@5"]["candidate"] == 0.5
    assert metrics["recall@5"]["sample_count"] == 2
    assert metrics["mrr"]["baseline"] == 0.5
    assert metrics["mrr"]["candidate"] == 0.5
    assert metrics["mrr"]["sample_count"] == 2
    assert metrics["ndcg@10"]["baseline"] == pytest.approx(0.306574, abs=1e-6)
    assert metrics["ndcg@10"]["candidate"] == 0.5
    assert metrics["ndcg@10"]["sample_count"] == 2
