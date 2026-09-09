"""兼容垫片 — 实际实现已拆分到 retrieval / generation / semantic / aggregate 子模块。"""
from backend.evaluation.metrics.retrieval import (
    recall_at_k, mrr, dcg_at_k, ndcg_at_k,
    jaccard_similarity, exact_match,
    chunk_recall_at_k, precision_at_k, context_noise_rate,
    reject_accuracy, _string_jaccard,
    stage_retrieval_metrics, stage_rerank_metrics,
)
from backend.evaluation.metrics.generation import (
    answer_correctness_typed, faithfulness_claim_based,
    _split_claims, _tokenize_text,
)
from backend.evaluation.metrics.semantic import (
    context_recall_semantic, context_precision_semantic, semantic_top1,
    answer_similarity_semantic, faithfulness_semantic,
    hallucination_rate, answer_relevancy_proxy,
)
from backend.evaluation.metrics.aggregate import (
    aggregate_metrics, p95_latency, stability_variance,
)

__all__ = [
    "recall_at_k", "mrr", "dcg_at_k", "ndcg_at_k",
    "jaccard_similarity", "exact_match",
    "chunk_recall_at_k", "precision_at_k", "context_noise_rate",
    "reject_accuracy", "_string_jaccard",
    "stage_retrieval_metrics", "stage_rerank_metrics",
    "answer_correctness_typed", "faithfulness_claim_based",
    "_split_claims", "_tokenize_text",
    "context_recall_semantic", "context_precision_semantic", "semantic_top1",
    "answer_similarity_semantic", "faithfulness_semantic",
    "hallucination_rate", "answer_relevancy_proxy",
    "aggregate_metrics", "p95_latency", "stability_variance",
]
