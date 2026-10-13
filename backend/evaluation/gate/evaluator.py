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
    有效样本 = total - errors - skipped；按套件全部层之和判定，不足时
    各层均标注原因（fail-closed，不静默放行）。
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
        summaries.append(TierSummary(
            tier=tier_name, total=total, passed=passed,
            failed=total - passed, pass_rate=pass_rate,
            threshold=threshold,
            passed_threshold=passed_threshold,
            errors=errors, skipped=skipped, valid_samples=valid,
            min_samples=effective_min_valid,
            # GATE-12 按套件总量判定，此处占位，循环后统一覆盖。
            passed_min_samples=True,
            gate_reasons=gate_reasons,
        ))

    # GATE-12 套件级口径：最低样本量是 suite 全局要求（suite JSON 的
    # min_samples / min_valid_samples 优先，缺省全局缺省值），按全部层
    # 有效样本之和判定。按层重复判定会制造数学死锁——如 smoke(19)+hard(10)
    # 的 29 用例套件单层永远达不到 20，无论质量多高分层的样本量门恒失败。
    suite_valid = sum(s.valid_samples for s in summaries)
    suite_min_ok = suite_valid >= effective_min_valid
    for s in summaries:
        s.passed_min_samples = suite_min_ok
        if not suite_min_ok:
            s.gate_reasons.append(
                f"有效样本不足：套件总 valid={suite_valid}（各层之和）"
                f"< 最低要求 {effective_min_valid}"
            )
    return summaries
