"""语义评估 Runner — 去 doc_id 化的检索质量评估。

Phase 2: 影子模式 — 与 legacy 指标并行计算，不决定 pass/fail。
Phase 3: 语义门控模式 — sem_context_recall 替代 snippet/chunk 命中。
"""
from __future__ import annotations

import threading

from typing import Any

from backend.evaluation.metrics import (
    answer_similarity_semantic,
    context_precision_semantic,
    context_recall_semantic,
    faithfulness_semantic,
    hallucination_rate,
)
from backend.evaluation.semantic import SemanticScorer, get_eval_scorer
from backend.shared.logger import logger


def _extract_gt_texts(case_expected: dict) -> list[str]:
    """从 ground_truth_context 提取纯文本列表。"""
    gt = case_expected.get("ground_truth_context") or []
    texts: list[str] = []
    for entry in gt:
        if isinstance(entry, dict):
            t = entry.get("text", "")
        elif isinstance(entry, str):
            t = entry
        else:
            t = str(entry)
        if t.strip():
            texts.append(t.strip())
    return texts


def _extract_retrieved_texts(details: list[dict]) -> list[str]:
    """从 details 列表提取检索到的 chunk 全文。"""
    return [d["page_content"] for d in details if d.get("page_content")]


def score_case_semantic(
    question: str,
    details: list[dict],
    case_expected: dict,
    case_metadata: dict,
    scorer: SemanticScorer,
    thresholds: dict[str, float] | None = None,
    generated_answer: str = "",
) -> dict[str, float]:
    """计算单条用例的全部语义指标（RAGAS 对齐命名）。

    Parameters
    ----------
    question : 用户问题
    details : _run_rag 构建的 per-chunk 详情列表（含 page_content）
    case_expected : TestCase.expected 字典
    case_metadata : TestCase.metadata 字典
    scorer : SemanticScorer 实例
    thresholds : 阈值配置（来自 golden_set.json thresholds.semantic）
    generated_answer : LLM 生成的答案（live/ragas 模式下非空）

    Returns
    -------
    dict[str, float] — 语义指标字典，键名以 sem_ 前缀（RAGAS 对齐）
    """
    if thresholds is None:
        thresholds = {}

    threshold = thresholds.get("sem_context_recall_min", 0.50)
    retrieved_texts = _extract_retrieved_texts(details)
    gt_texts = _extract_gt_texts(case_expected)

    metrics: dict[str, float] = {}

    # --- 检索质量语义指标（RAGAS context_recall / context_precision）---
    if gt_texts:
        recall_result = context_recall_semantic(
            retrieved_texts, gt_texts, scorer, threshold=threshold,
        )
        metrics["sem_context_recall"] = recall_result["context_recall"]
        metrics["sem_context_recall_soft"] = recall_result["context_recall_soft"]

        precision_result = context_precision_semantic(
            retrieved_texts, gt_texts, scorer, threshold=threshold,
        )
        metrics["sem_context_precision"] = precision_result["context_precision"]
    else:
        should_reject = case_expected.get("should_reject", False)
        if should_reject:
            metrics["sem_context_recall"] = 1.0
            metrics["sem_context_recall_soft"] = 1.0
            metrics["sem_context_precision"] = 1.0 if not retrieved_texts else 0.0
        else:
            metrics["sem_context_recall"] = float("nan")
            metrics["sem_context_recall_soft"] = float("nan")
            metrics["sem_context_precision"] = float("nan")

    # --- chunk recall@k（语义版）---
    if gt_texts and retrieved_texts:
        for k in (1, 3, 5):
            top_k_texts = retrieved_texts[:k]
            covered = 0
            for gt in gt_texts:
                queries = [gt] * len(top_k_texts)
                scores = scorer.score_pairs(queries, top_k_texts)
                if max(scores) >= threshold:
                    covered += 1
            metrics[f"sem_chunk_recall@{k}"] = round(covered / len(gt_texts), 4)

    # --- 生成质量语义指标（RAGAS faithfulness / answer_correctness）---
    if case_metadata.get("generation_eval"):
        expected_answer = case_metadata.get("expected_answer", "")
        # 只用真实生成回答；无生成答案时不用检索文本冒充（系统性虚高）
        if expected_answer and retrieved_texts and generated_answer:

            faith_result = faithfulness_semantic(
                generated_answer, retrieved_texts, scorer, threshold=threshold,
            )
            # 空答案/无可验证 claim → skipped（None），不写入指标
            if faith_result.get("faithfulness") is not None:
                metrics["sem_faithfulness"] = faith_result["faithfulness"]
                metrics["sem_hallucination_rate"] = hallucination_rate(faith_result)
                metrics["sem_claim_count"] = float(faith_result["claim_count"])

            metrics["sem_answer_correctness"] = answer_similarity_semantic(
                generated_answer, expected_answer, scorer,
            )

    return metrics


_scorer_instance: SemanticScorer | None = None
_scorer_lock = threading.Lock()


def _get_scorer() -> SemanticScorer:
    """延迟初始化 + 缓存 scorer 实例（避免每条用例重复加载模型）。

    双检锁：--workers 并发时防止重复加载模型。
    """
    global _scorer_instance
    if _scorer_instance is None:
        with _scorer_lock:
            if _scorer_instance is None:
                _scorer_instance = get_eval_scorer()
    return _scorer_instance


def compute_semantic_metrics(
    question: str,
    details: list[dict],
    case: Any,
    thresholds: dict[str, float] | None = None,
    generated_answer: str = "",
) -> dict[str, float]:
    """便捷入口：自动获取 scorer 并计算语义指标。

    供 _run_rag() 在 shadow 模式下调用。
    """
    try:
        scorer = _get_scorer()
        return score_case_semantic(
            question=question,
            details=details,
            case_expected=case.expected,
            case_metadata=case.metadata,
            scorer=scorer,
            thresholds=thresholds,
            generated_answer=generated_answer,
        )
    except Exception as e:
        logger.warning(f"[SemanticShadow] {case.id} 语义指标计算失败: {e}")
        return {}
