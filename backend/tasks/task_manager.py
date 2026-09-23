"""tasks/task_manager.py — 任务管理门面（API 层 ↔ Celery ↔ Redis 控制面）。

职责：
- 入队/恢复/取消/暂停 的编排动作（不直接执行图）
- Redis 控制标志：cancel / pause（节点边界生效）
- Redis pub/sub 事件广播：task_executor 发布 → SSE 路由订阅

不 import LangGraph；不 import Skill 层。
"""
from __future__ import annotations

import json

from backend.config.tasks import (
    TASK_EVENT_CHANNEL,
    TASK_FLAG_TTL,
    TASK_KEY_PREFIX,
    TASK_ZOMBIE_THRESHOLD_SECONDS,
)
from backend.models.task import TaskRecord, TaskStatus
from backend.shared.logger import logger

_CANCEL_FLAG = TASK_KEY_PREFIX + "cancel:"
_PAUSE_FLAG = TASK_KEY_PREFIX + "pause:"


def _redis():
    """复用全局 Redis 单例；不可用返回 None（调用方优雅降级）。"""
    from backend.infra.redis.client import get_redis

    return get_redis()


# ═══════════════════════════════════════════════════
# 控制标志
# ═══════════════════════════════════════════════════

def request_cancel(task_id: str) -> bool:
    """置取消标志（Worker 在节点边界轮询生效）。返回是否落标志成功。"""
    r = _redis()
    if r is None:
        logger.error("[TaskManager] Redis 不可用，取消标志无法下发: %s", task_id)
        return False
    r.set(_CANCEL_FLAG + task_id, "1", ex=TASK_FLAG_TTL)
    # 仍在队列未开跑的任务：直接 revoke（不等 Worker 拾取）
    _revoke_queued(task_id)
    return True


def request_pause(task_id: str) -> bool:
    """置暂停标志：Worker 在下一个节点边界停止 → WAITING_USER/PAUSED。"""
    r = _redis()
    if r is None:
        return False
    r.set(_PAUSE_FLAG + task_id, "1", ex=TASK_FLAG_TTL)
    return True


