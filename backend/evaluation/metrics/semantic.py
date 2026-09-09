"""语义指标 — CrossEncoder/Embedding scorer 驱动的软评分。"""
from typing import Any

from backend.evaluation.metrics.generation import _split_claims


def context_recall_semantic(
    retrieved_texts: list[str],
    ground_truth: list[str],
    scorer: Any,
    threshold: float = 0.50,
) -> dict[str, float]:
    """RAGAS-style 语义上下文召回率。"""
    if not ground_truth:
        return {
            "context_recall": float("nan"),
            "context_recall_soft": float("nan"),
            "covered_passages": 0,
            "total_passages": 0,
        }
    if not retrieved_texts:
        return {
            "context_recall": 0.0,
            "context_recall_soft": 0.0,
            "covered_passages": 0,
            "total_passages": len(ground_truth),
        }

    covered = 0
    soft_sum = 0.0
    for gt in ground_truth:
        queries = [gt] * len(retrieved_texts)
        scores = scorer.score_pairs(queries, retrieved_texts)
        max_score = max(scores) if scores else 0.0
        if max_score >= threshold:
            covered += 1
        soft_sum += max_score

    n = len(ground_truth)
    return {
        "context_recall": round(covered / n, 4),
        "context_recall_soft": round(soft_sum / n, 4),
        "covered_passages": covered,
        "total_passages": n,
    }


def context_precision_semantic(
    retrieved_texts: list[str],
    ground_truth: list[str],
    scorer: Any,
    threshold: float = 0.50,
    k: int = 10,
) -> dict[str, float]:
    """语义上下文精确率 — top-k 检索结果的平均相关性得分。"""
    if not retrieved_texts and not ground_truth:
        return {"context_precision": float("nan"), "relevant_count": 0, "total_retrieved": 0}
    if not ground_truth:
        return {"context_precision": float("nan"), "relevant_count": 0, "total_retrieved": len(retrieved_texts)}
    if not retrieved_texts:
        return {"context_precision": 0.0, "relevant_count": 0, "total_retrieved": 0}

    top_k = retrieved_texts[:k]
    soft_scores: list[float] = []
    relevant = 0
    for doc in top_k:
        queries = [doc] * len(ground_truth)
        scores = scorer.score_pairs(queries, ground_truth)
        max_score = max(scores) if scores else 0.0
        soft_scores.append(max_score)
        if max_score >= threshold:
            relevant += 1

    avg_precision = sum(soft_scores) / len(soft_scores) if soft_scores else 0.0
    return {
        "context_precision": round(avg_precision, 4),
        "relevant_count": relevant,
        "total_retrieved": len(top_k),
    }


def semantic_top1(
    retrieved_texts: list[str],
    ground_truth: list[str],
    scorer: Any,
    threshold: float = 0.50,
) -> float:
    """Top-1 语义命中 — 排名第一的检索结果是否与任一 GT 片段语义相似。"""
    if not ground_truth:
        return float("nan")
    if not retrieved_texts:
        return 0.0

    top1 = retrieved_texts[0]
    queries = [top1] * len(ground_truth)
    scores = scorer.score_pairs(queries, ground_truth)
    return 1.0 if max(scores) >= threshold else 0.0


def answer_similarity_semantic(
    answer: str,
    expected_answer: str,
    scorer: Any,
) -> float:
    """语义答案相似度 — scorer(answer, expected_answer)。"""
    if not expected_answer:
        return float("nan")
    if not answer:
        return 0.0
    scores = scorer.score_pairs([answer], [expected_answer])
    return round(scores[0], 4)


def faithfulness_semantic(
    answer: str,
    context: list[str],
    scorer: Any,
    threshold: float = 0.50,
) -> dict[str, float]:
    """基于 CrossEncoder 的忠实度评估 — soft scoring 版本。"""
    if not answer.strip():
        return {"faithfulness": 1.0, "claim_count": 0, "supported_count": 0}
    if not context:
        claims = _split_claims(answer)
        return {"faithfulness": 0.0, "claim_count": len(claims), "supported_count": 0}

    claims = _split_claims(answer)
    if not claims:
        return {"faithfulness": 1.0, "claim_count": 0, "supported_count": 0}

    max_scores: list[float] = []
    supported = 0
    for claim in claims:
        queries = [claim] * len(context)
        scores = scorer.score_pairs(queries, context)
        max_score = max(scores) if scores else 0.0
        max_scores.append(max_score)
        if max_score >= threshold:
            supported += 1

    avg_faithfulness = sum(max_scores) / len(max_scores) if max_scores else 0.0

    return {
        "faithfulness": round(avg_faithfulness, 4),
        "claim_count": len(claims),
        "supported_count": supported,
    }


def hallucination_rate(faithfulness_result: dict[str, float]) -> float:
    """幻觉率 = 1 - faithfulness。"""
    return round(1.0 - faithfulness_result.get("faithfulness", 0.0), 4)


def answer_relevancy_proxy(
    question: str,
    answer: str,
    scorer: Any,
) -> float:
    """答案相关性代理指标 — cosine(embed(question), embed(answer))。"""
    if not question:
        return float("nan")
    if not answer:
        return 0.0
    scores = scorer.score_pairs([question], [answer])
    return round(scores[0], 4)
