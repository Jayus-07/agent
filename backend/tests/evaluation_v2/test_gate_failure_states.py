from types import SimpleNamespace

from backend.evaluation_v2.runner import _gate


def test_provider_timeout_is_invalid_run_state_not_an_answer_quality_failure():
    report = SimpleNamespace(
        metadata={"run_validity": {"validity": "VALID"}},
        results=[
            SimpleNamespace(case_id="case-1", status="pass", error_stage=None),
            SimpleNamespace(case_id="case-2", status="error", error_stage="timeout"),
        ],
    )
    definition = {
        "minimum_valid_samples": 1,
        "metric_policy": {"min_pass_rate": 0.0},
        "security_policy": {"max_failed_cases": 10},
        "cases": [],
    }

    verdict, reasons = _gate(report, definition)

    assert verdict == "INVALID"
    assert reasons == [{"code": "incomplete_results", "errors": 1, "skipped": 0}]
