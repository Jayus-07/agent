"""tasks/agent_tasks.py — Celery 任务定义（Agent 执行入口）。

状态机责任划分：
- Celery：调度（排队/超时杀），不感知 Agent 内部状态
- agent_memory.tasks：状态权威（DB 行）
- LangGraph PostgresSaver：Agent 执行状态（checkpoint 恢复）

三套机制边界（Phase2）：
- Retry（本模块）：进程活着 + 分类为可重试错误 → 显式 self.retry（退避+jitter），
  budget = tasks.max_retries 行级快照；耗尽 → FAILED(retry_exhausted)
- Recovery（Step1）：Worker/进程死亡 → 心跳停 → lease 过期 → stale sweeper
  自动重投 → checkpoint 续跑；不经过本模块的 retry 决策
- Failure：不可重试错误（validation/auth/quota/permission...）→ 立即 FAILED

Phase2 Step1（自动恢复）：
- 显式状态短路：SUCCESS/CANCELLED/PAUSED/WAITING_USER 收到任何
  redelivery/redispatch/retry 一律 NO-OP，状态不被消息唤醒
- 执行租约心跳 + 执行期 fencing：本模块全部状态写带 execution_id

Phase2 Step2（错误分类）：
- 移除 autoretry_for=(Exception,) 无差别重试；异常先经
  tasks.error_taxonomy.classify_task_error 分类，再按 RetryPolicy 决策
- retry 入队前复查任务状态（CANCELLED/PAUSED/SUCCESS... 禁止 retry）
- TaskState/log/SSE 错误口径统一为分类词表（不再落异常类名）
"""
from __future__ import annotations

import socket
import time
from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded

from backend.models.task import TaskLeaseLost, TaskStatus
from backend.shared.logger import logger
# 顶层导入：壳层 except 子句在异常匹配期解析该名字，函数内局部导入不够
from backend.tasks.retry_policy import TaskRetryScheduled


def _fail(task_id: str, message: str, status: TaskStatus = TaskStatus.FAILED,
          progress: str = "", execution_id: str | None = None, *,
          error_type: str = "", retryable: bool | None = None,
          retry_count: int | None = None,
          retry_exhausted: bool | None = None,
          retry_delay: int | None = None) -> None:
    """终态/等待重试落库 + SSE 广播；execution_id 传入时为 fencing 写。

    租约已丢失（TaskLeaseLost）时静默退出：状态权威已归属新 owner，
    本 Worker 对任务不再有任何写权（含事件广播）。
    """
    from backend.services import task_service

    try:
        task_service.update_status(
            task_id, status, error_message=message[:2000], progress=progress,
            execution_id=execution_id,
            error_type=error_type or None,
            retry_exhausted=retry_exhausted)
    except TaskLeaseLost:
        logger.warning("[AgentTask] %s fencing 拒绝终态写（租约已被接管），"
                       "放弃落库: %s", task_id, message)
        return
    from backend.tasks.task_manager import publish_event

    publish_event(task_id,
                  "cancelled" if status == TaskStatus.CANCELLED
                  else ("paused" if status == TaskStatus.PAUSED else "failed"),
                  message=message,
                  error_type=error_type or None,
                  retryable=retryable,
                  retry_count=retry_count,
                  retry_delay=retry_delay)


def _budget_decision(record, retries: int, error_type: str, retryable: bool):
    """分类 + budget → (是否本轮重试, 延迟秒数)。

    budget 权威 = tasks.max_retries 行级快照；延迟来自集中 RetryPolicy。
    """
    from backend.tasks.retry_policy import compute_delay, get_policy

    if not retryable:
        return False, 0
    retries_left = int(record.max_retries) - int(retries)
    if retries_left <= 0:
        return False, 0
    return True, compute_delay(get_policy(record.workflow), retries)


