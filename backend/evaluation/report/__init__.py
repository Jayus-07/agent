"""兼容垫片 — 实际实现已拆分到 builder / markdown / json 子模块。"""
from backend.evaluation.release_gate import (  # noqa: F401 — 门禁裁决唯一出口
    GateVerdict,
    compute_module_release_gate,
)
from backend.evaluation.report.builder import (
    DEFAULT_THRESHOLDS,
    METRIC_LABELS,
    METRIC_LAYERS,
    METRIC_SOURCE,
    MODULE_LABELS,
    STATUS_ICONS,
    STATUS_LABELS,
    _categorize_metric,
    compute_dataset_validation,
    compute_performance_stats,
    compute_query_type_stats,
    compute_regression_diff,
    compute_release_gate,
    compute_reject_hallucination_pareto,
    print_summary,
)
from backend.evaluation.report.json import write_json_report
from backend.evaluation.report.markdown import write_markdown_report

__all__ = [
    "DEFAULT_THRESHOLDS",
    "GateVerdict",
    "METRIC_LABELS",
    "METRIC_LAYERS",
    "METRIC_SOURCE",
    "MODULE_LABELS",
    "STATUS_ICONS",
    "STATUS_LABELS",
    "_categorize_metric",
    "compute_dataset_validation",
    "compute_module_release_gate",
    "compute_performance_stats",
    "compute_query_type_stats",
    "compute_regression_diff",
    "compute_release_gate",
    "compute_reject_hallucination_pareto",
    "print_summary",
    "write_json_report",
    "write_markdown_report",
]
