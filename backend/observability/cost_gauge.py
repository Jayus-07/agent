"""observability/cost_gauge.py — 账本成本 Prometheus 投影（2026-10-06）。

G2 单一事实源：llm_usage（PG 账本）是成本唯一权威，Prometheus 侧不做任何
记账——本模块只把账本聚合值周期投影成 gauge（llm_usage_cost_cny_24h /
llm_usage_cost_cny_month），供 Grafana 域对比看板与告警消费。投影语义：
这里的数字永远由账本算出，Prometheus 不独立累加，杜绝双轨漂移；账本侧的
事后修正（估算回填/汇率重算）下一轮刷新自动跟随，无需回溯指标。

模式对齐 customer_service.qa_report.start_faq_gauge_refresher：app 进程
daemon 线程周期拉取、旁路软失败，不影响业务链路。
"""
from __future__ import annotations

import threading
import time

from backend.shared.logger import logger

_INTERVAL_S = 600
_MAX_SERIES = 200

_thread: threading.Thread | None = None


def _utc_ts(days_ago: float = 0) -> str:
    """llm_usage.ts 同格式（YYYY-MM-DDTHH:MM:SS，ISO 字典序可比）的 UTC 时刻。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days_ago * 86400))


def _month_start_iso() -> str:
    t = time.gmtime()
    return f"{t.tm_year:04d}-{t.tm_mon:02d}-01T00:00:00"


def refresh_once() -> int:
    """拉一次账本投影并刷 gauge。返回本次投影的 series 数（测试断言用）。"""
    from backend.observability import metrics
    from backend.observability.llm_usage_store import get_llm_usage_store

    store = get_llm_usage_store()
    rows_24h = store.cost_gauge_snapshot(_utc_ts(1.0))
    rows_month = store.cost_gauge_snapshot(_month_start_iso())
    for gauge, rows in (
        (metrics.llm_usage_cost_cny_24h, rows_24h),
        (metrics.llm_usage_cost_cny_month, rows_month),
    ):
        gauge.clear()
        for row in rows[:_MAX_SERIES]:
            gauge.labels(domain=row["domain"], model=row["model"]).set(row["cost_cny"])
    return len(rows_24h) + len(rows_month)


def start_cost_gauge_refresher(interval_s: float = _INTERVAL_S) -> None:
    """启动 app 进程内账本成本投影 daemon 线程（幂等，先刷后等）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    def _loop():
        while True:
            try:
                refresh_once()
            except Exception:  # noqa: BLE001 — 投影软失败，账本与业务不受影响
                logger.warning("[CostGauge] 账本成本投影刷新失败（软失败）", exc_info=True)
            time.sleep(interval_s)

    _thread = threading.Thread(target=_loop, name="cost-gauge-refresher", daemon=True)
    _thread.start()
    logger.info("[CostGauge] 账本成本投影 refresher started (interval=%ss)", interval_s)
