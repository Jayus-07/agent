"""E2E Runner — 对接 multi_agent.graph.MultiAgentSystem（完整 Agent 链路）。"""
import time

from backend.evaluation.judge import judge_answer
from backend.evaluation.metrics import answer_correctness_typed
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner


def _infer_routing_from_answer(answer: str, expected_routing: set) -> set:
    """从回答内容推断使用了哪些能力。"""
    actual_caps = set()
    if not answer:
        return actual_caps
    if "|" in answer and "---" in answer:
        actual_caps.add("query_database")
    if "参考文献" in answer or "来源" in answer:
        actual_caps.add("search_knowledge")
    return actual_caps


def _run_e2e(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """E2E runner — 完整 MultiAgentSystem 链路 + 可选的 LLM-as-Judge 评分。"""
    if not cases:
        return []

    judge = kwargs.get("judge", False)
    results: list[EvalResult] = []

    try:
        from backend.orchestration.graph import MultiAgentSystem
        mas = MultiAgentSystem()

        for case in cases:
            t0 = time.time()
            try:
                kb_id = case.metadata.get("kb_id", "default")
                answer = mas.ask(case.question, kb_id=kb_id)

                expected_routing = set(case.expected.get("expected_routing", []))
                actual_caps = _infer_routing_from_answer(answer, expected_routing)
                routing_ok = (
                    expected_routing.issubset(actual_caps)
                    if expected_routing else True
                )

                metrics = {"routing_accuracy": 1.0 if routing_ok else 0.0}

                if judge and answer:
                    rubric = case.expected.get("rubric", {})
                    jr = judge_answer(case.question, rubric, answer)
                    metrics["judge_completeness"] = float(jr.scores.get("completeness", 0))
                    metrics["judge_faithfulness"] = float(jr.scores.get("faithfulness", 0))
                    metrics["judge_conciseness"] = float(jr.scores.get("conciseness", 0))
                    metrics["judge_citation"] = float(jr.scores.get("citation_quality", 0))
                    metrics["judge_total"] = jr.total
                    metrics["judge_confidence"] = {
                        "low": 0.0, "medium": 0.5, "high": 1.0
                    }.get(jr.confidence, 0.5)

                if case.metadata.get("generation_eval") and answer:
                    expected_answer = case.metadata.get("expected_answer", "")
                    must_contain = case.metadata.get("must_contain", [])
                    must_not_contain = case.metadata.get("must_not_contain", [])
                    answer_type = case.metadata.get("answer_type", "factual")

                    if expected_answer:
                        correctness = answer_correctness_typed(
                            actual_answer=answer,
                            expected_answer=expected_answer,
                            answer_type=answer_type,
                            must_contain=must_contain,
                            must_not_contain=must_not_contain,
                        )
                        metrics["gen_answer_correctness"] = correctness["correctness"]
                        metrics["gen_must_contain_hit"] = correctness["must_contain_hit"]
                        metrics["gen_must_not_contain_violation"] = correctness["must_not_contain_violation"]

                passed = routing_ok and (
                    not judge or metrics.get("judge_total", 5) >= 3.0
                )

                results.append(EvalResult(
                    case_id=case.id, module="e2e",
                    status="pass" if passed else "fail",
                    expected=case.expected,
                    actual={"answer": answer[:500], "routing": list(actual_caps)},
                    metrics={
                        k: round(v, 4) if isinstance(v, float) else v
                        for k, v in metrics.items()
                    },
                    duration_ms=int((time.time() - t0) * 1000),
                ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="e2e", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e),
                    duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="e2e", status="error",
                expected=c.expected, actual={},
                error_msg="MultiAgentSystem not available",
            )
            for c in cases
        ]
    return results


register_runner("e2e", _run_e2e, needs_live=True)
