"""Evaluator Provider 体系 — 可组合的指标计算层。

每个 Evaluator 实现 `evaluate(case, ctx) -> dict[str, float]`，
runner 按配置组合调用，合并为最终 metrics 字典。

内置 Provider:
- SemanticEvaluator: CrossEncoder 语义指标 (sem_*)
- RagasEvaluator: RAGAS 官方包 (可选依赖)
"""
from backend.evaluation.evaluators.base import Evaluator
from backend.evaluation.evaluators.semantic import SemanticEvaluator

__all__ = [
    "Evaluator",
    "SemanticEvaluator",
]
