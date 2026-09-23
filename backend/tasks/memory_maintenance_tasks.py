"""tasks/memory_maintenance_tasks.py — Memory 衰减生命周期 beat 任务（STOP C）。

STOP A 审计发现 MemoryService.run_decay 全仓无调用方（衰减子系统从未运行，
死代码状态）；STOP C 按「基础设施已成熟则正式接线」决策接入 maintenance
beat 周期。核心逻辑在 MemoryDecayService（与 Celery 解耦，可测试、可手动触发），
本模块只做任务注册与日志/指标包装。

契约：
  - 每日一次（beat 单实例，无重复调度风险）；SQL 为幂等条件 UPDATE
  - explicit 豁免：用户显式记忆不衰减、不自动归档（origin != 'explicit'）
  - 只操作 is_active 行，不触碰 superseded 历史
  - 失败独立重试（≤3 次），不影响主聊天链
  - 已知限制：主 L3 注入路径暂不 mark_accessed（STOP D ownership），
    recency 基准偏保守 → decay 参数从宽（180/90 天双档），不做激进收缩
"""
from __future__ import annotations

from backend.config.tasks import (
    CELERY_MAX_RETRIES,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(
    name="memory.daily_decay",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": CELERY_MAX_RETRIES,
                  "countdown": CELERY_RETRY_BACKOFF,
                  "max": CELERY_RETRY_BACKOFF_MAX},
)
def memory_daily_decay() -> dict:
    """每日衰减 + 归档：>180 天 ×0.9、>90 天 ×0.95、importance<0.2 归档。"""
    import asyncio

    from backend.memory.service import MemoryService

    result = asyncio.run(MemoryService().run_decay())
    logger.info("[MemoryDecayTask] decayed=%s archived=%s",
                result.get("decayed", 0), result.get("archived", 0))
    try:
        from backend.observability.metrics import memory_decay_records_total
        if result.get("decayed"):
            memory_decay_records_total.labels(action="decayed").inc(result["decayed"])
        if result.get("archived"):
            memory_decay_records_total.labels(action="archived").inc(result["archived"])
    except Exception:  # 观测面异常不反噬任务
        pass
    return {"decayed": result.get("decayed", 0), "archived": result.get("archived", 0)}
