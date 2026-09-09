"""Planner Runner — 对接 multi_agent.planner。"""
import time

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.evaluation.runner import evaluate_planner_offline


def _run_planner(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """Planner runner — 调用 multi_agent.planner.planner_node。"""
    results: list[EvalResult] = []
    try:
        from backend.agents.planner import planner_node

        for case in cases:
            t0 = time.time()
            try:
                state = {
                    "question": case.question,
                    "kb_id": case.metadata.get("kb_id", "default"),
                }
                plan_state = planner_node(state)
                plan = plan_state.get("plan", {})
                nodes = plan.get("nodes", {})
                actual_caps = list(dict.fromkeys(
                    node.get("capability", "")
                    for node in nodes.values()
                    if node.get("capability")
                ))
                result = evaluate_planner_offline(case.id, case.expected, actual_caps)
                result.duration_ms = int((time.time() - t0) * 1000)
                results.append(result)
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="planner", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
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
