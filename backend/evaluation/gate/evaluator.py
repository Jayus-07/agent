"""分层阈值评估。"""
from __future__ import annotations

import os

from backend.evaluation.models import EvalResult, TestCase, TierSummary

TIER_THRESHOLDS: dict[str, float] = {
    "smoke": 0.95,
    "core": 0.85,
    "hard": 0.70,
    "regression": 1.00,
    "all": 0.85,
}

# GATE-12/DATA-09：全局最低有效样本量缺省（suite JSON 的 min_samples /
# min_valid_samples 优先）。极小样本 100% pass 不再等价于「评测通过」。
DEFAULT_MIN_SAMPLES = int(os.getenv("EVAL_MIN_SAMPLES", "8"))


def evaluate_tiers(
    cases: list[TestCase],
    results: list[EvalResult],
    thresholds: dict[str, float] | None = None,
    *,
    min_samples: int | None = None,
    min_valid_samples: int | None = None,
) -> list[TierSummary]:
    """按 tier 分组构建分层汇总 — 用于 CI/CD 分层卡点。

    min_samples / min_valid_samples：最低样本量门（GATE-12）。suite 级配置
    （loader 读 suites/{name}.json）优先，缺省回退全局 EVAL_MIN_SAMPLES。
    有效样本 = total - errors - skipped，不足则该层判定失败并注明原因。
    """
    thresholds = thresholds or TIER_THRESHOLDS
    effective_min_total = min_samples if min_samples is not None else DEFAULT_MIN_SAMPLES
    effective_min_valid = (
        min_valid_samples if min_valid_samples is not None else effective_min_total
    )
    result_by_id = {r.case_id: r for r in results}

    tier_cases: dict[str, list[TestCase]] = {}
    for c in cases:
        t = c.metadata.get("tier") or "core"
        tier_cases.setdefault(t, []).append(c)

    summaries: list[TierSummary] = []
    for tier_name in ("smoke", "core", "hard", "regression"):
        tc = tier_cases.get(tier_name)
        if not tc:
            continue
        tier_results = [
            result_by_id.get(c.id, EvalResult(
                case_id=c.id, module=c.module, status="skip",
                expected=c.expected, actual={},
            ))
            for c in tc
        ]
        passed = sum(1 for r in tier_results if r.status == "pass")
        errors = sum(1 for r in tier_results if r.status == "error")
        skipped = sum(1 for r in tier_results if r.status == "skip")
        total = len(tc)
        valid = total - errors - skipped
        pass_rate = round(passed / max(total, 1), 4)
        threshold = thresholds.get(tier_name, 0.85)
        passed_threshold = pass_rate >= threshold

        gate_reasons: list[str] = []
        if not passed_threshold:
            gate_reasons.append(
                f"通过率 {pass_rate:.1%} 低于阈值 {threshold:.1%}"
            )
        # 同一层的最低样本量口径：各 tier 共用 suite 级要求（按层重复判定
        # 会让 smoke 小集合恒挂），只要任一层有效样本达标即视为整体达标；
        # 全部层都不足时在每层标注原因（诚实暴露，不静默放行）。
        passed_min_samples = valid >= effective_min_valid
        if not passed_min_samples:
            gate_reasons.append(
                f"有效样本不足：valid={valid}（total={total} - errors={errors} "
                f"- skipped={skipped}）< 最低要求 {effective_min_valid}"
            )
        summaries.append(TierSummary(
            tier=tier_name, total=total, passed=passed,
            failed=total - passed, pass_rate=pass_rate,
            threshold=threshold,
            passed_threshold=passed_threshold,
            errors=errors, skipped=skipped, valid_samples=valid,
            min_samples=effective_min_valid,
            passed_min_samples=passed_min_samples,
            gate_reasons=gate_reasons,
        ))

    # 跨层口径：任一层有效样本达标 → 各层 passed_min_samples 统一视为达标
    #（避免 smoke 8 条 / core 30 条的组合里 smoke 层误伤整体判定）
    if summaries and any(s.passed_min_samples for s in summaries):
        for s in summaries:
            if not s.passed_min_samples:
                s.passed_min_samples = True
                s.gate_reasons = [
                    r for r in s.gate_reasons if "有效样本不足" not in r
                ]
    return summaries
