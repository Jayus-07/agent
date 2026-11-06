"""Planner Live runner 的来源、兜底与 run validity 契约。"""

import importlib

from backend.evaluation.models import (
    EvalReport,
    EvalResult,
    ModuleSummary,
    TestCase as EvalCase,
)
from backend.evaluation.release_gate import compute_module_release_gate
from backend.evaluation.runners.planner import _run_planner
from backend.evaluation.validity import RunValidity, resolve_report_validity


def _case() -> EvalCase:
    return EvalCase(
        id="P-LIVE",
        question="查询近30天销售额并生成简报",
        module="planner",
        expected={
            "capabilities": ["sql.query", "report.generate"],
            "should_not_contain": ["rag.search"],
            "min_steps": 2,
            "max_steps": 2,
            "edges": [{"from": "sql.query", "to": "report.generate"}],
        },
    )


def _plan() -> dict:
    return {
        "nodes": [
            {
                "step_id": "1",
                "capability": "sql.query",
                "description": "查询销售额",
                "params": {"question": "查询近30天销售额"},
            },
            {
                "step_id": "2",
                "capability": "report.generate",
                "description": "生成销售简报",
                "params": {"report_type": "sales_summary"},
            },
        ],
        "edges": {"2": ["1"]},
    }


def _report(results: list[EvalResult], pass_rate: float | None = None) -> EvalReport:
    total = len(results)
    passed = sum(result.status == "pass" for result in results)
    return EvalReport(
        module="planner",
        mode="live",
        summaries=[ModuleSummary(
            module="planner",
            total=total,
            passed=passed,
            failed=sum(result.status == "fail" for result in results),
            errors=sum(result.status == "error" for result in results),
            skipped=sum(result.status == "skip" for result in results),
            pass_rate=pass_rate if pass_rate is not None else passed / max(total, 1),
        )],
        results=results,
    )


def _run_with_outcome(monkeypatch, outcome: dict, case: EvalCase | None = None) -> EvalResult:
    package = importlib.import_module("backend.agents.planner")

    def fake_planner_node(state: dict) -> dict:
        assert state["_evaluation_mode"] is True
        return {"plan": {}, "_planner_evaluation": outcome}

    monkeypatch.setattr(package, "planner_node", fake_planner_node)
    return _run_planner([case or _case()])[0]


def test_live_runner_accepts_only_the_current_model_source(monkeypatch):
    result = _run_with_outcome(monkeypatch, {
        "status": "success",
        "source": "model",
        "actual_plan": _plan(),
    })

    assert result.status == "pass"
    assert result.metrics["capability_match"] == 1.0
    assert result.metrics["edge_match"] == 1.0
    assert result.actual["planner_outcome"] == {"status": "success", "source": "model"}


def test_runtime_fallback_is_quality_failure_even_if_fallback_matches(monkeypatch):
    result = _run_with_outcome(monkeypatch, {
        "status": "fallback",
        "source": "runtime_fallback",
        "error_category": "empty_model_plan",
        "actual_plan": _plan(),
    })

    assert result.status == "fail"
    assert result.error_stage == "planner_fallback"
    assert "不能作为模型通过样本" in (result.error_msg or "")


def test_runtime_knowledge_injection_does_not_hide_missing_model_capability(monkeypatch):
    case = EvalCase(
        id="P-RUNTIME-POLICY",
        question="查询本月退款订单，并说明退款处理规范。",
        module="planner",
        expected={
            "capabilities": ["sql.query", "rag.search"],
            "min_steps": 2,
            "max_steps": 2,
            "edges": [],
        },
    )
    model_plan = {
        "nodes": [{
            "step_id": "1",
            "capability": "sql.query",
            "description": "查询退款订单",
            "params": {},
        }],
        "edges": {},
    }

    result = _run_with_outcome(monkeypatch, {
        "status": "success",
        "source": "model",
        "runtime_adjusted": True,
        "actual_plan": model_plan,
    }, case=case)

    assert result.status == "fail"
    assert result.metrics["capability_match"] < 1.0
    assert result.metrics["structure_ok"] == 0.0


def test_invalid_model_output_is_quality_failure_not_environment_error(monkeypatch):
    result = _run_with_outcome(monkeypatch, {
        "status": "invalid_output",
        "source": "model",
        "error_category": "contract_violation",
        "errors": ["nodes[0].params must be an object"],
    })
    report = _report([result])

    assert result.status == "fail"
    assert resolve_report_validity(report).validity is RunValidity.VALID


def test_cached_source_cannot_be_counted_as_live_model_pass(monkeypatch):
    result = _run_with_outcome(monkeypatch, {
        "status": "success",
        "source": "cache",
        "actual_plan": _plan(),
    })
    report = _report([result])

    assert result.status == "error"
    assert resolve_report_validity(report).validity is RunValidity.INVALID_INFRA


def test_single_provider_error_overrides_stale_validity_and_invalidates_gate(monkeypatch):
    failed = _run_with_outcome(monkeypatch, {
        "status": "error",
        "source": "provider_degradation",
        "error_category": "provider",
        "error_code": "auth_failed",
    })
    passed_results = [
        EvalResult(
            case_id=f"P-{index:02d}",
            module="planner",
            status="pass",
            expected={},
            actual={"planner_outcome": {"status": "success", "source": "model"}},
        )
        for index in range(19)
    ]
    report = _report([*passed_results, failed], pass_rate=0.95)
    report.metadata["run_validity"] = {"validity": "VALID", "invalid_reason": "", "detail": {}}

    validity = resolve_report_validity(report)
    gate = compute_module_release_gate(report)

    assert validity.validity is RunValidity.INVALID_PROVIDER
    assert validity.invalid_reason == "planner_live_provider_error"
    assert gate["verdict"] == "INVALID"
    assert gate["overall"] is False


def test_request_budget_error_is_invalid_budget(monkeypatch):
    result = _run_with_outcome(monkeypatch, {
        "status": "error",
        "source": "runtime",
        "error_category": "budget",
        "error_code": "BUDGET_EXCEEDED",
    })

    assert resolve_report_validity(_report([result])).validity is RunValidity.INVALID_BUDGET


def test_provider_timeout_error_is_invalid_infra(monkeypatch):
    result = _run_with_outcome(monkeypatch, {
        "status": "error",
        "source": "provider_degradation",
        "error_category": "provider",
        "error_code": "timeout",
    })

    assert resolve_report_validity(_report([result])).validity is RunValidity.INVALID_INFRA


def test_live_skip_invalidates_planner_run():
    skipped = EvalResult(
        case_id="P-SKIP",
        module="planner",
        status="skip",
        expected={},
        actual={"skip_code": "planner_live_provider_unavailable"},
    )
    passed = EvalResult(
        case_id="P-PASS",
        module="planner",
        status="pass",
        expected={},
        actual={"planner_outcome": {"status": "success", "source": "model"}},
    )

    validity = resolve_report_validity(_report([passed, skipped]))

    assert validity.validity is RunValidity.INVALID_INFRA
    assert validity.invalid_reason == "planner_live_case_skipped"
