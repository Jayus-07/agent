"""兼容垫片 — 实际实现已拆分到 evaluator / regression 子模块。"""
from backend.evaluation.gate.evaluator import TIER_THRESHOLDS, evaluate_tiers
from backend.evaluation.gate.regression import (
    BASELINE_ROOT,
    baseline_path,
    check_regression,
    diff_baseline,
    flag_regressions,
    load_baseline,
    promote,
)

__all__ = [
    "BASELINE_ROOT",
    "TIER_THRESHOLDS",
    "baseline_path",
    "check_regression",
    "diff_baseline",
    "evaluate_tiers",
    "flag_regressions",
    "load_baseline",
    "promote",
]
