"""元数据发布门禁：指标缺失时 fail closed，诊断一致率不冒充准确率。"""
from __future__ import annotations

from backend.eval.metadata_baseline.evaluate import (
    abstain_rate,
    build_release_report,
    coverage,
    expected_calibration_error,
)
from backend.eval.metadata_baseline.validate_release import validate_release


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
