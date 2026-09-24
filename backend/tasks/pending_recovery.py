"""tasks/pending_recovery.py — PENDING Recovery Accelerator（Phase3 STOP B）。

职责边界（对齐 STOP A 审计 §4.3 与 STOP B 任务书）：
- 只做 **delivery recovery**：发现派发丢失的 stale PENDING → DB CAS 认领
  → 经既有 dispatch_task（QueueRouter 单一事实源）重投 → 正常 worker pickup。
- **不做 execution recovery**：不改状态为 RUNNING、不创建租约、不拿
  admission token、不碰业务 handler——执行完全由 Phase2 运行时接管
  （terminal 短路 → lease CAS → admission → fencing）。

安全性模型（duplicate delivery allowed, duplicate execution forbidden）：
- recovery 重投与"仍在途的原 broker 消息"并发是允许发生的：原消息后到时
  或被 RUNNING_ELSEWHERE 短路（lease CAS 0 行）、或被终态短路 NO-OP；
  不会产生第二个合法 execution。
- 认领是 PostgreSQL 条件 UPDATE（claim_stale_pending_for_recovery）：
  多 sweeper 并发只有一个 claim 成功；Redis 只做性能优化，不承担 correctness。
- publish 失败：行保持 PENDING + pending_recovery_last_at 已由 claim 写入
  （= 失败冷却），cooldown 过后自动再次 eligible——broker 恢复后自愈，
  不落 FAILED、不消耗 RetryPolicy budget。
- intentional delayed delivery（admission defer）：release_lease_for_defer
  写 dispatch_not_before_at durable 证据，not_before 未到期前永不重投。
- 计数超限：pending_recovery_count 达上限 → FAILED(DELIVERY_RECOVERY_
  EXHAUSTED) 可重试终态（管理端 retry / broker visibility 重投复活双兜底）。

不依赖 celery inspect / broker introspection 做 correctness（at-least-once
模型下 duplicate delivery 是常态，安全由 lease/fencing/幂等承担）。
"""
from __future__ import annotations

from backend.shared.logger import logger

# 结构化日志事件词表（STOP B §31；Prometheus 高基数禁令不适用——只进日志）
EVENT_CANDIDATE = "pending_recovery_candidate"
EVENT_CLAIMED = "pending_recovery_claimed"
EVENT_PUBLISHED = "pending_recovery_published"
EVENT_PUBLISH_FAILED = "pending_recovery_publish_failed"
EVENT_SKIPPED_NOT_BEFORE = "pending_recovery_skipped_not_before"
EVENT_EXHAUSTED = "pending_recovery_exhausted"


