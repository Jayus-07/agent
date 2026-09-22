"""tasks/agent_tasks.py — Celery 任务定义（Agent 执行入口）。

状态机责任划分：
- Celery：调度（排队/重试/超时杀），不感知 Agent 内部状态
- agent_memory.tasks：状态权威（DB 行）
- LangGraph PostgresSaver：Agent 执行状态（checkpoint 恢复）

重试语义：自愈式续跑——重试时任务已处于 FAILED 状态且 thread_id 在库，
executor 从最近 checkpoint 继续，已完成的节点不重跑（LLM 调用零浪费）。
"""
from __future__ import annotations

import socket

from celery.exceptions import SoftTimeLimitExceeded

from backend.config.tasks import (
    CELERY_MAX_RETRIES,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
)
from backend.models.task import TaskStatus
from backend.shared.logger import logger

#: 这些异常是"业务终态"，重试无意义（用户主动行为 / 永久性失败）
_NO_RETRY_EXC = (KeyboardInterrupt, SystemExit)


def _fail(task_id: str, message: str, status: TaskStatus = TaskStatus.FAILED,
          progress: str = "") -> None:
    from backend.services import task_service

    task_service.update_status(task_id, status, error_message=message[:2000],
                               progress=progress)
    from backend.tasks.task_manager import publish_event

    publish_event(task_id,
                  "cancelled" if status == TaskStatus.CANCELLED
                  else ("paused" if status == TaskStatus.PAUSED else "failed"),
                  message=message)


def execute_agent_task_impl(task_id: str, *,
                            retries: int = 0, hostname: str = "") -> dict:
    """任务执行主体（Celery task 与 eager 测试共用的纯函数）。"""
    from backend.orchestration.checkpoint import TaskGraphExecutor
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        # 任务行不存在：无法恢复，直接失败（不重试——查不到永远是查不到）
        raise LookupError(f"task record not found: {task_id}")
    if record.status == TaskStatus.CANCELLED:
        logger.info("[AgentTask] %s already cancelled, skip", task_id)
        return {"status": "CANCELLED"}
    if record.graph_name == "rag_index":
        # 执行器归属守卫（实机演练 2026-09-23）：rag_index 行的执行权在
        # execute_index（重投也走 rag_index 队列），agent 图绝不碰它——
        # 否则空 graph 输入直接 EmptyInputError。
        logger.warning("[AgentTask] %s is a rag_index task, skip (graph 归属守卫)",
                       task_id)
        return {"status": "SKIPPED_GRAPH_MISMATCH"}

    # 状态机禁止 FAILED→RUNNING 直跳（Phase1）：Celery autoretry 重投 /
    # 收尸后重试路径，先显式回 PENDING（requeue 标记，可审计）再抢租约。
    # 幂等：admin retry 已回 PENDING 时本跳转为自转换，直接通过。
    if record.status == TaskStatus.FAILED:
        task_service.update_status(
            task_id, TaskStatus.PENDING,
            progress="重试回队（从 checkpoint 续跑）")
        record = task_service.get_task(task_id)

    # 审查 #5 / Phase1 Step3：acks_late 可见性超时重投会派第二个 Worker 进来。
    # 执行前先原子抢租约（返回 execution_id，owner=worker+租约实例）：抢不到
    # 说明已有 Worker 在跑同一 thread_id —— 直接退出，不得进入执行分支
    # （LLM 重复烧钱、step_results 互踩）。
    lease_id = task_service.try_acquire_lease(task_id, worker=hostname or None)
    if not lease_id:
        logger.warning(
            "[AgentTask] %s lease held by another worker (acks_late 重投?), skip",
            task_id)
        return {"status": "RUNNING_ELSEWHERE"}
    logger.info("[AgentTask] %s lease acquired execution_id=%s worker=%s",
                task_id, lease_id[:8], hostname or "unknown")

    if retries:
        task_service.increment_retry(task_id)
        logger.warning("[AgentTask] retry #%d for %s (从 checkpoint 续跑)",
                       retries, task_id)

    executor = TaskGraphExecutor()
    try:
        output = executor.execute(record)
    except Exception as e:
        # 区分三类终态异常（禁止裸 except-pass；每类都有明确落库语义）
        from backend.orchestration.checkpoint import TaskCancelled, TaskPaused

        if isinstance(e, TaskCancelled):
            _fail(task_id, "用户取消", TaskStatus.CANCELLED, "已取消")
            return {"status": "CANCELLED"}
        if isinstance(e, TaskPaused):
            _fail(task_id, "用户暂停", TaskStatus.PAUSED, "已暂停，可 resume 恢复")
            return {"status": "PAUSED"}
        raise  # 其余异常上抛 → Celery autoretry（续跑）或终审 FAILED
    return {"status": str(output.get("status", TaskStatus.SUCCESS.value)),
            "output": output}


# ═══════════════════════════════════════════════════
# Celery 任务注册
# ═══════════════════════════════════════════════════

def _register_task():
    """惰性注册：celery 未安装时允许模块被非 Worker 进程安全 import（eager 测试前置检查用）。"""
    from backend.tasks.celery_app import celery_app

    @celery_app.task(
        bind=True,
        name="tasks.execute_agent",
        acks_late=True,
        autoretry_for=(Exception,),
        retry_backoff=CELERY_RETRY_BACKOFF,
        retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
        retry_jitter=True,
        max_retries=CELERY_MAX_RETRIES,
        # 终态异常不重试：用户取消/暂停是明确意图；DB 缺行不可恢复
        dont_autoretry_for=_NO_RETRY_EXC + (LookupError,),
    )
    def execute_agent_task(self, task_id: str) -> dict:
        try:
            return execute_agent_task_impl(
                task_id, retries=self.request.retries,
                hostname=getattr(self.request, "hostname", "") or socket.gethostname())
        except SoftTimeLimitExceeded:
            # 超时：自动 FAILED（验收要求），checkpoint 保留供人工 resume
            timeout_exc = SoftTimeLimitExceeded("任务执行超时")
            _fail(task_id, "任务超时（超过 soft time limit）", TaskStatus.FAILED,
                  "执行超时")
            from backend.shared.error_protocol import celery_error_result
            return celery_error_result(
                timeout_exc,
                source="celery.agent",
                reason="timeout",
            )
        except Exception as e:
            retries_left = CELERY_MAX_RETRIES - self.request.retries
            logger.error("[AgentTask] %s failed (剩余重试 %d): %s",
                         task_id, retries_left, e, exc_info=True)
            if retries_left <= 0:
                _fail(task_id, f"重试耗尽: {e}", TaskStatus.FAILED, "执行失败")
            else:
                # 仍会重试：状态记 FAILED + 原因，executor 重试时按 FAILED 续跑
                _fail(task_id, f"执行异常（将重试）: {e}", TaskStatus.FAILED,
                      f"等待第 {self.request.retries + 1} 次重试")
            raise

    return execute_agent_task


execute_agent_task = _register_task()
