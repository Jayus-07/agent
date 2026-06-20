"""Evaluation Framework — Agent Platform 评估体系。

Usage:
    python -m evaluation                    # 全量离线评估
    python -m evaluation rag --live         # RAG 真实检索评估
    python -m evaluation --live --judge     # 全量 + LLM评分

Public API:
    from evaluation import run_all, load_dataset
"""

from evaluation.runner import run_all
from evaluation.dataset import load_dataset
from evaluation.models import TestCase, EvalResult, EvalReport, ModuleSummary
from evaluation.report import print_summary, write_markdown_report, compare_reports
from evaluation.metrics import (
    recall_at_k, mrr, ndcg_at_k, jaccard_similarity, exact_match, result_set_match,
)

__all__ = [
    "run_all",
    "load_dataset",
    "TestCase",
    "EvalResult",
    "EvalReport",
    "ModuleSummary",
    "print_summary",
    "write_markdown_report",
    "compare_reports",
    "recall_at_k",
    "mrr",
    "ndcg_at_k",
    "jaccard_similarity",
    "exact_match",
    "result_set_match",
]