def recover_stale_pending_tasks(*, limit: int | None = None,
                                threshold_seconds: int | None = None,
                                cooldown_seconds: int | None = None,
                                max_recovery_count: int | None = None,
                                max_age_seconds: int | None = None) -> dict:
    """beat 壳入口：扫描 stale PENDING → CAS 认领 → dispatch_task 重投。

    全部参数默认取 config.tasks 配置；返回结构化结果供壳层日志/观测。
    单任务失败不阻断批次（逐任务 try/except，与 sweep_stale_executions 同型）。
    """
    from backend.config.tasks import (
        TASK_PENDING_RECOVERY_AFTER_SECONDS,
        TASK_PENDING_RECOVERY_BATCH_SIZE,
        TASK_PENDING_RECOVERY_COOLDOWN_SECONDS,
        TASK_PENDING_RECOVERY_MAX_AGE_SECONDS,
        TASK_PENDING_RECOVERY_MAX_COUNT,
    )
    from backend.services import task_service

    if not _enabled():
        return {"ok": True, "enabled": False, "claimed": 0, "published": 0,
                "publish_failed": 0, "exhausted": 0, "skipped": 0}

    limit = int(limit or TASK_PENDING_RECOVERY_BATCH_SIZE)
    threshold = int(threshold_seconds or TASK_PENDING_RECOVERY_AFTER_SECONDS)
    cooldown = int(cooldown_seconds or TASK_PENDING_RECOVERY_COOLDOWN_SECONDS)
    max_count = int(max_recovery_count or TASK_PENDING_RECOVERY_MAX_COUNT)
    max_age = int(max_age_seconds or TASK_PENDING_RECOVERY_MAX_AGE_SECONDS)

    # 候选查询带 count<max 过滤；超限行走独立收口查询（max_recovery_count=None）。
    # 候选批为空不能提前 return——超限收口仍需每轮检查（避免超限行因候选
    # 批长期为空而永远得不到终态收口）。
    candidate_ids = task_service.find_stale_pending(
        threshold_seconds=threshold, cooldown_seconds=cooldown,
        max_age_seconds=max_age, limit=limit,
        max_recovery_count=max_count)

    from backend.tasks.task_manager import dispatch_task

    claimed = published = publish_failed = skipped = exhausted = 0
    for task_id in candidate_ids:
        try:
            claim = task_service.claim_stale_pending_for_recovery(
                task_id, threshold_seconds=threshold,
                cooldown_seconds=cooldown, max_recovery_count=max_count,
                max_age_seconds=max_age)
            if claim is None:
                # 被并发 sweeper 抢先 / not_before 刚被 defer 写入 / 已终态
                skipped += 1
                continue
            claimed += 1
            logger.info(
                "event=%s task_id=%s workflow=%s previous_queue=%s "
                "previous_celery_task_id=%s pending_recovery_count=%s",
                EVENT_CLAIMED, task_id, claim["workflow"], claim["queue"],
                claim["celery_task_id"], claim["pending_recovery_count"])

            record = task_service.get_task(task_id)
            if record is None or record.status.value != "PENDING":
                # claim 后被并发改为非 PENDING（pause/cancel/拾取）：
                # 不重投——终态/暂停语义优先，消息层无需存在
                skipped += 1
                continue

            dispatch_task(record, dispatch_type="recovery")
            published += 1
            logger.info(
                "event=%s task_id=%s workflow=%s pending_recovery_count=%s",
                EVENT_PUBLISHED, task_id, record.workflow,
                claim["pending_recovery_count"])
            _record_recovery_metric("pending_recovered")
        except Exception as exc:  # noqa: BLE001 — 单任务失败不阻断批次
            # publish 失败（broker 仍不可达）：行保持 PENDING，claim 已写
            # pending_recovery_last_at = 失败冷却，cooldown 后自动再试。
            # 不 revert（行从未离开 PENDING）、不落 FAILED、不耗 retry budget。
            publish_failed += 1
            logger.warning(
                "event=%s task_id=%s error=%s —— 保持 PENDING，%ss 冷却后重试",
                EVENT_PUBLISH_FAILED, task_id, exc, cooldown)
            _record_recovery_metric("pending_publish_failed")

    # 超限收口：停滞且计数达上限的行不再无限重投（每轮 scan 检查一次）
    exhausted_ids = task_service.find_stale_pending(
        threshold_seconds=threshold, cooldown_seconds=cooldown,
        max_age_seconds=max_age,
        limit=limit, max_recovery_count=None)
    for task_id in exhausted_ids:
        record = task_service.get_task(task_id)
        if record is None or record.pending_recovery_count < max_count:
            continue
        if task_service.fail_stale_pending_delivery(
                task_id,
                message=(f"派发自动恢复 {max_count} 次后仍停滞，终态收口；"
                         "可从管理端重试（broker 重投到达时仍会自动复活）"),
                max_recovery_count=max_count, threshold_seconds=threshold,
                max_age_seconds=max_age):
            exhausted += 1
            logger.warning("event=%s task_id=%s pending_recovery_count>=%s",
                           EVENT_EXHAUSTED, task_id, max_count)
            _record_recovery_metric("pending_exhausted")

    return {"ok": True, "enabled": True, "candidates": len(candidate_ids),
            "claimed": claimed, "published": published,
            "publish_failed": publish_failed, "exhausted": exhausted,
            "skipped": skipped}


def _enabled() -> bool:
    from backend.config.tasks import TASK_PENDING_RECOVERY_ENABLED

    return bool(TASK_PENDING_RECOVERY_ENABLED)


def _record_recovery_metric(result: str) -> None:
    """复用 Phase2-F task_recovery_total（低基数 result 值扩展，best-effort）。"""
    try:
        from backend.observability.metrics import task_recovery_total

        task_recovery_total.labels(result=result).inc()
    except Exception:  # noqa: BLE001 — 观测失败不影响恢复
        logger.debug("[PendingRecovery] 恢复观测失败", exc_info=True)
