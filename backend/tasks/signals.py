"""tasks/signals.py — Celery 信号埋点（任务运行时字段统一收口）。

职责划分（避免双写冲突）：
- 本模块只写「运行时通用字段」：worker / queue / started_at / finished_at /
  duration_ms / error_type / traceback
- 业务状态语义（CANCELLED / PAUSED / WAITING_USER / output / progress）
  仍归 agent_tasks.py + task_executor.py；本模块对状态只在明确语义下兜底写
  （prerun 把 PENDING 提到 RUNNING；postrun SUCCESS 收尾；failure 终审 FAILED）

注册方式：celery_app.include 含本模块 → Worker 启动自动挂接；
API 进程 import celery_app 不触发（信号只在 worker 侧消费，eager 测试时
celery 也会派发 prerun/postrun，与手动 _fail 幂等不冲突）。

注意：所有回调必须「自身不抛异常」——埋点失败不能影响任务主流程。
"""
from __future__ import annotations

import time

from celery.signals import task_failure, task_postrun, task_prerun, task_retry

from backend.models.task import TaskStatus
from backend.shared.logger import logger

_EXECUTE_TASK_NAME = "tasks.execute_agent"

# 进程内 task_id → prerun 时刻（postrun 兜底算耗时用；正常路径由 DB started_at 算）
_prerun_ts: dict[str, float] = {}


def _owns_execution(task_id: str, record) -> bool:
    """fencing 守卫（Phase2 Step1）：本消息是否有权收尾该任务。

    - DB 行尚无 execution_id（异常残留）：维持旧行为允许收尾
    - impl 已登记租约上下文：必须与 DB 行活跃 execution 一致
    - impl 未登记（本消息从未认领租约，如 RUNNING_ELSEWHERE/短路 NO-OP/
      租约被接管后退出）：一律不收尾——状态权威属于持租约的执行者
    """
    if not record.execution_id:
        return True
    from backend.tasks.execution_context import get_execution

    ctx = get_execution()
    return (ctx is not None and str(ctx[0]) == str(task_id)
            and ctx[1] == record.execution_id)


def _update(task_id: str, **kwargs) -> None:
    """埋点写库兜底：失败只记 debug，不抛出（信号回调不允许影响主流程）。"""
    try:
        from backend.services import task_service

        task_service.update_status(task_id, **kwargs)
    except Exception:  # noqa: BLE001 — 埋点失败不影响任务
        logger.debug("[TaskSignals] update failed for %s", task_id, exc_info=True)


@task_prerun.connect
def _on_prerun(sender=None, task=None, **kwargs):
    """拾取时刻记账（postrun 兜底算耗时用）；不写任何状态。

    Phase1 状态机收口：置 RUNNING 的唯一入口 = try_acquire_lease（认领，
    同步落 worker/started_at）+ executor（恢复/开始执行）。prerun 若在
    租约之前抢先写 RUNNING，会让紧随其后的租约认领（只认领 PENDING）
    永远失败——任务被跳过还被 postrun 兜底成假 SUCCESS（2026-09-21 起
    lease 与 prerun 的时序冲突，本回调回归纯记账）。终态/人工态同理
    不在此处复活：CANCELLED 短路在 impl，FAILED 重试回队在 impl。
    """
    try:
        if task is None or task.name != _EXECUTE_TASK_NAME:
            return
        task_id = (kwargs.get("args") or [None])[0] or kwargs.get("task_id", "")
        if not task_id:
            return
        _prerun_ts[str(task_id)] = time.monotonic()
    except Exception:  # noqa: BLE001
        logger.debug("[TaskSignals] prerun hook failed", exc_info=True)


@task_postrun.connect
def _on_postrun(sender=None, task=None, state=None, **kwargs):
    """SUCCESS 收尾：finished_at + duration。

    守卫：仅当 DB 状态仍在 RUNNING/PENDING 时才写 SUCCESS。被取消/暂停/超时
    _fail 的任务 Celery 侧同样以 SUCCESS 返回（正常 return，非 raise），
    业务终态已由 executor / agent_tasks 落库，不得被本回调覆盖。
    Phase2 Step1 fencing：租约被接管的旧 Worker / 从未持租约的消息
    （RUNNING_ELSEWHERE、短路 NO-OP）一律不得盲写 SUCCESS——那会把
    新 owner 正在执行的任务提前标成终态。
    duration 与 finished_at 在 update_status 的终态分支自动补算，
    此处跳过不丢数据。
    """
    try:
        if task is None or task.name != _EXECUTE_TASK_NAME:
            return
        if state != "SUCCESS":
            return  # FAILURE 走 task_failure；RETRY 由 agent_tasks 记进度
        task_id = str((kwargs.get("args") or [None])[0] or kwargs.get("task_id", "") or "")
        if not task_id:
            return
        start = _prerun_ts.pop(task_id, None)
        from backend.services import task_service

        record = task_service.get_task(task_id)
        if record is None or record.status != TaskStatus.RUNNING:
            return  # PENDING→SUCCESS 状态机已禁：仅 RUNNING 残留允许兜底收尾
        if not _owns_execution(task_id, record):
            return  # 租约已易主/本消息未持租约：不收尾
        duration_ms = int((time.monotonic() - start) * 1000) if start else None
        task_service.update_status(task_id, TaskStatus.SUCCESS, duration_ms=duration_ms)
    except Exception:  # noqa: BLE001
        logger.debug("[TaskSignals] postrun hook failed", exc_info=True)


@task_failure.connect
def _on_failure(sender=None, task=None, **kwargs):
    """终审失败：异常类名 + 堆栈 + finished_at（重试耗尽或不可重试异常时到达）。

    Phase2 Step1 fencing：租约不属于本消息时不得写 FAILED（旧 Worker 的
    异常不能盖掉新 owner 的 RUNNING）。
    """
    try:
        if task is not None and task.name != _EXECUTE_TASK_NAME:
            return
        task_id = str((kwargs.get("args") or [None])[0]
                      or kwargs.get("task_id", "") or "")
        if not task_id:
            return
        exc = kwargs.get("exc")
        tb = kwargs.get("traceback") or ""
        _prerun_ts.pop(task_id, None)
        from backend.services import task_service

        record = task_service.get_task(task_id)
        if record is not None and not _owns_execution(task_id, record):
            return
        _update(
            task_id, TaskStatus.FAILED,
            error_type=type(exc).__name__ if exc else "",
            traceback_text=tb,
            error_message=f"任务执行失败: {exc}" if exc else "任务执行失败",
        )
    except Exception:  # noqa: BLE001
        logger.debug("[TaskSignals] failure hook failed", exc_info=True)


@task_retry.connect
def _on_retry(sender=None, task=None, reason=None, **kwargs):
    """重试排队：记录重试原因（状态语义由 agent_tasks 的 _fail 维持 FAILED）。"""
    try:
        if task is None or task.name != _EXECUTE_TASK_NAME:
            return
        task_id = str((kwargs.get("args") or [None])[0] or kwargs.get("task_id", "") or "")
        if not task_id:
            return
        from backend.services import task_service

        record = task_service.get_task(task_id)
        if record is not None and not _owns_execution(task_id, record):
            return
        _update(task_id, TaskStatus.FAILED,
                progress=f"等待重试: {reason}" if reason else "等待重试")
    except Exception:  # noqa: BLE001
        logger.debug("[TaskSignals] retry hook failed", exc_info=True)
