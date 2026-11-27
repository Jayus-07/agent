from __future__ import annotations

from decimal import Decimal
from backend.shared.logger import logger

def _record_quota_metric(
    metric_name: str,
    *,
    scope_type: str = "unknown",
    period_type: str = "unknown",
    result: str,
    threshold: str = "",
) -> None:
    """记录预算指标；指标链路异常不得改变预算结算语义。"""
    try:
        from backend.observability import metrics

        if metric_name == "budget_threshold_total":
            metrics.budget_threshold_total.labels(
                scope_type=scope_type,
                period_type=period_type,
                threshold=threshold,
            ).inc()
        else:
            metrics.budget_quota_total.labels(
                scope_type=scope_type,
                period_type=period_type,
                result=result,
            ).inc()
    except Exception as exc:
        # 预算本身已经由 PG 事务保护；Prometheus 不可用不能改变业务结果。
        logger.debug("[Quota] budget metric write failed: %s", exc)


def _insert_threshold_events(
    cur,
    events_table: str,
    *,
    scope_type: str,
    scope_id: str,
    period_type: str,
    period_start,
    used,
    reserved,
    limit,
) -> None:
    ratio = (Decimal(str(used)) + Decimal(str(reserved))) / Decimal(str(limit))
    for threshold in (Decimal("0.8000"), Decimal("1.0000")):
        if ratio >= threshold:
            cur.execute(
                f"""INSERT INTO {events_table} (
                            scope_type, scope_id, period_type, period_start, threshold
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING""",
                (scope_type, scope_id, period_type, period_start, threshold),
            )
            if cur.rowcount == 1:
                _record_quota_metric(
                    "budget_threshold_total",
                    scope_type=scope_type,
                    period_type=period_type,
                    result="created",
                    threshold=f"{threshold:.4f}",
                )
