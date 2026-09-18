"""100 文档基线双跑的可复现性比较器（阶段 0 0.3 / 实施计划 Task 5）。

输入是两份评测报告 JSON，每份必须含：
``context``：``dataset``、``git_commit``、``model``、``embedding_model``、
``config_fingerprint``、``index_version``（任一缺失或不一致 → 不可比）；
``metrics``：六个主指标 ``recall@5 / mrr / ndcg@10 / top1_accuracy /
citation_accuracy / reject_accuracy``（任一缺失或 |Δ| > 容差 → 不通过）。

比较器是纯函数：不读文件、不跑评测、不猜口径。真实双跑必须等
``codex/rag-eval-kb-unification`` 合并后由 CLI 触发；若合并后的评测报告
字段名与本契约不一致，只允许新增适配层，不得放宽比对规则。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

PRIMARY_METRICS: tuple[str, ...] = (
    "recall@5",
    "mrr",
    "ndcg@10",
    "top1_accuracy",
    "citation_accuracy",
    "reject_accuracy",
)
REQUIRED_CONTEXT_KEYS: tuple[str, ...] = (
    "dataset",
    "git_commit",
    "model",
    "embedding_model",
    "config_fingerprint",
    "index_version",
)
DEFAULT_TOLERANCE = 0.005
_DELTA_PRECISION = 6
_FLOAT_EPSILON = 1e-9


@dataclass(frozen=True)
class ReproducibilityResult:
    """双跑比较结果；``comparable`` 指口径一致，``passed`` 指指标全部达标。"""

    comparable: bool
    passed: bool
    tolerance: float
    metric_deltas: dict[str, float] = field(default_factory=dict)
    missing_metrics: list[str] = field(default_factory=list)
    context_mismatches: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _metric_deltas(
    first_metrics: Mapping[str, object],
    second_metrics: Mapping[str, object],
    tolerance: float,
) -> tuple[dict[str, float], list[str], list[str]]:
    deltas: dict[str, float] = {}
    missing: list[str] = []
    exceeded: list[str] = []
    for name in PRIMARY_METRICS:
        first_value = first_metrics.get(name)
        second_value = second_metrics.get(name)
        if not isinstance(first_value, (int, float)) or not isinstance(second_value, (int, float)):
            missing.append(name)
            continue
        delta = round(float(second_value) - float(first_value), _DELTA_PRECISION)
        deltas[name] = delta
        if abs(delta) > tolerance + _FLOAT_EPSILON:
            exceeded.append(name)
    return deltas, missing, exceeded


def compare_eval_runs(
    first: Mapping[str, object],
    second: Mapping[str, object],
    tolerance: float = DEFAULT_TOLERANCE,
) -> ReproducibilityResult:
    """比较两次评测运行；口径不一致或任一主指标差值超限都算失败。"""

    reasons: list[str] = []
    first_context = first.get("context") if isinstance(first.get("context"), Mapping) else {}
    second_context = second.get("context") if isinstance(second.get("context"), Mapping) else {}

    context_mismatches = [
        key
        for key in REQUIRED_CONTEXT_KEYS
        if first_context.get(key) != second_context.get(key)
    ]
    if context_mismatches:
        reasons.append(f"口径不一致: {', '.join(context_mismatches)}")

    first_metrics = first.get("metrics") if isinstance(first.get("metrics"), Mapping) else {}
    second_metrics = second.get("metrics") if isinstance(second.get("metrics"), Mapping) else {}
    deltas, missing, exceeded = _metric_deltas(first_metrics, second_metrics, tolerance)
    if missing:
        reasons.append(f"缺失主指标: {', '.join(missing)}")
    if exceeded:
        reasons.append(f"主指标差值超限 (>{tolerance}): {', '.join(exceeded)}")

    comparable = not context_mismatches
    passed = comparable and not missing and not exceeded
    return ReproducibilityResult(
        comparable=comparable,
        passed=passed,
        tolerance=tolerance,
        metric_deltas=deltas,
        missing_metrics=missing,
        context_mismatches=context_mismatches,
        reasons=reasons,
    )