def pause_task(task_id: str) -> dict:
    """暂停任务（Phase1 Step4 编排入口，幂等）。

    语义：不强杀当前原子节点——RUNNING 中请求暂停时，Worker 在当前节点
    完成并落库后、下一节点开始前发现标志，停止调度后续节点（C 不得开始），
    checkpoint 保留指向最后成功节点。

    - RUNNING：置 Redis 暂停标志（节点边界生效）
    - PENDING（未拾取）：直接原子落库 PAUSED（mark_paused_if_pending），
      不依赖 Worker；竞态失败（恰好被拾取）回落标志路径
    - PAUSED：幂等返回，不重复置标志
    - 终态：拒绝（ValueError）
    返回 {"ok": bool, "already": 是否原本已暂停, "mode": 标志/落库, ...}。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status.is_terminal():
        raise ValueError(f"task already terminal: {record.status.value}")
    if record.status == TaskStatus.PAUSED:
        return {"ok": True, "already": True, "status": "PAUSED"}

    if record.status == TaskStatus.PENDING:
        if task_service.mark_paused_if_pending(task_id):
            publish_event(task_id, "paused", node="queued",
                          message="任务在队列中暂停")
            return {"ok": True, "already": False, "status": "PAUSED",
                    "mode": "queued"}
        # 竞态：API 读到 PENDING 后 Worker 恰好拾取（租约已置 RUNNING）
        # → 回落标志路径，Worker 在第一个节点边界停下
        record = task_service.get_task(task_id)
        if record is not None and record.status == TaskStatus.RUNNING:
            ok = request_pause(task_id)
            return {"ok": ok, "already": False, "status": "RUNNING",
                    "mode": "flag", "flag": ok}
        raise ValueError("task status changed during pause, retry")

    ok = request_pause(task_id)
    if not ok:
        logger.error("[TaskManager] Redis 不可用，暂停标志无法下发: %s", task_id)
        return {"ok": False, "already": False, "status": record.status.value,
                "mode": "flag", "flag": False}
    return {"ok": True, "already": False, "status": record.status.value,
            "mode": "flag", "flag": True}


def clear_flags(task_id: str) -> None:
    """恢复/重启前清除全部控制标志。"""
    r = _redis()
    if r is not None:
        r.delete(_CANCEL_FLAG + task_id, _PAUSE_FLAG + task_id)


def is_cancel_requested(task_id: str) -> bool:
    r = _redis()
    return bool(r and r.exists(_CANCEL_FLAG + task_id))


def is_pause_requested(task_id: str) -> bool:
    r = _redis()
    return bool(r and r.exists(_PAUSE_FLAG + task_id))


def _revoke_queued(task_id: str) -> None:
    """对已入队未执行的 Celery 任务发 revoke（温和模式，执行中无效）。"""
    from backend.services import task_service

    try:
        record = task_service.get_task(task_id)
        if record and record.celery_task_id:
            from backend.tasks.celery_app import celery_app

            celery_app.control.revoke(record.celery_task_id, terminate=False)
    except Exception:
        # revoke 失败不致命：标志位兜底（Worker 拾取后仍会取消）
        logger.debug("[TaskManager] revoke failed (flag 兜底): %s", task_id, exc_info=True)


# ═══════════════════════════════════════════════════
# 入队 / 恢复
# ═══════════════════════════════════════════════════

def _record_enqueued(workflow: str, dispatch_type: str) -> None:
    """投递观测（Phase2-F）：低基数 workflow/dispatch_type，best-effort。

    放在 enqueue_task/_redispatch_index 两个投递原语（API 直投与
    dispatch_task 派发共同经过的唯一位置），不重复计数。
    """
    try:
        from backend.observability.metrics import task_enqueued_total

        task_enqueued_total.labels(
            workflow=workflow, dispatch_type=dispatch_type).inc()
    except Exception:  # noqa: BLE001 — 观测失败不影响投递
        logger.debug("[TaskManager] 投递观测失败", exc_info=True)


def enqueue_task(record: TaskRecord, *, dispatch_type: str = "initial") -> str | None:
    """interactive_agent 侧投递（queue 由 QueueRouter 决定，Step3 收口）。

    返回 celery async result id（不可用时 None）。队列名一律取
    QueueRouter.resolve_for_task(record).physical_queue——本函数与所有
    调用方（initial/resume/recovery）均不得自拼 queue 字符串。
    """
    from backend.services import task_service
    from backend.tasks.queue_router import log_route, resolve_for_task

    clear_flags(record.id)
    route = resolve_for_task(record)  # 未知 workflow 在此 fail-closed（不被下方 broker 兜底吞掉）
    _record_enqueued(route.workflow, dispatch_type)
    try:
        from backend.tasks.agent_tasks import execute_agent_task

        async_result = execute_agent_task.apply_async(
            args=[record.id], queue=route.physical_queue)
        task_service.mark_queued(record.id, async_result.id,
                                 queue=route.physical_queue)
        log_route(route, dispatch_type=dispatch_type, task_id=record.id,
                  celery_task_name="tasks.execute_agent",
                  previous_queue=record.queue)
        return async_result.id
    except Exception as e:
        # broker 不可达：任务留在 PENDING，由 API 返回 503 提示
        logger.error("[TaskManager] enqueue failed: %s (%s)", record.id, e)
        raise


def _redispatch_index(task_id: str, dispatch_type: str) -> str | None:
    """rag_index 侧投递（kwargs 重建 + queue 由 QueueRouter 决定）。

    缺 index_kwargs（无法重建消息）时抛错：resume 路径回滚 PAUSED、
    recovery 路径回滚认领，任务保留可再次处理。
    """
    from backend.tasks.index_task_runtime import redispatch_index_task

    _record_enqueued("rag_index", dispatch_type)
    result = redispatch_index_task(task_id, dispatch_type=dispatch_type)
    if result is None:
        raise RuntimeError(f"rag_index 任务 {task_id} 缺 index_kwargs，无法重投")
    return result


# workflow → 投递器注册表：唯一一处"哪个 workflow 用哪个 Celery task +
# 怎么重建 payload"的登记（执行语义）；queue 决策全部在 QueueRouter。
# lambda 体引用模块 globals（enqueue_task），monkeypatch 该名字仍生效。
_WORKFLOW_DISPATCHERS = {
    "main": lambda record, dt: enqueue_task(record, dispatch_type=dt),
    "rag_index": lambda record, dt: _redispatch_index(record.id, dt),
}


def dispatch_task(record: TaskRecord, *, dispatch_type: str = "initial") -> str | None:
    """workflow 统一派发入口（resume/recovery 共用；Step3 队列决策收口）。

    未知 workflow → QueueRoutingError（fail-closed，不偷偷投 agent）。
    """
    from backend.tasks.queue_router import QueueRoutingError, resolve_for_task

    route = resolve_for_task(record)
    dispatcher = _WORKFLOW_DISPATCHERS.get(route.workflow)
    if dispatcher is None:
        raise QueueRoutingError(
            f"workflow {route.workflow!r} 未登记投递器（fail-closed）")
    return dispatcher(record, dispatch_type)


def cancel_task(task_id: str) -> dict:
    """取消任务（Phase1 Step6 编排入口，幂等）。

    语义：不强杀当前原子节点——RUNNING 中取消时 Worker 在当前节点完成后
    的边界停下，后续节点不得开始；checkpoint 与已完成结果保留；
    CANCELLED 为终态，resume 被明确拒绝。

    - PENDING / PAUSED / WAITING_USER：原子落库 CANCELLED（不经 Worker），
      并对队列内消息发 revoke；竞态（恰好被拾取）回落标志路径
    - RUNNING：置 Redis 取消标志（节点边界生效），Worker 捕获后落 CANCELLED
    - 终态（含重复 cancel）：幂等返回 already

    返回 {"ok", "already", "status", "mode": db|flag}。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status.is_terminal():
        return {"ok": True, "already": True, "status": record.status.value}

    # 队列内消息直接失效（已在执行则 revoke 无效，幂等无害）
    _revoke_queued(task_id)

    if record.status in (TaskStatus.PENDING, TaskStatus.PAUSED,
                         TaskStatus.WAITING_USER):
        if task_service.mark_cancelled_if_status(task_id, record.status):
            clear_flags(task_id)
            publish_event(task_id, "cancelled",
                          node=record.current_node or "queued",
                          message="任务已取消")
            return {"ok": True, "already": False, "status": "CANCELLED",
                    "mode": "db"}
        # 竞态：读状态后恰好被拾取/恢复 → 回落标志路径
        record = task_service.get_task(task_id)
        if record is None:
            raise LookupError(f"task not found: {task_id}")
        if record.status.is_terminal():
            return {"ok": True, "already": True,
                    "status": record.status.value}

    ok = request_cancel(task_id)
    if not ok:
        logger.error("[TaskManager] Redis 不可用，取消标志无法下发: %s", task_id)
        return {"ok": False, "already": False,
                "status": record.status.value, "mode": "flag"}
    return {"ok": True, "already": False, "status": record.status.value,
            "mode": "flag"}


