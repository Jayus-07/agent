"""配对二分类结果的描述统计、精确检验和多重比较校正。"""

from __future__ import annotations

import math
from typing import Any


def _wilson_interval(successes: int, trials: int, z: float) -> tuple[float, float]:
    proportion = successes / trials
    z_squared = z * z
    denominator = 1 + z_squared / trials
    center = (proportion + z_squared / (2 * trials)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / trials + z_squared / (4 * trials * trials)
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _exact_mcnemar_p_value(candidate_only: int, baseline_only: int) -> float:
    discordant = candidate_only + baseline_only
    if discordant == 0:
        return 1.0

    tail_count = min(candidate_only, baseline_only)
    log_probability = (
        math.lgamma(discordant + 1)
        - math.lgamma(tail_count + 1)
        - math.lgamma(discordant - tail_count + 1)
        - discordant * math.log(2)
    )
    term = math.exp(log_probability)
    tail_probability = term
    for index in range(tail_count, 0, -1):
        term *= index / (discordant - index + 1)
        tail_probability += term
    return min(1.0, 2 * tail_probability)


def holm_adjust_p_values(p_values: list[float]) -> list[float]:
    """按原顺序返回 Holm step-down 家族错误率校正后的 p 值。"""
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in p_values):
        raise ValueError("p 值必须是 0 到 1 之间的有限数值")
    ranked = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    adjusted = [0.0] * len(p_values)
    running_max = 0.0
    family_size = len(p_values)
    for rank, (original_index, p_value) in enumerate(ranked):
        running_max = max(running_max, (family_size - rank) * p_value)
        adjusted[original_index] = min(1.0, running_max)
    return adjusted


def paired_binary_inference(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """计算配对通过率差、95% Wilson 区间和双侧精确 McNemar p 值。

    只有两边都为 pass/fail 的样本进入统计；错误、跳过及未知状态会被排除。
    置信区间把 discordant pair 中 B 获胜比例的 Wilson 区间映射回总样本
    的通过率差，因此是近似区间；p 值为条件精确检验。
    """
    candidate_only = 0
    baseline_only = 0
    sample_count = 0
    excluded_count = 0
    for case in cases:
        baseline = case.get("baseline")
        candidate = case.get("candidate")
        if baseline not in {"pass", "fail"} or candidate not in {"pass", "fail"}:
            excluded_count += 1
            continue
        sample_count += 1
        if baseline == "fail" and candidate == "pass":
            candidate_only += 1
        elif baseline == "pass" and candidate == "fail":
            baseline_only += 1

    discordant_count = candidate_only + baseline_only
    delta = (candidate_only - baseline_only) / sample_count if sample_count else 0.0
    ci_lower: float | None = None
    ci_upper: float | None = None
    if sample_count and discordant_count:
        direction_lower, direction_upper = _wilson_interval(
            candidate_only, discordant_count, 1.959963984540054
        )
        discordance_rate = discordant_count / sample_count
        ci_lower = discordance_rate * (2 * direction_lower - 1)
        ci_upper = discordance_rate * (2 * direction_upper - 1)

    return {
        "sample_count": sample_count,
        "excluded_count": excluded_count,
        "discordant_count": discordant_count,
        "candidate_only_pass": candidate_only,
        "baseline_only_pass": baseline_only,
        "pass_rate_delta": round(delta, 8),
        "ci95_lower": round(ci_lower, 8) if ci_lower is not None else None,
        "ci95_upper": round(ci_upper, 8) if ci_upper is not None else None,
        "p_value": round(_exact_mcnemar_p_value(candidate_only, baseline_only), 12),
        "p_value_method": "exact_mcnemar_two_sided",
        "confidence_interval_method": "paired_wilson_95",
    }
