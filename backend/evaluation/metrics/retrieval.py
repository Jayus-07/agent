"""检索质量指标 — recall/precision/MRR/NDCG/chunk 级/噪声率/rerank 分离度。"""
import math
import statistics
from typing import Any


def recall_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """召回率@K：预期集中有多少出现在实际结果的前 K 个中。"""
    if not expected:
        return float("nan")
    if not actual:
        return 0.0
    actual_set = set(actual[:k])
    hits = sum(1 for e in expected if e in actual_set)
    return hits / len(expected)


def mrr(actual: list[str], expected: list[str]) -> float:
    """Mean Reciprocal Rank：第一个相关结果排名的倒数均值。"""
    if not expected:
        return float("nan")
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
        dcg += rel / math.log2(i + 2)
    return dcg


def ndcg_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """Normalized DCG@K：考虑位置权重的排序质量。"""
    if not expected:
        return float("nan")
    if not actual:
        return 0.0
    expected_set = set(expected)
    actual_relevances = [1.0 if item in expected_set else 0.0 for item in actual]
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


def chunk_recall_at_k(
    actual_chunks: list[str], expected_chunks: set[str] | list[str], k: int
) -> float:
    """Chunk 级召回 — 真实校验细粒度命中。"""
    if not expected_chunks:
        return float("nan")
    expected = set(expected_chunks)
    top_k = set(actual_chunks[:k])
    return sum(1 for c in expected if c in top_k) / len(expected)


def precision_at_k(actual: list[str], expected: list[str], k: int) -> float:
    """精确率@K：实际结果前 K 个中有多少是相关的。"""
    if not actual or k <= 0:
        return 0.0
    expected_set = set(expected)
    top_k = actual[:k]
    relevant_count = sum(1 for item in top_k if item in expected_set)
    return relevant_count / len(top_k)


def context_noise_rate(actual: list[str], expected: list[str], k: int = 10) -> float:
    """上下文噪声率：实际结果中不相关文档的比例。"""
    if not expected:
        return float("nan")
    prec = precision_at_k(actual, expected, k)
    return 1.0 - prec


def reject_accuracy(
    results: list[Any], expected_reject_ids: set[str]
) -> float:
    """拒答准确率：out_of_scope 用例中系统主动说"无答案/资料未提及"的比例。"""
    oos = [r for r in results if r.case_id in expected_reject_ids]
    if not oos:
        return float("nan")
    rejected = sum(
        1 for r in oos
        if r.status == "pass" and r.metrics.get("reject_accuracy", 0.0) >= 1.0
    )
    return rejected / len(oos)


def _string_jaccard(a: str, b: str) -> float:
    """字符串级别的 Jaccard 相似度（基于字符 2-gram）。"""
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


def stage_retrieval_metrics(
    actual_docs: list[str],
    expected_docs: list[str],
    actual_chunks: list[str] | None = None,
    expected_chunks: list[str] | None = None,
    k_values: tuple[int, ...] = (1, 3, 5, 10),
) -> dict[str, float]:
    """检索阶段（Stage 1-3）综合指标。"""
    metrics: dict[str, float] = {}

    for k in k_values:
        metrics[f"doc_recall@{k}"] = round(recall_at_k(actual_docs, expected_docs, k), 4)
        metrics[f"doc_precision@{k}"] = round(precision_at_k(actual_docs, expected_docs, k), 4)

    metrics["doc_mrr"] = round(mrr(actual_docs, expected_docs), 4)
    metrics["doc_ndcg@10"] = round(ndcg_at_k(actual_docs, expected_docs, 10), 4)
    metrics["context_noise@10"] = round(context_noise_rate(actual_docs, expected_docs, 10), 4)

    if expected_docs and actual_docs:
        metrics["top1_accuracy"] = 1.0 if actual_docs[0] in set(expected_docs) else 0.0
    elif not expected_docs:
        metrics["top1_accuracy"] = float("nan")
    else:
        metrics["top1_accuracy"] = 0.0

    if actual_chunks is not None and expected_chunks:
        for k in k_values:
            metrics[f"chunk_recall@{k}"] = round(
                chunk_recall_at_k(actual_chunks, expected_chunks, k), 4
            )

    return metrics


def stage_rerank_metrics(
    rerank_scores: list[float],
    relevant_mask: list[bool],
) -> dict[str, float]:
    """重排阶段（Stage 4）指标。"""
    if not rerank_scores or not relevant_mask:
        return {
            "avg_relevant_score": 0.0,
            "avg_noise_score": 0.0,
            "score_separation": 0.0,
            "top1_is_relevant": 0.0,
            "top3_relevant_ratio": 0.0,
        }

    relevant_scores = [s for s, r in zip(rerank_scores, relevant_mask) if r]
    noise_scores = [s for s, r in zip(rerank_scores, relevant_mask) if not r]

    avg_relevant = statistics.mean(relevant_scores) if relevant_scores else 0.0
    avg_noise = statistics.mean(noise_scores) if noise_scores else 0.0
    separation = avg_relevant - avg_noise

    top1_relevant = 1.0 if (relevant_mask and relevant_mask[0]) else 0.0
    top3_relevant_count = sum(1 for r in relevant_mask[:3] if r)
    top3_ratio = top3_relevant_count / min(3, len(relevant_mask))

    return {
        "avg_relevant_score": round(avg_relevant, 4),
        "avg_noise_score": round(avg_noise, 4),
        "score_separation": round(separation, 4),
        "top1_is_relevant": top1_relevant,
        "top3_relevant_ratio": round(top3_ratio, 4),
    }
