"""测试 evaluation/metrics.py 的所有指标函数。"""
import math
import pytest
from evaluation.metrics import (
    recall_at_k,
    mrr,
    ndcg_at_k,
    jaccard_similarity,
    exact_match,
    result_set_match,
)


class TestRecallAtK:
    def test_perfect_recall(self):
        assert recall_at_k(["a", "b", "c"], ["a", "b", "c"], k=5) == 1.0

    def test_partial_recall(self):
        assert recall_at_k(["a", "x", "y"], ["a", "b", "c"], k=5) == 1.0 / 3.0

    def test_zero_recall(self):
        assert recall_at_k(["x", "y", "z"], ["a", "b"], k=5) == 0.0

    def test_k_limits(self):
        assert recall_at_k(["a", "b", "c", "d"], ["a", "d"], k=2) == 0.5

    def test_empty_expected(self):
        assert recall_at_k(["a"], [], k=5) == 1.0  # 不需要召回任何内容


class TestMRR:
    def test_first_place(self):
        assert mrr(["a", "b", "c"], ["a"]) == 1.0

    def test_third_place(self):
        assert mrr(["x", "y", "a"], ["a"]) == pytest.approx(1.0 / 3.0)

    def test_not_found(self):
        assert mrr(["x", "y", "z"], ["a"]) == 0.0

    def test_multi_expected(self):
        # 第一个相关的是 y(rank=2) → 1/2 = 0.5
        result = mrr(["x", "y", "z"], ["y", "z"])
        assert result == 0.5  # 只取第一个命中的 rank

    def test_empty_inputs(self):
        assert mrr([], ["a"]) == 0.0
        assert mrr(["a"], []) == 1.0


class TestNDCGAtK:
    def test_perfect_ranking(self):
        assert ndcg_at_k(["a"], ["a"], k=5) == 1.0

    def test_imperfect_ranking(self):
        # "a" in position 2, "b" not retrieved at all → NDCG < 1.0
        score = ndcg_at_k(["x", "a", "y", "z"], ["a", "b"], k=5)
        assert 0 < score < 1.0

    def test_perfect_ndcg_when_all_retrieved(self):
        # 所有相关项都在 top-K 中 → NDCG = 1.0
        assert ndcg_at_k(["a", "c", "b"], ["a", "b", "c"], k=5) == 1.0

    def test_no_relevant(self):
        assert ndcg_at_k(["x", "y"], ["a", "b"], k=5) == 0.0

    def test_k_truncation(self):
        assert ndcg_at_k(["x", "a"], ["a", "b"], k=1) == 0.0


class TestJaccardSimilarity:
    def test_identical(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_overlap(self):
        assert jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"}) == 2.0 / 4.0

    def test_empty_both(self):
        assert jaccard_similarity(set(), set()) == 1.0


class TestExactMatch:
    def test_match(self):
        assert exact_match("hello", "hello") == 1.0

    def test_mismatch(self):
        assert exact_match("hello", "world") == 0.0

    def test_dict_match(self):
        assert exact_match({"a": 1}, {"a": 1}) == 1.0

    def test_dict_mismatch(self):
        assert exact_match({"a": 1}, {"a": 2}) == 0.0


class TestResultSetMatch:
    def test_identical(self):
        rows = [{"name": "张三", "count": 3}]
        assert result_set_match(rows, rows) == 1.0

    def test_different_count(self):
        assert result_set_match([{"a": 1}], [{"a": 1}, {"b": 2}]) == 0.0

    def test_value_within_tolerance(self):
        assert result_set_match(
            [{"val": 3.0000001}], [{"val": 3.0}], tolerance=1e-6
        ) == 1.0

    def test_value_outside_tolerance(self):
        assert result_set_match(
            [{"val": 3.1}], [{"val": 3.0}], tolerance=0.05
        ) == 0.0

    def test_empty_both(self):
        assert result_set_match([], []) == 1.0
