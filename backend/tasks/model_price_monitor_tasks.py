"""供应商模型价格页每日巡检任务。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from celery.signals import task_revoked
from celery.exceptions import SoftTimeLimitExceeded, TaskRevokedError

from backend.infra.llm.price_page_monitor import (
    PostgresPricePageMonitorRepository,
    PriceQuote,
    fetch_official_prices,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app

_SOURCES = {
    "volcengine_ark_doubao_seed_2_0_mini": {
        "name": "火山方舟 Doubao Seed 2.0 mini",
        "url": (
            "https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing"
        ),
    },
    "aliyun_qwen3_7_text_embedding": {
        "name": "阿里云 Qwen3.7 Text Embedding",
        "url": "https://help.aliyun.com/en/model-studio/embedding",
    },
    "aliyun_qwen3_7_text_rerank": {
        "name": "阿里云 Qwen3.7 Text Rerank",
        "url": "https://help.aliyun.com/en/model-studio/qwen3-7-text-rerank",
    },
}


class ModelPriceMonitorExecutionFailed(RuntimeError):
    """让 Celery 的任务终态与巡检失败记录保持一致。"""


def _current_celery_task_id() -> str | None:
    """读取当前任务 ID，用于将运行记录关联到 Celery 生命周期事件。"""
    try:
        from celery import current_task

        task_id = current_task.request.id
    except Exception:
        return None
    return str(task_id) if task_id else None


@task_revoked.connect
def _record_price_monitor_revocation(
    sender=None, request=None, terminated=False, **kwargs,
):
    """消费 Celery revoke 信号；任务体不会收到预执行 revoke 异常。"""
    del kwargs
    if getattr(sender, "name", None) != "model.price_page_monitor":
        return
    task_id = str(getattr(request, "id", "") or "")
    if not task_id:
        return
    try:
        PostgresPricePageMonitorRepository().record_revoked_task(
            task_id, terminated=bool(terminated),
        )
    except Exception as exc:
        logger.error(
            "[ModelPriceMonitor] 无法记录 Celery revoke task_id=%s error_type=%s",
            task_id, type(exc).__name__,
        )


def _finish_interrupted_run(
    repo,
    *,
    run_id: str,
    status: str,
    error: BaseException,
    source_results: list[dict[str, object]],
    comparison,
    comparison_results: dict[str, object],
    alert_events: list[dict[str, object]],
) -> None:
    """尽力保存任务中断状态，同时由 Celery 保留原始终态异常。"""
    if repo is not None and comparison and comparison.source_changes:
        try:
            repo.release_source_change_alerts([
                str(change["event_id"])
                for change in comparison.source_changes
            ])
        except Exception as release_exc:
            logger.error(
                "[ModelPriceMonitor] 无法归还待处理告警 run_id=%s error_type=%s",
                run_id, type(release_exc).__name__,
            )
    try:
        repo.finish_run(
            run_id,
            status=status,
            finished_at=datetime.now(ZoneInfo("Asia/Shanghai")),
            sources_failed=sum(
                1 for source in source_results if source["status"] == "failed"
            ) if source_results else 0,
            quotes_recorded=comparison.recorded_count if comparison else 0,
            source_changes=(
                len(comparison.source_changes) if comparison else 0
            ),
            system_mismatches=(
                len(comparison.system_mismatches) if comparison else 0
            ),
            source_results=source_results,
            comparison_results=comparison_results,
            alert_events=alert_events,
            error_type=type(error).__name__,
        )
    except Exception as finish_exc:
        logger.error(
            "[ModelPriceMonitor] 无法收敛中断运行状态 run_id=%s error_type=%s",
            run_id, type(finish_exc).__name__,
        )


def _source_results(
    quotes: list[PriceQuote], failures: list[dict[str, str]]
) -> list[dict[str, object]]:
    quotes_by_source: dict[str, list[PriceQuote]] = defaultdict(list)
    for quote in quotes:
        quotes_by_source[quote.source_id].append(quote)
    failure_by_source = {item["source_id"]: item["error_type"] for item in failures}
    results: list[dict[str, object]] = []
    for source_id, source in _SOURCES.items():
        source_quotes = quotes_by_source[source_id]
        error_type = failure_by_source.get(source_id)
        ok = bool(source_quotes) and error_type is None
        results.append({
            "source_id": source_id,
            "name": source["name"],
            "url": source["url"],
            "status": "succeeded" if ok else "failed",
            "error_type": error_type or (None if source_quotes else "NoPriceQuotes"),
            "quote_count": len(source_quotes),
            "quotes": [
                {
                    "model_name": quote.model_name,
                    "component": quote.component,
                    "dimension": quote.dimension,
                    "price_per_unit": str(quote.price_per_unit),
                    "unit": quote.unit,
                    "currency": quote.currency,
                    "source_updated_at": quote.source_updated_at,
                    "content_sha256": quote.content_sha256,
                }
                for quote in source_quotes
            ],
        })
    return results


@celery_app.task(name="model.price_page_monitor")
def monitor_model_price_pages() -> dict[str, object]:
    """读取三份官方价目页，与前次观测及当前有效价格比较并告警。"""
    from backend.observability.alerts import log_degradation, make_alert

    started_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    observed_on = started_at.date()
    run_id = str(uuid4())
    alert_events: list[dict[str, object]] = []
    quotes: list[PriceQuote] = []
    failures: list[dict[str, str]] = []
    source_results: list[dict[str, object]] = []
    comparison = None
    comparison_results: dict[str, object] = {}

    def emit_alert(code: str, detail: dict[str, object]) -> bool:
        try:
            log_degradation(make_alert(code, detail))
        except Exception as exc:
            logger.warning(
                "[ModelPriceMonitor] 告警触发失败 code=%s error_type=%s",
                code, type(exc).__name__,
            )
            return False
        # log_degradation 是异步 best-effort 通道；这里只记录触发，不声称 webhook 已送达。
        alert_events.append({"code": code, "recorded": True, "delivery": "unknown"})
        return True

    repo = None
    try:
        repo = PostgresPricePageMonitorRepository()
        repo.start_run(
            run_id,
            observed_on=observed_on,
            started_at=started_at,
            sources_total=len(_SOURCES),
            celery_task_id=_current_celery_task_id(),
        )
    except (SoftTimeLimitExceeded, TaskRevokedError) as exc:
        _finish_interrupted_run(
            repo,
            run_id=run_id,
            status=("cancelled" if isinstance(exc, TaskRevokedError) else "failed"),
            error=exc,
            source_results=source_results,
            comparison=comparison,
            comparison_results=comparison_results,
            alert_events=alert_events,
        )
        raise
    except Exception as exc:
        emit_alert(
            "MODEL_PRICE_MONITOR_FAILED",
            {"observed_on": observed_on.isoformat(), "error_type": type(exc).__name__},
        )
        logger.error(
            "[ModelPriceMonitor] 无法创建运行记录 error_type=%s",
            type(exc).__name__,
        )
        raise ModelPriceMonitorExecutionFailed(
            f"无法创建运行记录: {type(exc).__name__}"
        ) from exc

    try:
        quotes, failures = fetch_official_prices()
        source_results = _source_results(quotes, failures)
        failed_source_count = sum(
            1 for source in source_results if source["status"] == "failed"
        )
        if failed_source_count:
            emit_alert(
                "MODEL_PRICE_SOURCE_UNAVAILABLE",
                {
                    "observed_on": observed_on.isoformat(),
                    "sources": [
                        {
                            "source_id": source["source_id"],
                            "error_type": source["error_type"],
                        }
                        for source in source_results if source["status"] == "failed"
                    ],
                },
            )

        comparison = repo.record_and_compare(quotes, observed_on=observed_on)
        if comparison.source_changes:
            alert_triggered = emit_alert(
                "MODEL_PRICE_SOURCE_CHANGED",
                {
                    "observed_on": observed_on.isoformat(),
                    "event_ids": [
                        change["event_id"]
                        for change in comparison.source_changes
                    ],
                    "changes": comparison.source_changes,
                },
            )
            if not alert_triggered:
                raise RuntimeError("价格变化告警未成功触发")
            else:
                triggered_at = datetime.now(ZoneInfo("UTC"))
                for change in comparison.source_changes:
                    repo.mark_source_change_alert_triggered(
                        str(change["event_id"]), triggered_at=triggered_at,
                    )
        if comparison.system_mismatches:
            emit_alert(
                "MODEL_PRICE_SYSTEM_MISMATCH",
                {
                    "observed_on": observed_on.isoformat(),
                    "mismatches": comparison.system_mismatches,
                },
            )

        status = (
            "failed" if not quotes
            else "partial" if failed_source_count
            else "succeeded"
        )
        finished_at = datetime.now(ZoneInfo("Asia/Shanghai"))
        comparison_results = {
            "prices": comparison.prices,
            "source_changes": comparison.source_changes,
            "system_mismatches": comparison.system_mismatches,
        }
        repo.finish_run(
            run_id,
            status=status,
            finished_at=finished_at,
            sources_failed=failed_source_count,
            quotes_recorded=comparison.recorded_count,
            source_changes=len(comparison.source_changes),
            system_mismatches=len(comparison.system_mismatches),
            source_results=source_results,
            comparison_results=comparison_results,
            alert_events=alert_events,
            error_type="NoPriceQuotes" if not quotes else None,
        )
        logger.info(
            "[ModelPriceMonitor] 巡检完成 run_id=%s observed_on=%s quotes=%d "
            "changed=%d mismatches=%d failed_sources=%d status=%s",
            run_id, observed_on.isoformat(), comparison.recorded_count,
            len(comparison.source_changes), len(comparison.system_mismatches),
            failed_source_count, status,
        )
        if status == "failed":
            raise ModelPriceMonitorExecutionFailed(
                "所有官方价格来源均不可用或未解析到价格"
            )
        return {
            "ok": status == "succeeded",
            "run_id": run_id,
            "observed_on": observed_on.isoformat(),
            "status": status,
            "recorded": comparison.recorded_count,
            "source_changes": len(comparison.source_changes),
            "system_mismatches": len(comparison.system_mismatches),
            "sources_failed": failed_source_count,
        }
    except ModelPriceMonitorExecutionFailed:
        raise
    except (SoftTimeLimitExceeded, TaskRevokedError) as exc:
        _finish_interrupted_run(
            repo,
            run_id=run_id,
            status=("cancelled" if isinstance(exc, TaskRevokedError) else "failed"),
            error=exc,
            source_results=source_results,
            comparison=comparison,
            comparison_results=comparison_results,
            alert_events=alert_events,
        )
        raise
    except Exception as exc:
        emit_alert(
            "MODEL_PRICE_MONITOR_FAILED",
            {"observed_on": observed_on.isoformat(), "error_type": type(exc).__name__},
        )
        logger.error(
            "[ModelPriceMonitor] 巡检保存或价格读取失败 run_id=%s error_type=%s",
            run_id, type(exc).__name__,
        )
        _finish_interrupted_run(
            repo,
            run_id=run_id,
            status="failed",
            error=exc,
            source_results=source_results,
            comparison=comparison,
            comparison_results=comparison_results,
            alert_events=alert_events,
        )
        raise ModelPriceMonitorExecutionFailed(
            f"巡检保存或价格读取失败: {type(exc).__name__}"
        ) from exc
