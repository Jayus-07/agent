"""tasks/budget_reconciliation_tasks.py — 预算对账日报（beat）。

薄壳：聚合与告警裁决在 backend/infra/llm/reconciliation_report.py
（与 Celery 解耦可测试）。调度见 celery_app.conf.beat_schedule
`budget.reconciliation_check`（每日一次，队列路由登记在
queue_router._BEAT_TASK_ROUTES）。

企业口径（2026-10-02）：对账人只处理聚合异常——窗口未决率超阈值出
告警（degradation.jsonl + 可选 webhook），待对账明细只是下钻材料。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="budget.reconciliation_check")
def budget_reconciliation_check() -> dict:
    """对账日报：聚合两路数据源，未决率超阈值时出告警。"""
    from backend.infra.llm.reconciliation_report import (
        build_reconciliation_report,
        evaluate_reconciliation_alert,
    )

    try:
        report = build_reconciliation_report()
        alert = evaluate_reconciliation_alert(report)
        if alert is not None:
            from backend.observability.alerts import log_degradation, make_alert

            log_degradation(make_alert(alert["code"], detail=alert))
            logger.warning(
                "[BudgetReconcile] 未决率告警 ratio=%s (%s/%s)",
                alert["ratio"], alert["needs_review"],
                alert["total_reservations"],
            )
        quota = report.get("quota") or {}
        return {
            "needs_review_ratio_window": quota.get("needs_review_ratio_window"),
            "reservations_total_window": quota.get("reservations_total_window"),
            "estimated_share": (report.get("derived") or {}).get("estimated_share"),
            "alerted": alert is not None,
        }
    except Exception:
        logger.error("[BudgetReconcileTask] 对账日报失败", exc_info=True)
        return {"alerted": False, "error": "reconciliation report failed"}