def _finalize_failure(task_id: str, exc: BaseException, *, record,
                      retries: int, execution_id: str | None,
                      timeout_limit_ms: int | None = None) -> bool:
    """统一失败出口：分类 → budget → 等待重试落库 或 终态 FAILED。

    返回 True = 已调度重试（调用方须抛 TaskRetryScheduled 由壳执行
    self.retry）；返回 False = 终态已落库（retry_exhausted / 不可重试），
    调用方应上抛原始异常交 Celery FAILURE + signals 补 traceback。
    """
    from backend.tasks.error_taxonomy import classify_task_error

    decision = classify_task_error(exc)
    retry_now, delay = _budget_decision(record, retries,
                                        decision.error_type, decision.retryable)
    if retry_now:
        _fail(task_id,
              f"可重试错误（{decision.error_type}）: {exc}",
              TaskStatus.FAILED,
              progress=f"等待第 {retries + 1}/{record.max_retries} 次重试"
                       f"（{delay}s 后，从 checkpoint 续跑）",
              execution_id=execution_id,
              error_type=decision.error_type, retryable=True,
              retry_count=retries, retry_delay=delay)
        return True

    exhausted = decision.retryable  # 分类可重试但 budget 耗尽
    _fail(task_id,
          f"{'重试耗尽' if exhausted else '不可重试错误'}"
          f"（{decision.error_type}）: {exc}",
          TaskStatus.FAILED,
          progress=("重试耗尽" if exhausted else
                    f"不可重试（{decision.error_type}），已终态"),
          execution_id=execution_id,
          error_type=decision.error_type, retryable=False,
          retry_count=retries, retry_exhausted=exhausted,
          retry_delay=None)
    logger.error(
        "[AgentTask] %s final failure: error_type=%s retryable=%s "
        "retry_exhausted=%s retry_count=%s/%s timeout_limit_ms=%s",
        task_id, decision.error_type, decision.retryable, exhausted,
        retries, record.max_retries, timeout_limit_ms)
    return False


def _defer_admission(task_id: str, record, lease_id: str,
                     decision_reason: str) -> None:
    """admission 满载/fail-closed 的 defer 出口（Phase2 Step4）。

    - budget 未超：释放租约回 PENDING（RUNNING→PENDING 例外通道，任务
      尚未执行任何节点，队列语义与 initial PENDING 等价）+ 消息 countdown
      重投（bounded 指数退避 + jitter；不消耗业务 retry budget）
    - budget 耗尽：fencing 落 FAILED(admission_rejected) 终态（管理端可
      重试），不再重投——避免满载期间无限轮询
    - 重投失败（broker 故障）：异常上抛 → 消息 unacked → broker 重投，
      任务保持 PENDING 无 token（无泄漏）
    """
    from backend.services import task_service
    from backend.tasks import admission
    from backend.tasks.queue_router import resolve_for_task

    if admission.defer_budget_exhausted(task_id):
        try:
            task_service.update_status(
                task_id, TaskStatus.FAILED,
                error_message=(
                    f"系统繁忙：延迟准入重试超限（admission {decision_reason}），"
                    "任务终态收口；可从管理端重试"),
                progress="admission 满载重投超限（可重试）",
                execution_id=lease_id,
                error_type="admission_rejected")
        except TaskLeaseLost:
            logger.warning("[AgentTask] %s defer budget 耗尽终态写被 fencing "
                           "拒绝（租约已易主），放弃", task_id)
            return
        from backend.tasks.task_manager import publish_event

        publish_event(task_id, "failed",
                      message="系统繁忙：延迟准入重试超限（可重试）",
                      error_type="admission_rejected")
        logger.warning(
            "[AgentTask] %s admission defer budget 耗尽，落 FAILED "
            "(admission_rejected)", task_id)
        return

    delay = admission.note_deferred(task_id, workflow=record.workflow)
    # Phase3 STOP B：defer countdown 窗口写入 durable not_before 证据——
    # PENDING recovery sweeper 在该时刻前不得把本行当 orphan 提前重投
    task_service.release_lease_for_defer(task_id, lease_id,
                                         not_before_seconds=delay)
    route = resolve_for_task(record)
    logger.warning(
        "[AgentTask] %s admission deferred (reason=%s)，%.1fs 后重投 %s",
        task_id, decision_reason, delay, route.physical_queue)
    execute_agent_task.apply_async(args=[task_id],
                                   queue=route.physical_queue,
                                   countdown=delay)


