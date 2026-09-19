"""元数据发布门禁：指标缺失时 fail closed，诊断一致率不冒充准确率。"""
from __future__ import annotations

from backend.eval.metadata_baseline.evaluate import (
    abstain_rate,
    build_release_report,
    coverage,
    expected_calibration_error,
)
from backend.eval.metadata_baseline.validate_release import validate_release
from backend.eval.metadata_baseline.validate_release import (
    _load_metric_gate,
    _rollback_gate,
)


def _valid_load_report() -> dict:
    return {
        "report_version": "metadata-load-v1",
        "peak_multiplier": 2.0,
        "sustained_queue_growth": False,
        "queue_age_p95_seconds": 2.4,
        "primary_p95_ms": 420.0,
        "shadow_p95_ms": 390.0,
        "embedding_qps": 18.0,
        "llm_qps": 6.0,
        "llm_429_rate": 0.0,
        "db_pool_wait_p95_ms": 12.0,
        "duplicate_write_count": 0,
    }


def _valid_rollback_report() -> dict:
    return {
        "report_version": "metadata-rollback-v1",
        "rollback_duration_seconds": 42.0,
        "old_fingerprint_present": True,
        "idempotent_replay": True,
        "duplicate_write_count": 0,
    }


def test_incumbent_agreement_is_not_accuracy_gate():
    gold_rows = [{"id": "1", "text": "t", "doc_type_gold": "legal"}]
    prediction_rows = [{
        "id": "1",
        "pred": {"doc_type": "policy", "decision": "accepted", "confidence": 0.9},
        "incumbent_pred": "policy",
    }]

    report = build_release_report(gold_rows, prediction_rows)

    assert report["accuracy"] == 0.0
    assert report["incumbent_agreement"] == 1.0
    assert report["gates"]["accuracy"]["passed"] is False


def test_r0_precision_gate_blocks_when_one_of_200_is_wrong():
    gold_rows = [
        {"id": str(i), "text": "t", "doc_type_gold": "legal"}
        for i in range(200)
    ]
    prediction_rows = [
        {
            "id": str(i),
            "pred": {
                "doc_type": "legal" if i < 199 else "policy",
                "route_source": "r0",
                "confidence": 0.999,
                "decision": "accepted",
            },
        }
        for i in range(200)
    ]

    report = build_release_report(gold_rows, prediction_rows)

    assert report["gates"]["r0_precision"]["passed"] is False
    assert report["gates"]["r0_precision"]["actual"] == 0.995


def test_abstain_counts_as_coverage_loss_not_silent_general():
    rows = [{
        "id": "1",
        "text": "t",
        "doc_type_gold": "legal",
        "pred": {"doc_type": "general", "decision": "abstain", "confidence": 0.5},
    }]

    assert coverage(rows) == 0.0
    assert abstain_rate(rows) == 1.0


def test_expected_calibration_error_is_bounded():
    rows = [
        {"pred": {"doc_type": "legal", "confidence": 0.9}, "doc_type_gold": "legal"},
        {"pred": {"doc_type": "policy", "confidence": 0.9}, "doc_type_gold": "legal"},
    ]
    assert expected_calibration_error(rows) == 0.4


def test_release_fails_closed_when_support_and_fingerprints_are_missing():
    gold_rows = [{"id": "1", "text": "t", "doc_type_gold": "legal"}]
    prediction_rows = [{
        "id": "1",
        "pred": {"doc_type": "legal", "decision": "accepted", "confidence": 0.99},
    }]

    report = validate_release(gold_rows, prediction_rows)

    assert report["passed"] is False
    assert report["gates"]["label_support"]["passed"] is False
    assert report["gates"]["fingerprints"]["passed"] is False


def test_external_load_report_is_required_and_checks_peak_and_duplicates():
    report = _load_metric_gate([], load_report=_valid_load_report())

    assert report["passed"] is True
    assert report["actual"]["peak_multiplier"] == 2.0

    invalid = _valid_load_report()
    invalid["peak_multiplier"] = 1.5
    invalid["duplicate_write_count"] = 1
    report = _load_metric_gate([], load_report=invalid)

    assert report["passed"] is False
    assert "peak_multiplier" in report["reason"]


def test_external_load_report_blocks_shadow_p95_regression():
    gold_rows = [{"id": "1", "text": "t", "doc_type_gold": "legal"}]
    prediction_rows = [{"id": "1", "pred": {"doc_type": "legal"}}]
    load_report = _valid_load_report()
    load_report["shadow_p95_ms"] = 500.0

    report = validate_release(
        gold_rows,
        prediction_rows,
        load_report=load_report,
        rollback_report=_valid_rollback_report(),
    )

    assert report["gates"]["shadow_primary_p95"]["passed"] is False
    assert report["gates"]["shadow_primary_p95"]["reason"] == "shadow_p95_higher"


def test_external_rollback_report_requires_idempotent_replay_and_old_fingerprint():
    report = _rollback_gate([], rollback_report=_valid_rollback_report())

    assert report["passed"] is True
    assert report["actual"]["rollback_duration_seconds"] == 42.0

    invalid = _valid_rollback_report()
    invalid["old_fingerprint_present"] = False
    report = _rollback_gate([], rollback_report=invalid)

    assert report["passed"] is False
    assert "old_fingerprint" in report["reason"]


def test_validate_release_consumes_external_load_and_rollback_reports():
    gold_rows = [{"id": "1", "text": "t", "doc_type_gold": "legal"}]
    prediction_rows = [{
        "id": "1",
        "pred": {
            "doc_type": "legal",
            "decision": "accepted",
            "confidence": 0.99,
            "taxonomy_version": "taxonomy-v1",
            "rules_version": "rules-v1",
            "model_version": "model-v1",
            "prompt_version": "prompt-v1",
        },
    }]

    report = validate_release(
        gold_rows,
        prediction_rows,
        load_report=_valid_load_report(),
        rollback_report=_valid_rollback_report(),
    )

    assert report["gates"]["queue_growth"]["passed"] is True
    assert report["gates"]["rollback_duration"]["passed"] is True


def test_release_requires_double_annotation_or_adjudication_evidence():
    gold_rows = [
        {
            "id": str(index),
            "text": "t",
            "doc_type_gold": "legal",
        }
        for index in range(50)
    ]
    prediction_rows = [
        {
            "id": str(index),
            "pred": {"doc_type": "legal", "confidence": 0.99},
        }
        for index in range(50)
    ]

    report = validate_release(gold_rows, prediction_rows)

    assert report["gates"]["gold_annotation"]["passed"] is False
    assert report["gates"]["gold_annotation"]["reason"] == (
        "annotation_evidence_missing"
    )
