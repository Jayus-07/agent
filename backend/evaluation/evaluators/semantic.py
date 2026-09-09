"""SemanticEvaluator — CrossEncoder 语义指标 Provider。

离线运行（不需要 LLM），依赖本地 CrossEncoder 模型。
指标: sem_context_recall, sem_context_precision, sem_faithfulness, sem_answer_correctness 等。
"""
from __future__ import annotations

from typing import Any

from backend.evaluation.evaluators.base import Evaluator
from backend.evaluation.models import TestCase
from backend.evaluation.runners.rag_semantic import compute_semantic_metrics


class SemanticEvaluator(Evaluator):
    name = "semantic"

    def __init__(self, thresholds: dict[str, float] | None = None):
        self._thresholds = thresholds

    def should_run(self, case: TestCase, ctx: dict[str, Any]) -> bool:
        gate = ctx.get("gate_mode", "shadow")
        return gate in ("shadow", "semantic") and not ctx.get("ragas", False)

    def evaluate(self, case: TestCase, ctx: dict[str, Any]) -> dict[str, float]:
        question = case.question
        details = ctx.get("details", [])
        generated_answer = ctx.get("generated_answer", "")

        return compute_semantic_metrics(
            question=question,
            details=details,
            case=case,
            thresholds=self._thresholds or ctx.get("semantic_thresholds"),
            generated_answer=generated_answer,
        )
