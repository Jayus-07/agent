"""语义指标单元测试 — 注入 FakeScorer，零模型依赖。"""
import math

import pytest

from backend.evaluation.metrics import (
    answer_relevancy_proxy,
    answer_similarity_semantic,
    context_precision_semantic,
    context_recall_semantic,
    faithfulness_semantic,
    hallucination_rate,
    semantic_top1,
)


class FakeScorer:
    """可控评分器 — 返回预设分数或基于字符串相等性的确定性分数。"""

    def __init__(self, fixed_scores: list[float] | None = None):
        self._fixed = fixed_scores
        self._call_count = 0

    def score_pairs(self, queries: list[str], docs: list[str]) -> list[float]:
        self._call_count += 1
        if self._fixed is not None:
            return self._fixed[: len(queries)]
        return [1.0 if q == d else 0.0 for q, d in zip(queries, docs)]


@pytest.fixture
def fake():
    return FakeScorer()


# ── context_recall_semantic ──


class TestContextRecallSemantic:
    def test_empty_gt_returns_nan(self, fake):
        result = context_recall_semantic(["doc1"], [], fake)
        assert math.isnan(result["context_recall"])
        assert result["total_passages"] == 0

    def test_empty_retrieved_returns_zero(self, fake):
        result = context_recall_semantic([], ["gt1"], fake)
        assert result["context_recall"] == 0.0
        assert result["total_passages"] == 1

    def test_perfect_recall(self, fake):
        result = context_recall_semantic(["gt1", "gt2"], ["gt1", "gt2"], fake)
        assert result["context_recall"] == 1.0
        assert result["covered_passages"] == 2

    def test_zero_recall(self, fake):
        result = context_recall_semantic(["unrelated"], ["gt1"], fake)
        assert result["context_recall"] == 0.0

    def test_threshold_monotonicity(self):
        scorer = FakeScorer(fixed_scores=[0.6])
        low = context_recall_semantic(["doc"], ["gt"], scorer, threshold=0.5)
        high = context_recall_semantic(["doc"], ["gt"], scorer, threshold=0.7)
        assert low["context_recall"] >= high["context_recall"]

    def test_soft_recall(self):
        scorer = FakeScorer(fixed_scores=[0.8])
        result = context_recall_semantic(["doc"], ["gt"], scorer, threshold=0.5)
        assert result["context_recall_soft"] == 0.8


# ── context_precision_semantic ──


class TestContextPrecisionSemantic:
    def test_empty_gt_returns_nan(self, fake):
        import math
        result = context_precision_semantic(["doc1"], [], fake)
        assert math.isnan(result["context_precision"])

    def test_empty_retrieved_returns_zero(self, fake):
        result = context_precision_semantic([], ["gt1"], fake)
        assert result["context_precision"] == 0.0

    def test_all_relevant(self, fake):
        result = context_precision_semantic(["gt1", "gt2"], ["gt1", "gt2"], fake)
        assert result["context_precision"] == 1.0

    def test_none_relevant(self, fake):
        result = context_precision_semantic(["noise1", "noise2"], ["gt1"], fake)
        assert result["context_precision"] == 0.0


# ── semantic_top1 ──


class TestSemanticTop1:
    def test_empty_gt_returns_nan(self, fake):
        assert math.isnan(semantic_top1(["doc"], [], fake))

    def test_empty_retrieved_returns_zero(self, fake):
        assert semantic_top1([], ["gt"], fake) == 0.0

    def test_hit(self, fake):
        assert semantic_top1(["gt1", "other"], ["gt1"], fake) == 1.0

    def test_miss(self, fake):
        assert semantic_top1(["noise"], ["gt1"], fake) == 0.0


# ── answer_similarity_semantic ──


class TestAnswerSimilarity:
    def test_both_empty(self, fake):
        assert math.isnan(answer_similarity_semantic("", "", fake))

    def test_one_empty(self, fake):
        assert math.isnan(answer_similarity_semantic("answer", "", fake))

    def test_identical(self, fake):
        assert answer_similarity_semantic("same text", "same text", fake) == 1.0

    def test_different(self, fake):
        assert answer_similarity_semantic("a", "b", fake) == 0.0


# ── faithfulness_semantic ──


class TestFaithfulnessSemantic:
    def test_empty_answer(self, fake):
        result = faithfulness_semantic("", ["ctx"], fake)
        assert result["faithfulness"] == 1.0

    def test_empty_context(self, fake):
        result = faithfulness_semantic("some claim。", [], fake)
        assert result["faithfulness"] == 0.0

    def test_supported_claims(self):
        scorer = FakeScorer(fixed_scores=[0.9, 0.8])
        result = faithfulness_semantic("claim1。claim2。", ["ctx1", "ctx2"], scorer, threshold=0.5)
        # Soft scoring: avg of max scores = (0.9 + 0.9) / 2 = 0.9
        assert result["faithfulness"] == 0.9
        assert result["supported_count"] == 2

    def test_unsupported_claims(self):
        scorer = FakeScorer(fixed_scores=[0.1, 0.2])
        result = faithfulness_semantic("claim1。claim2。", ["ctx1", "ctx2"], scorer, threshold=0.5)
        # Soft scoring: avg of max scores = (0.2 + 0.2) / 2 = 0.2
        assert result["faithfulness"] == 0.2
        assert result["supported_count"] == 0


# ── hallucination_rate ──


class TestHallucinationRate:
    def test_perfect_faithfulness(self):
        assert hallucination_rate({"faithfulness": 1.0}) == 0.0

    def test_zero_faithfulness(self):
        assert hallucination_rate({"faithfulness": 0.0}) == 1.0

    def test_partial(self):
        assert hallucination_rate({"faithfulness": 0.7}) == 0.3


# ── answer_relevancy_proxy ──


class TestAnswerRelevancyProxy:
    def test_both_empty(self, fake):
        assert math.isnan(answer_relevancy_proxy("", "", fake))

    def test_one_empty(self, fake):
        assert answer_relevancy_proxy("q", "", fake) == 0.0

    def test_identical(self, fake):
        assert answer_relevancy_proxy("same", "same", fake) == 1.0
