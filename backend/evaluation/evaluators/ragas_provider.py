"""RagasEvaluator — RAGAS 官方包作为可选 Benchmark Provider。

所有 `from ragas import ...` 调用封装在 ragas_bridge 内部，
本模块仅负责从 eval_ctx 读取"四张答卷"并调用 compute_ragas_metrics_safe。
"""
from __future__ import annotations

from typing import Any

from backend.evaluation.evaluators.base import Evaluator
from backend.evaluation.models import TestCase
from backend.shared.logger import logger


class RagasEvaluator(Evaluator):
    name = "ragas"

    def should_run(self, case: TestCase, ctx: dict[str, Any]) -> bool:
        if ctx.get("no_ragas", False):
            return False
        if ctx.get("ragas_disabled", False):
            return False
        return True

    def evaluate(self, case: TestCase, ctx: dict[str, Any]) -> dict[str, float]:
        ragas_input = ctx.get("ragas_input")
        if not ragas_input:
            return {
                "ragas_context_recall": 0.0,
                "ragas_context_precision": 0.0,
                "ragas_faithfulness": 0.0,
                "ragas_reason": "no_ragas_input",
            }

        question = ragas_input["question"]
        answer = ragas_input.get("answer", "")
        contexts = ragas_input.get("contexts", [])
        ground_truth = ragas_input.get("ground_truth")
        reference_contexts = ragas_input.get("reference_contexts")

        if not contexts or not answer or not answer.strip():
            return {
                "ragas_context_recall": 0.0,
                "ragas_context_precision": 0.0,
                "ragas_faithfulness": 0.0,
                "ragas_reason": "missing_context_or_answer",
            }

        level = ctx.get("ragas_level", "standard")

        try:
            from backend.evaluation.ragas_bridge import compute_ragas_metrics_safe
            return compute_ragas_metrics_safe(
                question=question,
                answer=answer,
                contexts=contexts,
                ground_truth=ground_truth,
                reference_contexts=reference_contexts,
                level=level,
            )
        except Exception as e:
            logger.warning(f"[RAGAS] {case.id} 计算失败: {e}")
            return {
                "ragas_context_recall": 0.0,
                "ragas_context_precision": 0.0,
                "ragas_faithfulness": 0.0,
                "ragas_reason": f"error: {e}",
            }
