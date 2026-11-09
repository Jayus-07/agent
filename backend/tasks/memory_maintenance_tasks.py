"""tasks/memory_maintenance_tasks.py — Memory 衰减生命周期 beat 任务（STOP C 接线，D4 对齐）。

STOP A 审计发现 MemoryService.run_decay 全仓无调用方（衰减子系统从未运行，
死代码状态）；STOP C 按「基础设施已成熟则正式接线」决策接入 maintenance
beat 周期。核心逻辑在 MemoryDecayService（与 Celery 解耦，可测试、可手动触发），
本模块只做任务注册与日志/指标包装。

契约：
  - 每日一次（beat 单实例，无重复调度风险）；SQL 为幂等条件 UPDATE
  - explicit 豁免：用户显式记忆不衰减（origin != 'explicit'）
  - **D4 边界（2026-11-10 第 2 期 D-1 裁决）**：衰减只降排序权重，不归档
    不隐藏（原「importance<0.2 归档」条款废止）；记忆量有界由 dreaming
    整合（合并/矛盾失效）+ 用户删除承担
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
    """每日衰减：>180 天 ×0.9、>90 天 ×0.95（仅降权；D4 边界——不归档不隐藏，
    第 2 期 D-1 裁决移除 archive_stale，记忆量有界由 dreaming 整合承担）。"""
    import asyncio

    from backend.memory.service import MemoryService

    from backend.infra.async_utils import run_on_worker_loop

    result = run_on_worker_loop(MemoryService().run_decay())
    logger.info("[MemoryDecayTask] decayed=%s", result.get("decayed", 0))
    try:
        from backend.observability.metrics import memory_decay_records_total
        if result.get("decayed"):
            memory_decay_records_total.labels(action="decayed").inc(result["decayed"])
    except Exception:  # 观测面异常不反噬任务
        pass
    return {"decayed": result.get("decayed", 0)}


@celery_app.task(
    name="memory.dreaming",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": CELERY_MAX_RETRIES,
                  "countdown": CELERY_RETRY_BACKOFF,
                  "max": CELERY_RETRY_BACKOFF_MAX},
)
def memory_dreaming() -> dict:
    """每日 dreaming 整合（ADR-0012 D4）：碎片合并/矛盾失效，动作走版本链。

    开关 MEMORY_DREAMING_ENABLED 默认关：关闭时返回 skipped 正常终态
    （不算失败不重试，与 eval 采样同保守模式）。核心逻辑在
    MemoryDreamingService（与 Celery 解耦，可测试、可手动触发）。
    """
    import asyncio

    from backend.infra.async_utils import run_on_worker_loop
    from backend.memory.dreaming import MemoryDreamingService

    result = run_on_worker_loop(MemoryDreamingService().run())
    logger.info("[MemoryDreamingTask] %s", result)
    return result
