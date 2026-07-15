"""指标计算库 — 纯函数，无副作用，可直接用于 pytest 参数化。"""

import math
from typing import Any


def recall_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """召回率@K：预期集中有多少出现在实际结果的前 K 个中。"""
    if not expected:
        return 1.0
    if not actual:
        return 0.0
    actual_set = set(actual[:k])
    hits = sum(1 for e in expected if e in actual_set)
    return hits / len(expected)


def mrr(actual: list[str], expected: list[str]) -> float:
    """Mean Reciprocal Rank：第一个相关结果排名的倒数均值。"""
    if not expected:
        return 1.0
    if not actual:
        return 0.0
    expected_set = set(expected)
    for i, item in enumerate(actual, start=1):
        if item in expected_set:
            return 1.0 / i
    return 0.0


def dcg_at_k(relevances: list[float], k: int) -> float:
    """Discounted Cumulative Gain。"""
    dcg = 0.0
    for i, rel in enumerate(relevances[:k]):
        # 使用标准 DCG 公式: rel / log2(i+2)
        dcg += rel / math.log2(i + 2)
    return dcg


def ndcg_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """Normalized DCG@K：考虑位置权重的排序质量。"""
    if not expected:
        return 1.0
    if not actual:
        return 0.0
    expected_set = set(expected)
    # 二值相关度：在期望集中=1，否则=0
    actual_relevances = [1.0 if item in expected_set else 0.0 for item in actual]
    # 理想排序：所有相关结果排在最前面
    ideal_relevances = [1.0] * min(len(expected), k)
    ideal_relevances += [0.0] * max(0, k - len(ideal_relevances))

    actual_dcg = dcg_at_k(actual_relevances, k)
    ideal_dcg = dcg_at_k(ideal_relevances, k)
    if ideal_dcg == 0.0:
        return 0.0
    return actual_dcg / ideal_dcg


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard 相似度：|A ∩ B| / |A ∪ B|。"""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / len(union)


def exact_match(actual: Any, expected: Any) -> float:
    """精确匹配，返回 0.0 或 1.0。"""
    return 1.0 if actual == expected else 0.0


def result_set_match(
    actual_rows: list[dict], expected_rows: list[dict], tolerance: float = 1e-6
) -> float:
    """SQL 结果集比对：行数一致 + 每行每列的值在 tolerance 内一致。"""
    if len(actual_rows) != len(expected_rows):
        return 0.0
    if not actual_rows and not expected_rows:
        return 1.0

    # 按所有列排序以消除行顺序差异
    def sort_key(row: dict) -> str:
        return str(sorted(row.items()))

    sorted_actual = sorted(actual_rows, key=sort_key)
    sorted_expected = sorted(expected_rows, key=sort_key)

    for a_row, e_row in zip(sorted_actual, sorted_expected):
        if set(a_row.keys()) != set(e_row.keys()):
            return 0.0
        for key in a_row:
            a_val = a_row[key]
            e_val = e_row[key]
            if isinstance(a_val, (int, float)) and isinstance(e_val, (int, float)):
                if abs(a_val - e_val) > tolerance:
                    return 0.0
            elif str(a_val) != str(e_val):
                return 0.0
    return 1.0
