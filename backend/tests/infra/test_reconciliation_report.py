"""reconciliation_report 单测——对账日报的聚合与告警裁决（纯逻辑）。

企业口径（2026-10-02）：人只处理聚合异常。裁决必须验证三个边界——
样本下限（防小流量假突刺）、数据源降级不误报、阈值边界（等于不算超）。
"""
from __future__ import annotations

from backend.infra.llm.reconciliation_report import (
    build_reconciliation_report,
    evaluate_reconciliation_alert_with,
)


def _report(total: int, reviewed: int, ratio: float) -> dict:
    return {
        "quota": {
            "reservations_total_window": total,
            "needs_review_window": reviewed,
            "needs_review_ratio_window": ratio,
            "window_hours": 24,
            "by_reason": {"settle_failed": reviewed},
        },
        "usage": {"total_calls": 100, "estimated_calls": 3, "by_status": {"exact": 97}},
        "derived": {"estimated_share": 0.03},
    }


def test_alert_not_fired_below_min_samples():
    """样本量不足：1/1=100% 也不得告警（防小流量假突刺）。"""
    assert evaluate_reconciliation_alert_with(
        _report(total=1, reviewed=1, ratio=1.0),
        ratio_threshold=0.02, min_samples=20,
    ) is None


def test_alert_not_fired_at_or_below_threshold():
    """等于阈值不算超（只报严格大于），低于阈值更不报。"""
    assert evaluate_reconciliation_alert_with(
        _report(total=100, reviewed=2, ratio=0.02),
        ratio_threshold=0.02, min_samples=20,
    ) is None
    assert evaluate_reconciliation_alert_with(
        _report(total=100, reviewed=1, ratio=0.01),
        ratio_threshold=0.02, min_samples=20,
    ) is None


def test_alert_fired_above_threshold_with_enough_samples():
    alert = evaluate_reconciliation_alert_with(
        _report(total=100, reviewed=10, ratio=0.1),
        ratio_threshold=0.02, min_samples=20,
    )
    assert alert is not None
    assert alert["code"] == "BUDGET_NEEDS_REVIEW_RATIO_HIGH"
    assert alert["ratio"] == 0.1
    assert alert["needs_review"] == 10
    assert alert["total_reservations"] == 100


def test_alert_silent_when_quota_source_degraded():
    """quota 数据源故障（error 段）不告警——缺数据 ≠ 异常，防误报。"""
    report = {"quota": {"error": "quota summary 不可用"}, "usage": {}, "derived": {}}
    assert evaluate_reconciliation_alert_with(
        report, ratio_threshold=0.02, min_samples=20,
    ) is None


def test_build_report_derives_estimated_share():
    """聚合段：estimated_share = estimated_calls / total_calls。"""
    fake_summary = {"pending_count": 0, "needs_review_ratio_window": 0.0}

    class _FakeUsageStore:
        def cost_status_summary(self, hours: float = 24):
            return {"window_hours": hours, "total_calls": 8,
                    "by_status": {"exact": 6, "estimated": 2},
                    "estimated_calls": 2}

    import sys
    from backend.observability import llm_usage_store as store_mod
    original_get = store_mod.get_llm_usage_store
    store_mod.get_llm_usage_store = lambda: _FakeUsageStore()
    try:
        report = build_reconciliation_report(quota_summary=fake_summary)
    finally:
        store_mod.get_llm_usage_store = original_get
    assert report["quota"] is fake_summary  # 传入的汇总原样复用（不重复查库）
    assert report["derived"]["estimated_share"] == 0.25
    assert report["usage"]["estimated_calls"] == 2