def _record_queue_wait(record) -> None:
    """排队等待观测（Phase2-F）：queued_at → 租约认领的时长，best-effort。

    只在租约认领成功（=拾取时刻）后调用；负值（时钟偏移）与超过 1 天的
    陈旧行不计入分布，避免污染直方图。
    """
    try:
        from backend.observability.metrics import task_queue_wait_seconds

        if record.queued_at is None:
            return
        queued = record.queued_at
        if queued.tzinfo is None:
            queued = queued.replace(tzinfo=timezone.utc)
        wait = (datetime.now(timezone.utc) - queued).total_seconds()
        if 0 <= wait < 86400:
            task_queue_wait_seconds.labels(
                workflow=record.workflow).observe(wait)
    except Exception:  # noqa: BLE001 — 观测失败不影响任务
        logger.debug("[AgentTask] 队列等待观测失败", exc_info=True)


def _bind_task_identity(record):
    """任务体绑定租户/操作者上下文（Phase2 Step6）。

    side-effect 类 Tool（email/export/data_collection/competitor）的全局
    幂等以 (tenant, actor) 为隔离身份：此前任务运行时不绑定身份，工具在
    Celery 上下文里一律走无租户兼容直调路径——工作流邮件等真实副作用
    只受进程内指纹保护，跨 Worker 的 retry/recovery 重入即重复发送。
    绑定后任务内工具与 HTTP 请求同一语义（run_idempotent_operation 全局
    幂等 + 副作用预算门禁）。空值也必须显式绑定：prefork 子进程跨任务
    复用，不清空会把上一个任务的租户泄漏给下一个任务。

    返回恢复函数，finally 必须调用。
    """
    from backend.tools.session import _current_tenant_id, _current_user_id

    tenant_token = _current_tenant_id.set(
        str(getattr(record, "tenant_id", "") or ""))
    user_token = _current_user_id.set(
        str(getattr(record, "user_id", "") or ""))

    def _restore() -> None:
        _current_tenant_id.reset(tenant_token)
        _current_user_id.reset(user_token)

    return _restore


