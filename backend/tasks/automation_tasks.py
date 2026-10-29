"""tasks/automation_tasks.py — 用户自动任务执行链（决策 #27）。

职责边界（红线：不新建第二执行器）：
- automations.scan：beat 每分钟扫描 cron 到期（简化 5 字段匹配器 + 原子
  认领，见 services/automations.scan_due_cron_automations）→ 派 automations.run
- automations.run：事件消费入口——读 automation → 复用既有执行链
  （task_service.create_task + task_manager.enqueue_task → tasks.execute_agent
  → TaskGraphExecutor，workflow='main' 主图管线）。本任务只是"触发器"，
  Agent 执行、重试、恢复、Governance/确认门全部由既有链承担
- automations.cleanup_result_refs：每日清理 tasks.result_refs（#25 尾巴，
  backend/orchestration/result_refs.cleanup_expired）

调度定义在 backend/tasks/celery_app.py beat_schedule（beat 单实例纪律：
调度职责只在 agent-beat，本模块不注册任何自调度）；队列解析一律经
QueueRouter（backend/tasks/queue_router.py 登记后使用，禁止自拼队列）。

失败语义（failure-degradation 口径）：
- 触发失败（automation 缺失/停用/校验失败/派发失败）→ automation_events
  终态 failed + detail，可观测、不重试入死循环（重试由既有任务链负责）
- automations.run 本身不吞异常：DB 不可用等基础设施故障上抛交 Celery
  （acks_late 回队），仅业务性失败落事件终态
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="automations.scan")
def scan_user_automations() -> dict:
    """cron 到期扫描（beat 60s）：认领窗口 → 派发 automations.run。"""
    from backend.services import automations
    from backend.tasks.queue_router import beat_queue

    try:
        claimed = automations.scan_due_cron_automations()
    except Exception as e:  # noqa: BLE001 — 扫描失败必须可见但不打断 beat 节拍
        logger.error("[Automations] cron 扫描失败: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}
    dispatched = 0
    for item in claimed:
        try:
            from backend.tasks.automation_tasks import run_user_automation

            run_user_automation.apply_async(
                args=[item["automation_id"], item["event_id"]],
                queue=beat_queue("automations.run"),
            )
            dispatched += 1
        except Exception:  # noqa: BLE001 — broker 故障：事件留在 queued，下轮窗口自然重试
            logger.error("[Automations] %s 派发失败（broker 不可达?）",
                         item["automation_id"], exc_info=True)
            # 窗口已被认领：本窗口不再重复派发；事件停 queued 属可观测欠账
    if claimed:
        logger.info("[Automations] cron 扫描派发 %d/%d", dispatched, len(claimed))
    return {"ok": True, "claimed": len(claimed), "dispatched": dispatched}


@celery_app.task(name="automations.run")
def run_user_automation(automation_id: str, event_id: int) -> dict:
    """执行一次用户自动任务（复用既有执行链，见模块 docstring）。"""
    from backend.services import automations, task_service
    from backend.tasks.task_manager import enqueue_task

    automation = automations.get_automation(automation_id, internal=True)
    if automation is None:
        automations.mark_event(event_id, status="failed",
                               detail="automation 不存在（已被删除?）")
        return {"status": "failed", "reason": "missing"}

    if not automation.get("enabled"):
        automations.mark_event(event_id, status="failed",
                               detail="automation 已停用，跳过执行")
        _set_last_status(automation_id, "failed")
        return {"status": "failed", "reason": "disabled"}

    if automation.get("confirm_policy") != "auto":
        # 防御性兜底：CRUD 层已拒绝非 auto；若历史数据出现则不自动执行
        # （绝不静默把待确认任务当 auto 跑——写操作确认语义不可绕过）
        automations.mark_event(event_id, status="failed",
                               detail=f"confirm_policy={automation['confirm_policy']}"
                                      " 非 auto，v1 不自动执行")
        _set_last_status(automation_id, "failed")
        return {"status": "failed", "reason": "confirm_policy"}

    automations.mark_event(event_id, status="running")

    try:
        question = automations.validate_payload(automation.get("payload") or {})
    except automations.AutomationValidationError as e:
        automations.mark_event(event_id, status="failed",
                               detail=f"payload 不合法: {e}")
        _set_last_status(automation_id, "failed")
        return {"status": "failed", "reason": "payload"}

    # ── 复用既有执行链（红线）：create_task + enqueue_task ──
    # 写操作不因自动化绕过：tasks.execute_agent 管线内既有 Tool
    # Governance / 确认门照常生效。
    try:
        record = task_service.create_task(
            automation["user_id"], question, tenant_id=automation["tenant_id"])
        celery_id = enqueue_task(record)
    except Exception as e:  # noqa: BLE001 — 派发失败：事件终态化，不无限重试
        logger.error("[Automations] %s 派发 agent 任务失败: %s",
                     automation_id, e, exc_info=True)
        automations.mark_event(
            event_id, status="failed",
            detail=f"执行链派发失败: {e}"[:2000])
        _set_last_status(automation_id, "failed")
        return {"status": "failed", "reason": "dispatch"}

    automations.mark_event(
        event_id, status="succeeded",
        payload={"task_id": record.id, "celery_id": celery_id or ""},
        detail=f"task {record.id}")
    _set_last_status(automation_id, "succeeded", touch_last_run=True)
    return {"status": "succeeded", "task_id": record.id}


def _set_last_status(automation_id: str, status: str,
                     *, touch_last_run: bool = False) -> None:
    """回写最近状态（cron 窗口时刻已认领时不再覆盖 last_run_at）。"""
    from backend.services import automations

    try:
        automations.set_last_status(automation_id, status,
                                    touch_last_run=touch_last_run)
    except Exception:  # noqa: BLE001 — 观测性回写失败不影响执行结果
        logger.warning("[Automations] %s last_status 回写失败",
                       automation_id, exc_info=True)


@celery_app.task(name="automations.cleanup_result_refs")
def cleanup_task_result_refs() -> dict:
    """每日清理过期任务结果引用（#25 尾巴：result_refs 24h 失效）。

    合并顺序注：result_refs 模块随 #123 PR（feat/toolgov-result-refs）
    交付；惰性导入保证该 PR 未合并时本任务每日显式失败（不静默跳过、
    不影响 beat/其他任务），合并后自动恢复真实清理。
    """
    from backend.orchestration.result_refs import cleanup_expired

    result = cleanup_expired()
    logger.info("[Automations] result_refs 清理: %s", result)
    return result