def resume_task(task_id: str, user_input: str = "",
                *, allow_failed: bool = False,
                dispatch_type: str = "resume") -> TaskRecord:
    """恢复任务（Phase1 Step5：原子认领，重复 resume 不产生第二个执行链）。

    1. 清控制标志 → 2. 若带用户输入写入 DB（Worker 续跑时注入 state）→
    3. 原子认领 expected→PENDING（条件 UPDATE，并发双 resume 恰一个胜出，
    败者幂等返回不重复入队）→ 4. 经 QueueRouter 按 workflow 派发。
    Worker 拾取后按租约 + checkpoint 判定续跑，不重头执行图；真正执行权
    由 try_acquire_lease 仲裁（max_concurrent_executor_per_task=1）。

    - 默认仅 PAUSED / WAITING_USER 可恢复（Phase1 规格口径；
      WAITING_USER 为"暂停等人"变体，既有产品能力，登记例外）
    - FAILED 重试走管理端通道（allow_failed=True），保持自愈式续跑语义
      （管理端传 dispatch_type="admin_retry" 入观测轨迹）
    - 队列亲和：rag_index resume 永远回 rag_index（QueueRouter binding，
      不再 if-else 判断 graph_name——Phase1 曾因此误投 agent 队列）
    - 入队失败回滚 PENDING→PAUSED（状态机合法），任务保留可再次 resume

    返回最新 TaskRecord（败者返回的是已被对手认领后的行，status=PENDING）。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    status = record.status
    if status in (TaskStatus.SUCCESS, TaskStatus.CANCELLED):
        raise ValueError(f"task is terminal ({status.value}), resume rejected")
    if status == TaskStatus.FAILED and not allow_failed:
        raise ValueError(
            "仅 PAUSED/WAITING_USER 可恢复；FAILED 重试请走管理端重试通道")
    if status in (TaskStatus.RUNNING, TaskStatus.PENDING):
        # 已在执行/排队：视为幂等命中（不重复入队）
        logger.info("[TaskManager] resume on %s task %s, skip re-enqueue",
                    status.value, task_id)
        return record

    clear_flags(task_id)
    if user_input:
        task_service.append_checkpoint(
            task_id, "__user_input__", {"user_input": user_input})
    if not task_service.claim_for_resume(task_id, status):
        # 并发对手已认领（status 已变）：幂等返回最新行，不重复入队
        logger.info("[TaskManager] resume lost race for %s (already claimed),"
                    " skip re-enqueue", task_id)
        return task_service.get_task(task_id)  # type: ignore[return-value]

    record = task_service.get_task(task_id)  # type: ignore[assignment]
    try:
        dispatch_task(record, dispatch_type=dispatch_type)  # type: ignore[arg-type]
    except Exception:
        # 入队失败回滚到 PAUSED（PENDING→PAUSED 白名单合法），可再次 resume
        task_service.mark_paused_if_pending(
            task_id, progress="入队失败已回滚，可再次恢复")
        publish_event(task_id, "paused", status="PAUSED",
                      message="恢复入队失败，已回滚暂停")
        raise
    publish_event(task_id, "resumed", status="PENDING",
                  progress="已恢复，等待调度（从 checkpoint 续跑）")
    return task_service.get_task(task_id)  # type: ignore[return-value]


# ═══════════════════════════════════════════════════
# Stale Recovery Sweeper（Phase2 Step1：Worker crash 自动恢复主路径）
# ═══════════════════════════════════════════════════

def sweep_stale_executions() -> dict:
    """beat 周期任务：租约过期的 RUNNING 任务 → 原子认领 → 按原 queue 自动重投。

    恢复链（不等 broker visibility_timeout）：RUNNING + lease_expires_at
    过期 → claim_stale_for_recovery（原子，recovery_count 计数，同一 stale
    execution 只能被认领一次）→ 按 graph_name 回投原队列 → 新 Worker 走
    正常 lease acquire + checkpoint 续跑。

    - 保留原 task_id/thread_id，恢复必须经正常租约认领（不绕过）
    - 恢复次数超上限 → fail_recovered_task 终态 FAILED(ZOMBIE_RECONCILED)
    - 重投失败（broker 不可达）→ 回滚认领（PENDING→RUNNING，租约字段维持
      过期值），下一轮 sweep 重新处理
    """
    from backend.config.tasks import (
        CELERY_HARD_TASK_TIMEOUT,
        TASK_MAX_LEASE_RECOVERIES,
        TASK_RECOVERY_GRACE_SECONDS,
    )
    from backend.services import task_service

    legacy_threshold = CELERY_HARD_TASK_TIMEOUT + 60
    stale_ids = task_service.find_stale_executions(
        grace_seconds=TASK_RECOVERY_GRACE_SECONDS,
        legacy_threshold_seconds=legacy_threshold)

    def _record_recovery(result: str) -> None:
        # Phase2-F 恢复观测（best-effort）：recovered|exhausted|reverted
        try:
            from backend.observability.metrics import task_recovery_total

            task_recovery_total.labels(result=result).inc()
        except Exception:  # noqa: BLE001 — 观测失败不影响恢复
            logger.debug("[TaskManager] 恢复观测失败", exc_info=True)

    recovered: list[str] = []
    exhausted: list[str] = []
    for task_id in stale_ids:
        if not task_service.claim_stale_for_recovery(
                task_id,
                grace_seconds=TASK_RECOVERY_GRACE_SECONDS,
                legacy_threshold_seconds=legacy_threshold,
                max_recoveries=TASK_MAX_LEASE_RECOVERIES):
            # 认领失败 = 被并发 sweeper 抢先（幂等命中）或恢复次数已耗尽
            if task_service.fail_recovered_task(
                    task_id,
                    grace_seconds=TASK_RECOVERY_GRACE_SECONDS,
                    legacy_threshold_seconds=legacy_threshold,
                    message=(
                        f"自动恢复 {TASK_MAX_LEASE_RECOVERIES} 次后仍失败"
                        "（租约持续过期），终态收口；可从管理端重试")):
                _record_recovery("exhausted")
                exhausted.append(task_id)
                clear_flags(task_id)
                publish_event(task_id, "failed",
                              message="自动恢复次数耗尽，任务已收口（可重试）")
            continue

        record = task_service.get_task(task_id)
        try:
            if record is None:
                raise RuntimeError(f"task row missing: {task_id}")
            # Step3：队列决策收口——按 workflow 经 QueueRouter 重投原
            # workload 队列（rag_index 回 rag_index，agent 回 agent）；
            # tasks.queue 仅作 trace 对照（binding 变化时打 previous/
            # resolved，不作为路由依据）
            dispatch_task(record, dispatch_type="recovery")
        except Exception as e:
            # 重投失败：回滚认领，保持 stale 语义等下一轮（不丢任务）
            _record_recovery("reverted")
            task_service.revert_recovery_claim(task_id)
            logger.error("[TaskManager] recovery redispatch 失败，已回滚 %s: %s",
                         task_id, e)
            continue
        _record_recovery("recovered")
        recovered.append(task_id)
        publish_event(task_id, "recovering",
                      message="检测到 Worker 失联，已自动恢复重投（从 checkpoint 续跑）")
        logger.warning("[TaskManager] stale execution recovered: %s "
                       "(recovery_count=%s, previous_queue=%s)",
                       task_id, record.recovery_count, record.queue)
    if recovered or exhausted:
        logger.warning("[TaskManager] sweep: recovered=%s exhausted=%s "
                       "(scanned=%d)", recovered, exhausted, len(stale_ids))
    return {"ok": True, "scanned": len(stale_ids),
            "recovered": recovered, "exhausted": exhausted}


# ═══════════════════════════════════════════════════
# 僵尸任务收尸（2026-09-21 高并发审查 B5；Phase2 Step1 退化为最终兜底）
# ═══════════════════════════════════════════════════

def reconcile_zombie_tasks() -> dict:
    """beat 周期任务：把心跳停更超阈值的僵尸 RUNNING 任务收尸为 FAILED。

    Phase2 Step1 起职责收窄为**最终故障收口**：Worker crash 的主恢复路径
    是 sweep_stale_executions（租约过期 ~3min 内自动重投恢复，无需人工）。
    本任务只在 sweep 本身不可用（beat/队列故障）时兜底——可恢复任务
    已被 sweep 转走（不再 RUNNING），轮到这里的只有恢复链也失效的残留。

    场景：Worker 崩溃 + sweeper 不可用 → 无任何自动恢复路径，任务永久卡
    RUNNING。收尸为 FAILED 后即可在管理端重试（从 checkpoint 续跑）。

    原子性：收尸走 task_service.reap_zombie_running 的条件 UPDATE，
    与活 Worker 并发安全（心跳/续租持续刷新 updated_at 的活任务不会被
    误杀）；收尸后清控制标志 + 广播 SSE failed 事件。
    """
    from backend.services import task_service

    reaped = task_service.reap_zombie_running(
        threshold_seconds=TASK_ZOMBIE_THRESHOLD_SECONDS,
        status=TaskStatus.FAILED,
        error_message=(
            f"Worker 失联且自动恢复链路未生效：心跳停更超过 "
            f"{TASK_ZOMBIE_THRESHOLD_SECONDS}s，由 zombie reconcile 最终收尸"
            "（可重试）"),
        error_type="ZOMBIE_RECONCILED",
    )
    for task_id in reaped:
        clear_flags(task_id)
        publish_event(task_id, "failed",
                      message="Worker 心跳超时，任务被最终收尸（可重试）")
    if reaped:
        logger.warning("[TaskManager] zombie reconcile 最终收尸 %d 个任务: %s",
                       len(reaped), reaped)

    # Phase2 Step4：admission 对账顺带执行（同一 beat 周期，不新增调度器）。
    # 识别过期残留（物理清理）、token 在但任务已终态（释放）、计数漂移；
    # admission 关闭或 store 不可用时静默跳过（对账是观测性兜底，不阻塞收尸）。
    admission_report = None
    try:
        from backend.tasks import admission

        if admission.get_admission_controller().policy.enabled:
            admission_report = admission.reconcile_admission_state()
    except Exception:  # noqa: BLE001 — 对账失败不影响 zombie 收尸主流程
        logger.debug("[TaskManager] admission reconcile 失败", exc_info=True)
    return {"ok": True, "count": len(reaped),
            "threshold_seconds": TASK_ZOMBIE_THRESHOLD_SECONDS,
            "admission": admission_report}


def force_cancel_task(task_id: str) -> dict:
    """管理端强制撤销（B5：RUNNING 纳入强制取消范围）。

    1. 置取消标志 + 队列内 revoke（原有温和路径，活 Worker 节点边界生效）；
    2. 若任务为 RUNNING 且心跳已停更超阈值（僵尸），直接原子收尸为
       CANCELLED——不等一个永远不会来的 Worker。

    返回 {"flag": 标志是否下发成功, "forced": 是否强制收尸, "status": 最新状态}。
    任务不存在抛 LookupError；已终态抛 ValueError。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status.is_terminal():
        raise ValueError(f"task already terminal: {record.status.value}")

    flag_ok = request_cancel(task_id)

    forced = False
    if record.status == TaskStatus.RUNNING:
        reaped = task_service.reap_zombie_running(
            threshold_seconds=TASK_ZOMBIE_THRESHOLD_SECONDS,
            status=TaskStatus.CANCELLED,
            error_message="管理员强制撤销（RUNNING 心跳超时，直接收尸）",
            error_type="ADMIN_FORCE_CANCEL",
            task_id=task_id,
        )
        forced = bool(reaped)
        if forced:
            clear_flags(task_id)
            publish_event(task_id, "cancelled",
                          message="管理员强制撤销（僵尸任务收尸）")

    record = task_service.get_task(task_id)
    return {"flag": flag_ok, "forced": forced,
            "status": record.status.value if record else ""}


# ═══════════════════════════════════════════════════
# 事件广播（SSE 数据源）
# ═══════════════════════════════════════════════════

def publish_event(task_id: str, event: str, **payload) -> None:
    """发布任务事件到 Redis pub/sub（SSE 路由订阅同一通道）。

    事件形态对齐验收协议：
      {"event": "node_start", "node": "planner", ...}
    广播失败不影响执行（fire-and-forget，SSE 是 best-effort 推送）。
    """
    r = _redis()
    if r is None:
        return
    try:
        r.publish(TASK_EVENT_CHANNEL + task_id,
                  json.dumps({"event": event, **payload},
                             ensure_ascii=False, default=str))
    except Exception:
        logger.debug("[TaskManager] publish event failed: %s/%s",
                     task_id, event, exc_info=True)


def subscribe_events(task_id: str):
    """订阅任务事件通道（SSE 路由用；返回 pubsub 或 None）。"""
    r = _redis()
    if r is None:
        return None
    try:
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(TASK_EVENT_CHANNEL + task_id)
        return pubsub
    except Exception:
        logger.debug("[TaskManager] subscribe failed: %s", task_id, exc_info=True)
        return None
