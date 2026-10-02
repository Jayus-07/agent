"""reconciliation_report — 预算对账日报的聚合与告警裁决（纯逻辑）。

企业口径（2026-10-02）：对账是**聚合级**的——人只处理比率异常，永不逐笔
核对流水。本模块把两路数据源拼成一张日报：
  - 预占台账（quota store）：未决队列规模 + 窗口未决率（needs_review 占比）
  - 用量明细（llm_usage）：cost_status 分布（estimated 估算结算占比等）

告警裁决（evaluate_reconciliation_alert）是纯函数：窗口未决率超过阈值且
样本量足 → 出告警 payload；样本下限防止小流量期 1/1=100% 的假突刺。
Celery 日报任务与 budgets 路由共用同一份构建逻辑（G2：不建第二份口径）。
"""
from __future__ import annotations

from typing import Any


def build_reconciliation_report(
    quota_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """拼装对账日报（软失败：单路数据源故障只降级该段，不抛异常）。

    quota_summary 可传调用方已查好的汇总（如 budgets 路由里 sweep 之后
    现成的 reconciliation_summary），避免同一窗口查两遍。
    """
    if quota_summary is None:
        try:
            from backend.infra.llm.quota import PostgresQuotaStore

            quota_summary = PostgresQuotaStore().reconciliation_summary()
        except Exception as exc:
            quota_summary = {"error": f"quota summary 不可用: {exc}"}

    usage: dict[str, Any] = {}
    try:
        from backend.config.budget import BUDGET_RECONCILE_WINDOW_HOURS
        from backend.observability.llm_usage_store import get_llm_usage_store

        usage = get_llm_usage_store().cost_status_summary(
            hours=float(BUDGET_RECONCILE_WINDOW_HOURS),
        )
    except Exception as exc:
        usage = {"error": f"usage summary 不可用: {exc}"}

    usage_total = int(usage.get("total_calls") or 0)
    estimated_calls = int(usage.get("estimated_calls") or 0)
    return {
        "quota": quota_summary,
        "usage": usage,
        "derived": {
            # estimated 结算占比：估算兜底的工作量指标（越高说明 provider
            # 回 usage 尾帧越不可靠，需关注供应商链路）
            "estimated_share": (
                round(estimated_calls / usage_total, 4) if usage_total else 0.0
            ),
        },
    }


def evaluate_reconciliation_alert(report: dict[str, Any]) -> dict[str, Any] | None:
    """窗口未决率超阈值且样本量足 → 返回告警 payload；否则 None。

    阈值与样本下限读 config.budget 默认值；测试用
    evaluate_reconciliation_alert_with 注入自定义阈值。
    """
    from backend.config.budget import (
        BUDGET_RECONCILE_ALERT_MIN_SAMPLES,
        BUDGET_RECONCILE_ALERT_RATIO,
    )
    return evaluate_reconciliation_alert_with(
        report,
        ratio_threshold=float(BUDGET_RECONCILE_ALERT_RATIO),
        min_samples=int(BUDGET_RECONCILE_ALERT_MIN_SAMPLES),
    )


def evaluate_reconciliation_alert_with(
    report: dict[str, Any],
    *,
    ratio_threshold: float,
    min_samples: int,
) -> dict[str, Any] | None:
    """可注入阈值的裁决实现（测试用纯函数）。"""
    quota = report.get("quota") or {}
    if "error" in quota:
        return None  # 数据源降级时不告警——缺数据≠异常，避免误报
    total = int(quota.get("reservations_total_window") or 0)
    reviewed = int(quota.get("needs_review_window") or 0)
    ratio = float(quota.get("needs_review_ratio_window") or 0.0)
    if total < min_samples:
        return None
    if ratio <= ratio_threshold:
        return None
    return {
        "code": "BUDGET_NEEDS_REVIEW_RATIO_HIGH",
        "ratio": ratio,
        "threshold": ratio_threshold,
        "window_hours": quota.get("window_hours"),
        "needs_review": reviewed,
        "total_reservations": total,
        "by_reason": quota.get("by_reason") or {},
        "hint": "请到管理端预算页按 review_reason 分布排查系统性成因",
    }


__all__ = [
    "build_reconciliation_report",
    "evaluate_reconciliation_alert",
    "evaluate_reconciliation_alert_with",
]
