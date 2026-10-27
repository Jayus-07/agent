from contextlib import contextmanager
from types import SimpleNamespace

from backend.app.api.routes.evaluation_v2 import _safe_slice_tags
from backend.evaluation_v2 import repository


@contextmanager
def _fake_question_transaction():
    """compare_runs 只用事务回查问题文本；CI 无 PG 服务，返回空问题表。"""
    yield None, SimpleNamespace(execute=lambda *_args, **_kwargs: None, fetchall=lambda: [])


def test_run_slice_tags_keep_only_supported_redacted_labels():
    tags = _safe_slice_tags({
        "category": "制度问答",
        "scenario": "拨打 13800138000 咨询",
        "risk_level": "high",
        "difficulty": "easy",
        "owner_email": "user@example.com",
        "notes": "不要写入 Run 快照",
    })

    assert tags == {
        "category": "制度问答",
        "scenario": "拨打 [PHONE_REDACTED] 咨询",
        "risk_level": "high",
        "difficulty": "easy",
    }


def test_compare_runs_aggregates_paired_case_slices(monkeypatch):
    case_ids = ["case-1", "case-2", "case-3"]
    snapshot = {
        "dataset_hash": "dataset-hash",
        "cases": [
            {"case_id": "case-1", "slice_tags": {"category": "faq", "risk_level": "high"}},
            {"case_id": "case-2", "slice_tags": {"category": "faq", "risk_level": "high"}},
            {"case_id": "case-3", "slice_tags": {"category": "table", "risk_level": "low"}},
        ],
    }
    runs = {
        "baseline": {
            "run_id": "baseline",
            "suite_version_id": "suite-version",
            "suite_hash": "suite-hash",
            "corpus_hash": "corpus-hash",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "snapshot_json": snapshot,
        },
        "candidate": {
            "run_id": "candidate",
            "suite_version_id": "suite-version",
            "suite_hash": "suite-hash",
            "corpus_hash": "corpus-hash",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "snapshot_json": snapshot,
        },
    }
    results = {
        "baseline": [
            {"case_id": case_ids[0], "result_status": "pass", "scores_json": {"accuracy": 0.2}},
            {"case_id": case_ids[1], "result_status": "fail", "scores_json": {"accuracy": 0.8}},
            {"case_id": case_ids[2], "result_status": "pass", "scores_json": {"accuracy": 1.0}},
        ],
        "candidate": [
            {"case_id": case_ids[0], "result_status": "pass", "scores_json": {"accuracy": 0.4}},
            {"case_id": case_ids[1], "result_status": "pass", "scores_json": {"accuracy": 0.9}},
            {"case_id": case_ids[2], "result_status": "fail", "scores_json": {"accuracy": 0.7}},
        ],
    }
    monkeypatch.setattr(repository, "get_run", lambda _tenant, run_id: runs[run_id])
    monkeypatch.setattr(repository, "list_run_results", lambda _tenant, run_id, **_kwargs: results[run_id])
    monkeypatch.setattr(repository, "_transaction", lambda _tenant: _fake_question_transaction())

    comparison = repository.compare_runs("tenant-a", "baseline", "candidate")

    faq = next(item for item in comparison["slices"] if item["field"] == "category" and item["value"] == "faq")
    assert faq["sample_count"] == 2
    assert faq["baseline_pass_rate"] == 0.5
    assert faq["candidate_pass_rate"] == 1.0
    assert faq["pass_rate_delta"] == 0.5
    assert faq["pass_rate_inference"]["eligible"] is False
    assert faq["pass_rate_inference"]["reason"] == "minimum_sample_count"
    assert faq["metrics"] == [{
        "key": "accuracy",
        "baseline": 0.5,
        "candidate": 0.65,
        "delta": 0.15,
        "sample_count": 2,
    }]
    assert comparison["slices_truncated"] == 0
    assert comparison["pass_rate_inference"] == {
        "sample_count": 3,
        "excluded_count": 0,
        "discordant_count": 2,
        "candidate_only_pass": 1,
        "baseline_only_pass": 1,
        "pass_rate_delta": 0.0,
        "ci95_lower": -0.54062506,
        "ci95_upper": 0.54062506,
        "p_value": 1.0,
        "p_value_method": "exact_mcnemar_two_sided",
        "confidence_interval_method": "paired_wilson_95",
    }


def test_slice_inference_uses_minimum_sample_and_holm_adjustment(monkeypatch):
    case_refs = []
    baseline_results = []
    candidate_results = []
    for index in range(40):
        case_id = f"case-{index:02d}"
        category = "faq" if index < 20 else "policy"
        case_refs.append({"case_id": case_id, "slice_tags": {"category": category}})
        baseline_status = "fail" if index < 10 else "pass"
        candidate_status = "pass"
        if index >= 20:
            baseline_status = "fail" if index == 20 else "pass"
            candidate_status = "pass"
        baseline_results.append({"case_id": case_id, "result_status": baseline_status, "scores_json": {}})
        candidate_results.append({"case_id": case_id, "result_status": candidate_status, "scores_json": {}})

    snapshot = {"dataset_hash": "dataset-hash", "cases": case_refs}
    runs = {
        slot: {
            "run_id": slot,
            "suite_version_id": "suite-version",
            "suite_hash": "suite-hash",
            "corpus_hash": "corpus-hash",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "snapshot_json": snapshot,
        }
        for slot in ("baseline", "candidate")
    }
    results = {"baseline": baseline_results, "candidate": candidate_results}
    monkeypatch.setattr(repository, "get_run", lambda _tenant, run_id: runs[run_id])
    monkeypatch.setattr(repository, "list_run_results", lambda _tenant, run_id, **_kwargs: results[run_id])
    monkeypatch.setattr(repository, "_transaction", lambda _tenant: _fake_question_transaction())

    comparison = repository.compare_runs("tenant-a", "baseline", "candidate")

    faq = next(item for item in comparison["slices"] if item["value"] == "faq")
    policy = next(item for item in comparison["slices"] if item["value"] == "policy")
    assert faq["pass_rate_inference"]["eligible"] is True
    assert faq["pass_rate_inference"]["sample_count"] == 20
    assert faq["pass_rate_inference"]["adjusted_p_value"] == 0.00390625
    assert faq["pass_rate_inference"]["family_size"] == 2
    assert policy["pass_rate_inference"]["adjusted_p_value"] == 1.0
