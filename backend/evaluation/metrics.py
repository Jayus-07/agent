"""指标计算库 — 纯函数，无副作用，可直接用于 pytest 参数化。

V1.0 新增指标（覆盖召回/生成/可用性/性能/稳定性）：
- chunk_recall_at_k: 细粒度 chunk 级召回
- p95_latency: 95 分位响应时间
- reject_accuracy: out_of_scope 拒答准确率
- stability_variance: 同问异答方差（越小越稳定）
"""
import math
import statistics
from itertools import combinations
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


# ========== V1.0 新增指标 ==========

def chunk_recall_at_k(
    actual_chunks: list[str], expected_chunks: set[str] | list[str], k: int
) -> float:
    """Chunk 级召回 — 真实校验细粒度命中。

    Args:
        actual_chunks: 实际召回的 chunk_id 列表（已去重或不去重皆可）
        expected_chunks: 期望召回的 chunk_id 集合
        k: 仅看前 K 个结果

    Returns:
        float: 0.0~1.0，无 expected 时返回 1.0
    """
    if not expected_chunks:
        return 1.0
    expected = set(expected_chunks)
    top_k = set(actual_chunks[:k])
    return sum(1 for c in expected if c in top_k) / len(expected)


def p95_latency(durations_ms: list[int]) -> int:
    """95 分位响应时间（毫秒）。

    用于性能门禁 — 阈值建议 ≤ 3000ms。
    """
    if not durations_ms:
        return 0
    sorted_d = sorted(durations_ms)
    idx = int(len(sorted_d) * 0.95)
    # 边界保护：idx 可能等于 len
    idx = min(idx, len(sorted_d) - 1)
    return int(sorted_d[idx])


def reject_accuracy(
    results: list[Any], expected_reject_ids: set[str]
) -> float:
    """拒答准确率：out_of_scope 用例中系统主动说"无答案/资料未提及"的比例。

    Args:
        results: EvalResult 列表
        expected_reject_ids: 期望拒答的 case_id 集合

    Returns:
        float: 0.0~1.0
    """
    oos = [r for r in results if r.case_id in expected_reject_ids]
    if not oos:
        return 1.0
    # pass 表示成功拒答（在 builtin.py runner 里 expect_reject=True 用例的 status
    # 走专门的判定；这里兼容 status=='pass' 且 metrics 有 reject_marker 的结果）
    rejected = sum(
        1 for r in oos
        if r.status == "pass" and r.metrics.get("rejected", 0.0) >= 1.0
    )
    return rejected / len(oos)


def stability_variance(answers: list[str]) -> float:
    """同问 N 次答案的稳定性方差 — 越小越稳定。

    Args:
        answers: 同一 question 跑 N 次得到的答案列表

    Returns:
        float: Jaccard 相似度的方差，0.0 表示完全一致。
        阈值建议: ≤ 0.15 表示稳定。
    """
    if len(answers) < 2:
        return 0.0
    pairs = [
        _string_jaccard(a, b) for a, b in combinations(answers, 2)
    ]
    return float(statistics.pstdev(pairs))


def _string_jaccard(a: str, b: str) -> float:
    """字符串级别的 Jaccard 相似度（基于字符 2-gram）。

    比集合更鲁棒 — 处理变长文本。
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not grams_a and not grams_b:
        return 1.0
    union = grams_a | grams_b
    return len(grams_a & grams_b) / len(union) if union else 0.0


def aggregate_metrics(results: list[Any]) -> dict[str, float]:
    """从 EvalResult 列表聚合统计指标（per-case 指标的均值）。

    Args:
        results: EvalResult 列表（必须有 .metrics 字段）

    Returns:
        dict[str, float]: {metric_name: avg_value}
    """
    agg: dict[str, list[float]] = {}
    for r in results:
        for k, v in (r.metrics or {}).items():
            if isinstance(v, (int, float)):
                agg.setdefault(k, []).append(float(v))
    return {k: round(sum(vs) / len(vs), 4) for k, vs in agg.items()}