def execute_agent_task_impl(task_id: str, *,
                            retries: int = 0, hostname: str = "") -> dict:
    """任务执行主体（Celery task 与 eager 测试共用的纯函数）。"""
    from backend.orchestration.checkpoint import TaskGraphExecutor
    from backend.services import task_service
    from backend.tasks.execution_context import clear_execution, set_execution
    from backend.tasks.lease_heartbeat import LeaseHeartbeat

    record = task_service.get_task(task_id)
    if record is None:
        # 任务行不存在：无法恢复，直接失败（不重试——查不到永远是查不到）
        raise LookupError(f"task record not found: {task_id}")

    # ── 显式状态短路（Phase2 Step1）：任何 redelivery / recovery 重投 /
    #    retry 到达时，非执行态一律 NO-OP，状态不被消息唤醒 ──
    if record.status == TaskStatus.CANCELLED:
        logger.info("[AgentTask] %s already cancelled, skip", task_id)
        return {"status": "CANCELLED"}
    if record.status == TaskStatus.SUCCESS:
        logger.info("[AgentTask] %s already succeeded, no-op", task_id)
        return {"status": "SUCCESS_NOOP"}
    if record.status in (TaskStatus.PAUSED, TaskStatus.WAITING_USER):
        # 暂停/等人任务不被 redelivery/retry 自动唤醒：只有显式 resume API 才恢复
        logger.info("[AgentTask] %s in %s, keep paused (no-op)",
                    task_id, record.status.value)
        return {"status": record.status.value, "skipped": True}
    if record.graph_name == "rag_index":
        # 执行器归属守卫（实机演练 2026-09-23）：rag_index 行的执行权在
        # execute_index（重投也走 rag_index 队列），agent 图绝不碰它——
        # 否则空 graph 输入直接 EmptyInputError。
        logger.warning("[AgentTask] %s is a rag_index task, skip (graph 归属守卫)",
                       task_id)
        return {"status": "SKIPPED_GRAPH_MISMATCH"}

    # 状态机禁止 FAILED→RUNNING 直跳（Phase1）：重试/recovery 重投路径，
    # 先显式回 PENDING（requeue 标记，可审计）再抢租约。
    # 幂等：admin retry 已回 PENDING 时本跳转为自转换，直接通过。
    if record.status == TaskStatus.FAILED:
        task_service.update_status(
            task_id, TaskStatus.PENDING,
            progress="重试回队（从 checkpoint 续跑）")
        record = task_service.get_task(task_id)

    # 执行前先原子抢租约（返回 execution_id，owner=worker+租约实例）：抢不到
    # 说明已有 Worker 在跑同一 thread_id —— 直接退出，不得进入执行分支
    # （LLM 重复烧钱、step_results 互踩）。
    from backend.config.tasks import TASK_LEASE_TTL_SECONDS

    lease_id = task_service.try_acquire_lease(
        task_id, worker=hostname or None, lease_ttl_seconds=TASK_LEASE_TTL_SECONDS)
    if not lease_id:
        logger.warning(
            "[AgentTask] %s lease held by another worker (acks_late 重投?), skip",
            task_id)
        return {"status": "RUNNING_ELSEWHERE"}
    logger.info("[AgentTask] %s lease acquired execution_id=%s worker=%s",
                task_id, lease_id[:8], hostname or "unknown")

    # 排队等待观测（Phase2-F）：租约认领成功 = 拾取时刻；queued_at 缺失
    # （重投/恢复链路未刷新）时跳过，不臆造 0。
    _record_queue_wait(record)

    # ── Admission Control（Phase2 Step4）：lease 认领成功后申请容量槽位。
    #    拒绝（容量满 / fail-closed）→ defer 出口：不执行任何节点。
    #    位置必须在 heartbeat/执行上下文之前——defer 早退路径不得残留
    #    ContextVar 与心跳线程（Phase2-F §32 泄漏修复，对齐 index 顺序）。
    from backend.tasks import admission

    decision = admission.acquire_for_execution(
        task_id, owner_execution_id=lease_id, record=record)
    if not decision.allowed:
        _defer_admission(task_id, record, lease_id, decision.reason)
        return {"status": "ADMISSION_DEFERRED"}

    t0 = time.monotonic()
    hb = LeaseHeartbeat(task_id, lease_id)
    hb.start()
    ctx_token = set_execution(task_id, lease_id)
    restore_identity = _bind_task_identity(record)
    exit_status = "FAILED"  # 防御缺省：未被显式标记的异常出口按 FAILED 计
    try:
        if retries:
            from backend.observability.metrics import task_retry_total

            task_retry_total.labels(workflow=record.workflow).inc()
            task_service.increment_retry(task_id)
            logger.warning("[AgentTask] retry #%d for %s (从 checkpoint 续跑)",
                           retries, task_id)

        executor = TaskGraphExecutor()
        output = executor.execute(record, execution_id=lease_id, heartbeat=hb)
        exit_status = str(output.get("status", TaskStatus.SUCCESS.value))
        return {"status": exit_status,
                "output": output}
    except SoftTimeLimitExceeded as exc:
        # SoftTimeLimit：runtime 层 timeout（进程仍活着）。分类器映射为
        # timeout（retryable）→ 与通用异常同一条 Retry/Failure 出口；
        # checkpoint 保留在最后成功节点，重试从 checkpoint 续跑。
        from backend.tasks.error_taxonomy import classify_task_error
        from backend.tasks.retry_policy import TaskRetryScheduled

        decision = classify_task_error(exc)
        retry_now, delay = _budget_decision(record, retries,
                                            decision.error_type,
                                            decision.retryable)
        if retry_now:
            exit_status = "RETRY_SCHEDULED"
            _fail(task_id, f"可重试错误（{decision.error_type}）: {exc}",
                  TaskStatus.FAILED,
                  progress=f"等待第 {retries + 1}/{record.max_retries} 次重试"
                           f"（{delay}s 后，从 checkpoint 续跑）",
                  execution_id=lease_id, error_type=decision.error_type,
                  retryable=True, retry_count=retries, retry_delay=delay)
            raise TaskRetryScheduled(
                exc, error_type=decision.error_type, retryable=True,
                delay=delay, retry_count=retries,
                max_retries=int(record.max_retries))
        # budget 耗尽（或分类不可重试）：终态 FAILED，checkpoint 保留
        exit_status = "FAILED"
        _finalize_failure(task_id, exc, record=record, retries=retries,
                          execution_id=lease_id,
                          timeout_limit_ms=None)
        raise
    except TaskLeaseLost:
        # 租约被接管：禁止写任何状态/事件，立即退出（恢复链由新 owner 继续）
        exit_status = "LEASE_LOST"
        logger.warning("[AgentTask] %s 租约被接管（execution=%s），本 executor "
                       "退出且不写状态", task_id, lease_id[:8])
        return {"status": "LEASE_LOST"}
    except Exception as e:
        # 区分三类终态异常（禁止裸 except-pass；每类都有明确落库语义）
        from backend.orchestration.checkpoint import TaskCancelled, TaskPaused

        if isinstance(e, TaskCancelled):
            exit_status = TaskStatus.CANCELLED.value
            _fail(task_id, "用户取消", TaskStatus.CANCELLED, "已取消",
                  execution_id=lease_id)
            return {"status": "CANCELLED"}
        if isinstance(e, TaskPaused):
            exit_status = TaskStatus.PAUSED.value
            _fail(task_id, "用户暂停", TaskStatus.PAUSED,
                  "已暂停，可 resume 恢复", execution_id=lease_id)
            return {"status": "PAUSED"}
        will_retry = _finalize_failure(task_id, e, record=record,
                                       retries=retries, execution_id=lease_id)
        if will_retry:
            # _finalize_failure 已按同口径落"等待重试"状态并算好延迟；
            # 这里抛 TaskRetryScheduled 由 Celery 壳复查状态后 self.retry
            from backend.tasks.error_taxonomy import classify_task_error
            from backend.tasks.retry_policy import TaskRetryScheduled

            decision = classify_task_error(e)
            _, delay = _budget_decision(record, retries,
                                        decision.error_type, decision.retryable)
            exit_status = "RETRY_SCHEDULED"
            raise TaskRetryScheduled(
                e, error_type=decision.error_type, retryable=True,
                delay=delay, retry_count=retries,
                max_retries=int(record.max_retries))
        exit_status = "FAILED"
        raise  # 终态已落库：上抛原始异常 → Celery FAILURE + signals 补 traceback
    finally:
        hb.stop()
        clear_execution(ctx_token)
        restore_identity()
        # Phase2-F 出口观测（best-effort）：出口状态 + 执行段耗时
        try:
            from backend.observability.metrics import (
                task_execution_duration_seconds,
                task_terminal_total,
            )

            task_terminal_total.labels(
                workflow=record.workflow, status=exit_status).inc()
            task_execution_duration_seconds.labels(
                workflow=record.workflow).observe(time.monotonic() - t0)
        except Exception:  # noqa: BLE001 — 观测失败不影响任务
            logger.debug("[AgentTask] 出口观测失败", exc_info=True)
        # 终态/retry/租约丢失统一出口释放容量槽位（owner CAS 幂等：
        # 租约已易主或 token 已过期时零副作用；defer 路径未持有 token，
        # 释放为 no-op）——retry countdown 期间不占 admission 容量
        try:
            admission.release_for_execution(task_id, lease_id,
                                            reason="execution_end")
        except Exception:  # noqa: BLE001 — 释放失败由 token TTL 自愈兜底
            logger.warning("[AgentTask] %s admission release 异常（TTL 自愈）",
                           task_id, exc_info=True)


