"""分层阈值评估。"""
from __future__ import annotations

from backend.evaluation.models import EvalResult, TestCase, TierSummary

TIER_THRESHOLDS: dict[str, float] = {
    "smoke": 0.95,
    "core": 0.85,
    "hard": 0.70,
    "regression": 1.00,
    "all": 0.85,
}


def evaluate_tiers(
    cases: list[TestCase],
    results: list[EvalResult],
    thresholds: dict[str, float] | None = None,
) -> list[TierSummary]:
    """按 tier 分组构建分层汇总 — 用于 CI/CD 分层卡点。"""
    thresholds = thresholds or TIER_THRESHOLDS
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
        passed = sum(
            1 for c in tc
            if result_by_id.get(c.id, EvalResult(
                case_id=c.id, module=c.module, status="skip",
                expected=c.expected, actual={},
            )).status == "pass"
        )
        total = len(tc)
        pass_rate = round(passed / max(total, 1), 4)
        threshold = thresholds.get(tier_name, 0.85)
        summaries.append(TierSummary(
            tier=tier_name, total=total, passed=passed,
            failed=total - passed, pass_rate=pass_rate,
            threshold=threshold,
            passed_threshold=pass_rate >= threshold,
        ))

    return summaries
