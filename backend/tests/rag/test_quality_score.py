"""TD-09 质量评分回归（2026-10-02）。

此前 quality_score 列恒为默认 0（从未计算）：每份文档都在管理端触发
「质量评分偏低 (0/100)」告警——纯噪音且掩盖真实质量差异。钉死公式与
接线行为：score = 100 − 40×hard − 10×warn，下限 0。
"""
import pytest

from backend.rag.preprocessing.quality_gate import compute_quality_score


def test_clean_document_scores_full():
    assert compute_quality_score([]) == 100


def test_warns_deduct_ten_each():
    warns = [{"check": "clean_ratio", "severity": "warn", "detail": "x"},
             {"check": "table", "severity": "warn", "detail": "y"}]
    assert compute_quality_score(warns) == 80


def test_hard_deducts_forty():
    hard = [{"check": "cleaned_chars", "severity": "hard", "detail": "x"}]
    assert compute_quality_score(hard) == 60


def test_floor_at_zero():
    many = ([{"check": f"h{i}", "severity": "hard", "detail": ""} for i in range(3)])
    assert compute_quality_score(many) == 0


def test_missing_severity_counts_as_warn():
    """历史兼容：无 severity 字段的异常按警告计（旧记录格式）。"""
    assert compute_quality_score([{"check": "legacy"}]) == 90