def _retry_after_state_recheck(task_self, task_id: str,
                               scheduled) -> dict:
    """retry 入队前的最终状态复查（规格 §十一）。

    CANCELLED / PAUSED / SUCCESS / WAITING_USER / 非预期态 → 放弃重投
    （NO-OP）；仅当任务仍处于 impl 写下的 FAILED(等待重试) 才执行
    self.retry（新消息 countdown 入队，旧消息 ack）。

    Step3：重投队列显式经 QueueRouter 解析（同 workload 亲和 + 配置
    变化跟随新 binding），不依赖 task_routes 隐式路由。
    """
    from backend.services import task_service
    from backend.tasks.queue_router import log_route, resolve_for_task

    record = task_service.get_task(task_id)
    if record is None:
        logger.warning("[AgentTask] %s retry recheck: row missing, drop", task_id)
        return {"status": "MISSING", "skipped": True}
    if record.status != TaskStatus.FAILED:
        logger.warning(
            "[AgentTask] %s retry recheck: status=%s ≠ FAILED，放弃重投"
            "（不被 retry 唤醒）", task_id, record.status.value)
        return {"status": record.status.value, "skipped": True,
                "retry_cancelled": True}
    route = resolve_for_task(record)
    log_route(route, dispatch_type="retry", task_id=task_id,
              celery_task_name="tasks.execute_agent",
              previous_queue=record.queue)
    logger.warning(
        "[AgentTask] %s retry #%d/%d scheduled (%s, delay=%ss, queue=%s,"
        " 从 checkpoint 续跑)",
        task_id, scheduled.retry_count + 1, scheduled.max_retries,
        scheduled.error_type, scheduled.delay, route.physical_queue)
    raise task_self.retry(exc=scheduled.original, countdown=scheduled.delay,
                          queue=route.physical_queue)


