from types import SimpleNamespace

from backend.evaluation.models import EvalReport, EvalResult
from backend.evaluation.runners import travel_provider
from backend.evaluation.validity import RunValidity, resolve_report_validity


def test_live_mode_does_not_run_fake_provider_probes(monkeypatch):
    case = SimpleNamespace(id="TP-LIVE-001", expected={"status": "success"})

    def fail_if_fake_runner_runs(_cases):
        raise AssertionError("Live 模式不应执行 fake Provider 探针")

    monkeypatch.setattr(
        travel_provider, "_run_all_cases", fail_if_fake_runner_runs,
    )

    results = travel_provider._run_travel_provider([case], live=True)

    assert len(results) == 1
    assert results[0].status == "skip"
    assert results[0].actual["execution_mode"] == "live_unsupported"
    assert results[0].actual["real_provider_called"] is False
    assert results[0].actual["skip_code"] == "live_evaluator_unsupported"


def test_offline_mode_keeps_fake_provider_runner(monkeypatch):
    case = SimpleNamespace(id="TP-OFFLINE-001", expected={})
    expected = [object()]
    monkeypatch.setattr(
        travel_provider, "_run_all_cases", lambda cases: expected,
    )

    assert travel_provider._run_travel_provider([case], live=False) is expected


def test_unsupported_live_case_invalidates_mixed_report_even_if_cached_pass():
    report = EvalReport(
        module="all",
        mode="live",
        summaries=[],
        results=[
            EvalResult(
                case_id="P-001",
                module="planner",
                status="pass",
                expected={},
                actual={},
            ),
            EvalResult(
                case_id="TP-001",
                module="travel-provider",
                status="skip",
                expected={},
                actual={
                    "execution_mode": "live_unsupported",
                    "real_provider_called": False,
                    "skip_code": "live_evaluator_unsupported",
                },
            ),
        ],
        metadata={
            "run_validity": {
                "validity": "VALID",
                "invalid_reason": "",
                "detail": {},
            },
        },
    )

    verdict = resolve_report_validity(report)

    assert verdict.validity is RunValidity.INVALID_ENV
    assert verdict.invalid_reason == "live_evaluator_unsupported"
    assert verdict.detail["case_ids"] == ["TP-001"]
