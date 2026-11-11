"""Travel 评测必须校验真实行程内容和 Live Provider 前置条件。"""

from backend.evaluation.models import (
    EvalReport,
    EvalResult,
    TestCase as EvalTestCase,
)
from backend.evaluation.runners import travel as travel_runner
from backend.evaluation.validity import RunValidity, resolve_report_validity


def test_poi_golden_checks_structured_itinerary_not_reporter_text():
    final = {
        "itinerary": {
            "status": "ready",
            "days": [{"items": [{"poi": {"name": "西湖"}}], "legs": []}],
        },
        "brief": {"destination": "杭州", "days": 1},
        "validation": {"violations": []},
        "final_answer": "已生成杭州行程；每日安排见行程卡。",
        "candidates": [],
    }

    actual = travel_runner._snapshot_actual(final)

    assert actual["itinerary_pois"] == ["西湖"]
    assert travel_runner._judge_one(
        {"pois_contain": ["西湖"]}, actual, "turn1",
    ) == []


def test_live_required_poi_skips_when_provider_is_unconfigured(monkeypatch):
    from backend.config import map as map_config
    from backend.config import travel as travel_config

    monkeypatch.setattr(travel_config, "TRAVEL_POI_SOURCE", "seed")
    monkeypatch.setattr(map_config, "is_configured", lambda: False)
    case = EvalTestCase(
        id="T-live-required",
        question="福州2天行程",
        module="travel",
        expected={},
        metadata={"requires_live_poi": True},
    )

    result = travel_runner._run_case(case)

    assert result.status == "skip"
    assert result.actual["execution_mode"] == "live_provider_unavailable"
    assert result.actual["real_provider_called"] is False
    assert result.actual["skip_code"] == "required_live_provider_unavailable"


def test_missing_required_provider_invalidates_cached_live_pass():
    report = EvalReport(
        module="travel",
        mode="live",
        summaries=[],
        results=[
            EvalResult(
                case_id="T-G12",
                module="travel",
                status="skip",
                expected={},
                actual={
                    "skip_code": "required_live_provider_unavailable",
                    "real_provider_called": False,
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

    assert verdict.validity is RunValidity.INVALID_PROVIDER
    assert verdict.invalid_reason == "required_live_case_unavailable"
    assert verdict.detail["case_ids"] == ["T-G12"]


def test_poi_failure_injection_uses_service_boundary_and_restores():
    from backend.travel.services import poi_service

    original = poi_service.search_poi

    with travel_runner._ProviderFailure(fail_transit=False, fail_poi=True):
        assert poi_service.search_poi is travel_runner._failing_search_poi

    assert poi_service.search_poi is original
