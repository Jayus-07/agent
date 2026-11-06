"""Planner Runner — 对接 multi_agent.planner。"""
import time

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.evaluation.runner import evaluate_planner_offline


def _run_planner(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """Planner Live runner — 无缓存调用并验证完整执行计划契约。"""
    results: list[EvalResult] = []
    try:
        from backend.agents.planner import planner_node
        from backend.agents.planner.planner import _planner_evaluation_error

        for case in cases:
            t0 = time.time()
            try:
                state = {
                    "question": case.question,
                    "kb_id": case.metadata.get("kb_id", "default"),
                    "_evaluation_mode": True,
                }
                plan_state = planner_node(state)
                outcome = plan_state.get("_planner_evaluation")
                if not isinstance(outcome, dict):
                    raise RuntimeError("Planner evaluation outcome is missing")

                public_outcome = {
                    key: value for key, value in outcome.items()
                    if key != "actual_plan"
                }
                status = outcome.get("status")
                if status == "error":
                    error_code = str(outcome.get("error_code") or "unknown")
                    results.append(EvalResult(
                        case_id=case.id, module="planner", status="error",
                        expected=case.expected,
                        actual={"planner_outcome": public_outcome},
                        error_msg=f"Planner Live execution failed [{error_code}]",
                        error_stage=str(outcome.get("error_category") or "planner_runtime"),
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
                    continue

                if status == "invalid_output":
                    results.append(EvalResult(
                        case_id=case.id, module="planner", status="fail",
                        expected=case.expected,
                        actual={"planner_outcome": public_outcome},
                        error_msg="; ".join(outcome.get("errors") or [
                            "Planner output violates the plan contract",
                        ]),
                        error_stage="planner_output",
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
                    continue

                if status == "fallback":
                    actual_plan = outcome.get("actual_plan")
                    result = evaluate_planner_offline(
                        case.id,
                        case.expected,
                        actual_plan=actual_plan if isinstance(actual_plan, dict) else None,
                    )
                    result.status = "fail"
                    result.error_stage = "planner_fallback"
                    result.error_msg = "空模型计划触发运行时兜底，不能作为模型通过样本"
                    result.actual["planner_outcome"] = public_outcome
                    result.duration_ms = int((time.time() - t0) * 1000)
                    results.append(result)
                    continue

                if status != "success" or outcome.get("source") != "model":
                    source = str(outcome.get("source") or "unknown")
                    outcome = {
                        "status": status or "unknown",
                        "source": source,
                        "error_category": "runtime",
                        "error_code": "non_model_live_result",
                    }
                    results.append(EvalResult(
                        case_id=case.id, module="planner", status="error",
                        expected=case.expected,
                        actual={"planner_outcome": outcome},
                        error_msg="Planner Live 未证明本次真实模型调用",
                        error_stage="planner_runtime",
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
                    continue

                actual_plan = outcome.get("actual_plan")
                if not isinstance(actual_plan, dict):
                    raise RuntimeError("Planner evaluation plan is missing")
                result = evaluate_planner_offline(
                    case.id, case.expected, actual_plan=actual_plan)
                result.actual["planner_outcome"] = public_outcome
                result.duration_ms = int((time.time() - t0) * 1000)
                results.append(result)
            except Exception as e:
                outcome = _planner_evaluation_error(e)
                error_code = str(outcome.get("error_code") or "unknown")
                results.append(EvalResult(
                    case_id=case.id, module="planner", status="error",
                    expected=case.expected,
                    actual={"planner_outcome": outcome},
                    error_msg=f"Planner Live execution failed [{error_code}]",
                    error_stage=str(outcome.get("error_category") or "planner_runtime"),
                    duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="planner", status="error",
                expected=c.expected, actual={},
                error_msg="Planner module not available",
            )
            for c in cases
        ]
    return results


register_runner("planner", _run_planner, needs_live=True)