# ═══════════════════════════════════════════════════
# Celery 任务注册
# ═══════════════════════════════════════════════════

def _register_task():
    """惰性注册：celery 未安装时允许模块被非 Worker 进程安全 import（eager 测试前置检查用）。"""
    from backend.config.tasks import CELERY_MAX_RETRIES
    from backend.tasks.celery_app import celery_app

    @celery_app.task(
        bind=True,
        name="tasks.execute_agent",
        acks_late=True,
        # Phase2 Step2：无差别 autoretry_for=(Exception,) 已移除——
        # 重试由 impl 分类决策后经 TaskRetryScheduled → self.retry 显式触发
        max_retries=CELERY_MAX_RETRIES,
    )
    def execute_agent_task(self, task_id: str) -> dict:
        try:
            # 超时/租约丢失/终态异常全部在 impl 内收口（fencing 感知），
            # 本壳只负责把 request 上下文传进去
            return execute_agent_task_impl(
                task_id, retries=self.request.retries,
                hostname=getattr(self.request, "hostname", "") or socket.gethostname())
        except TaskRetryScheduled as scheduled:
            # retry 入队前最终状态复查（CANCELLED/PAUSED/... 不被 retry 唤醒）
            return _retry_after_state_recheck(self, task_id, scheduled)

    return execute_agent_task


execute_agent_task = _register_task()
