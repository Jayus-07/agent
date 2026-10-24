from backend.evaluation_v2 import repository
from backend.evaluation_v2.statistics import (
    holm_adjust_p_values,
    paired_binary_inference,
)


def test_holm_adjustment_controls_family_error_rate():
    assert holm_adjust_p_values([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]


def test_holm_adjustment_rejects_invalid_p_values():
    import pytest

    with pytest.raises(ValueError, match="p 值必须"):
        holm_adjust_p_values([0.01, float("nan")])


def test_paired_binary_inference_reports_exact_mcnemar_and_wilson_interval():
    result = paired_binary_inference([
        {"baseline": "fail", "candidate": "pass"}
        for _ in range(10)
    ])

    assert result["candidate_only_pass"] == 10
    assert result["baseline_only_pass"] == 0
    assert result["pass_rate_delta"] == 1.0
    assert result["p_value"] == 0.001953125
    assert result["ci95_lower"] > 0
    assert result["ci95_upper"] == 1.0
    assert result["p_value_method"] == "exact_mcnemar_two_sided"
    assert result["confidence_interval_method"] == "paired_wilson_95"


def test_paired_binary_inference_excludes_non_binary_statuses():
    result = paired_binary_inference([
        {"baseline": "pass", "candidate": "pass"},
        {"baseline": "error", "candidate": "pass"},
        {"baseline": "fail", "candidate": "skip"},
    ])

    assert result["sample_count"] == 1
    assert result["excluded_count"] == 2
    assert result["discordant_count"] == 0
    assert result["p_value"] == 1.0
    assert result["ci95_lower"] is None
    assert result["ci95_upper"] is None


def test_compare_different_dataset_versions_returns_incomparable_without_delta(monkeypatch):
    runs = {
        "baseline": {
            "run_id": "baseline",
            "suite_version_id": "suite-v1",
            "suite_hash": "suite-hash",
            "corpus_hash": "corpus-hash",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "snapshot_json": {"dataset_hash": "dataset-v1", "cases": []},
        },
        "candidate": {
            "run_id": "candidate",
            "suite_version_id": "suite-v1",
            "suite_hash": "suite-hash",
            "corpus_hash": "corpus-hash",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "snapshot_json": {"dataset_hash": "dataset-v2", "cases": []},
        },
    }
    monkeypatch.setattr(repository, "get_run", lambda _tenant, run_id: runs[run_id])
    monkeypatch.setattr(repository, "list_run_results", lambda *_args, **_kwargs: [])

    comparison = repository.compare_runs("tenant-a", "baseline", "candidate")

    assert comparison["comparable"] is False
    assert comparison["reasons"] == ["Golden 数据集版本不一致"]
    assert comparison["metrics"] == []
    assert comparison["cases"] == []
    assert comparison["pass_rate_inference"] is None
