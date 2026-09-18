"""trace_retention.py — trace 留存期限清理（合规确认结论落地）。

规划：docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md §7.2；
合规确认：docs/2026-09-19-元数据抽取数据送LLM合规确认申请.md §5.5
（2026-09-19 结论：默认 14 天，敏感数据 180 天）。

敏感判定按 trace 内 doc_type（financial/customer_data/legal，配置可调）：
这三类文档的抽取结果含实体、合同条款与财务内容。data JSON 解析失败的
行按**非敏感**处理走 14 天默认期——宁可早删不可多留（留存即风险）。

清理走批量循环（TRACE_RETENTION_BATCH_SIZE），任何异常不中断 loop，
由 startup 任务按周期调用（app/server.py::start_trace_retention_loop）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from backend.config.observability import (
    TRACE_RETENTION_BATCH_SIZE,
    TRACE_RETENTION_DAYS_DEFAULT,
    TRACE_RETENTION_DAYS_SENSITIVE,
    TRACE_RETENTION_SWEEP_FIRST_DELAY_MIN,
    TRACE_RETENTION_SWEEP_INTERVAL_HOURS,
    TRACE_SENSITIVE_DOC_TYPES,
)
from backend.shared.logger import logger

_CUTOFF_FMT = "%Y-%m-%d %H:%M:%S"


def _cutoff(days: int, now: datetime | None = None) -> str:
    """按本地时间文本（与 store 写入同构）计算截止时间。"""
    base = now or datetime.now()
    return (base - timedelta(days=days)).strftime(_CUTOFF_FMT)


def _extract_doc_type(data: dict) -> str:
    """从 trace dict 提取 doc_type（宽容提取；找不到返回空串）。"""
    dt = data.get("doc_type")
    if isinstance(dt, str) and dt:
        return dt.lower()
    meta = data.get("metadata")
    if isinstance(meta, dict):
        dt = meta.get("doc_type")
        if isinstance(dt, str) and dt:
            return dt.lower()
    return ""


def purge_expired_traces(store, now: datetime | None = None,
                         batch_size: int = TRACE_RETENTION_BATCH_SIZE) -> dict:
    """清理超期 trace，返回统计 {default_purged, sensitive_purged, errors}。

    两段式：
      1) 默认期：created_at < now-14d 且 doc_type 非敏感 → 删；
      2) 敏感期：created_at < now-180d → 无条件删（含 JSON 脏行）。
    """
    cutoff_default = _cutoff(TRACE_RETENTION_DAYS_DEFAULT, now)
    cutoff_sensitive = _cutoff(TRACE_RETENTION_DAYS_SENSITIVE, now)
    sensitive = {t.strip().lower() for t in TRACE_SENSITIVE_DOC_TYPES}
    stats = {"default_purged": 0, "sensitive_purged": 0, "errors": 0}

    # 1) 敏感期：超 180 天无条件删（先删，减少第二段扫描量）
    try:
        while True:
            rows = store.iter_before(cutoff_sensitive, limit=batch_size)
            if not rows:
                break
            stats["sensitive_purged"] += store.delete_by_ids(
                [r["trace_id"] for r in rows])
            if len(rows) < batch_size:
                break
    except Exception as e:
        stats["errors"] += 1
        logger.warning(f"[TraceRetention] 敏感期清理失败: {e}")

    # 2) 默认期：超 14 天且非敏感
    try:
        while True:
            rows = store.iter_before(cutoff_default, limit=batch_size)
            if not rows:
                break
            stale_ids = [r["trace_id"] for r in rows
                         if _extract_doc_type(r) not in sensitive]
            if stale_ids:
                stats["default_purged"] += store.delete_by_ids(stale_ids)
            # 本批全部命中敏感（或无 stale_id）时若不推进会死循环：
            # iter_before 按 created_at ASC 固定窗口，剩余行均 >= 敏感期，
            # 直接跳出交由下个周期处理（届时自然落入敏感期段）
            if len(rows) < batch_size:
                break
            if not stale_ids:
                break
    except Exception as e:
        stats["errors"] += 1
        logger.warning(f"[TraceRetention] 默认期清理失败: {e}")

    if stats["default_purged"] or stats["sensitive_purged"] or stats["errors"]:
        logger.info(f"[TraceRetention] 清理完成: {stats} "
                    f"(cutoff default={cutoff_default}, sensitive={cutoff_sensitive})")
    return stats


async def trace_retention_loop(store=None) -> None:
    """startup 后台任务：首次延迟避开启动期，之后按周期清理。"""
    import asyncio

    if store is None:
        from backend.observability.trace_store import get_trace_store
        store = get_trace_store()
    await asyncio.sleep(TRACE_RETENTION_SWEEP_FIRST_DELAY_MIN * 60)
    while True:
        try:
            await asyncio.to_thread(purge_expired_traces, store)
        except Exception as e:
            logger.warning(f"[TraceRetention] 清理周期异常（不影响服务）: {e}")
        await asyncio.sleep(TRACE_RETENTION_SWEEP_INTERVAL_HOURS * 3600)
