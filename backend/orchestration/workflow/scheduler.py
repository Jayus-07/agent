"""workflow/scheduler.py — Workflow Scheduler（基于 APScheduler）

设计原则：
- 与 data_collection/scheduler.py 解耦（职责清晰）
- 复用 WorkflowRegistry（注册 workflow）
- 复用 WorkflowExecutor（执行 workflow）
- 提供 register_daily / register_interval / run_now / start / stop 接口
"""
from __future__ import annotations

from typing import Any
import asyncio
from datetime import datetime

from backend.orchestration.workflow.registry import WorkflowRegistry, get_workflow_registry
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.shared.logger import logger

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("[WorkflowScheduler] APScheduler 未安装，定时任务不可用，仅支持手动触发")


class WorkflowScheduler:
    """Workflow 调度器（手动触发 + 定时触发）"""

    def __init__(
        self,
        registry: WorkflowRegistry | None = None,
        executor: WorkflowExecutor | None = None,
    ):
        self.registry = registry or get_workflow_registry()
        self.executor = executor or WorkflowExecutor(registry=self.registry)
        self._aps: Any | None = None
        self._jobs: dict[str, dict[str, Any]] = {}  # 跟踪注册的定时任务
        logger.info(
            f"[WorkflowScheduler] 初始化: {len(self.registry._workflows)} 个 workflow"
        )

    async def run_now(self, workflow_name: str, inputs: dict | None = None):
        """手动触发 workflow

        Returns:
            WorkflowContext: 运行结果
        """
        logger.info(f"[WorkflowScheduler] 手动触发: {workflow_name}")
        return await self.executor.run(workflow_name, inputs or {})

    def register_daily(self, workflow_name: str, hour: int, minute: int = 0):
        """注册每天定时触发"""
        if not APSCHEDULER_AVAILABLE:
            raise RuntimeError("APScheduler 未安装")
        self._ensure_aps()
        job_id = f"{workflow_name}_daily_{hour:02d}{minute:02d}"
        self._aps.add_job(
            self._run_async,
            CronTrigger(hour=hour, minute=minute),
            id=job_id,
            args=[workflow_name],
            replace_existing=True,
        )
        self._jobs[job_id] = {
            "workflow": workflow_name,
            "trigger": f"daily {hour:02d}:{minute:02d}",
        }
        logger.info(
            f"[WorkflowScheduler] 注册定时: {workflow_name} 每天 {hour:02d}:{minute:02d}"
        )

    def register_interval(self, workflow_name: str, seconds: int):
        """注册周期性触发"""
        if not APSCHEDULER_AVAILABLE:
            raise RuntimeError("APScheduler 未安装")
        self._ensure_aps()
        job_id = f"{workflow_name}_interval_{seconds}"
        self._aps.add_job(
            self._run_async,
            "interval",
            seconds=seconds,
            id=job_id,
            args=[workflow_name],
            replace_existing=True,
        )
        self._jobs[job_id] = {
            "workflow": workflow_name,
            "trigger": f"interval {seconds}s",
        }
        logger.info(f"[WorkflowScheduler] 注册周期: {workflow_name} 每 {seconds}s")

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出已注册的定时任务（含下次执行时间）"""
        result = []
        for job_id, info in self._jobs.items():
            job = {"id": job_id, **info}
            if self._aps is not None:
                aps_job = self._aps.get_job(job_id)
                if aps_job is not None:
                    job["next_run_time"] = (
                        aps_job.next_run_time.isoformat()
                        if aps_job.next_run_time else None
                    )
            result.append(job)
        return result

    def reschedule_daily(self, workflow_name: str, hour: int, minute: int):
        """修改已有 daily 定时任务的时间并立即生效"""
        if not APSCHEDULER_AVAILABLE:
            raise RuntimeError("APScheduler 未安装")
        self._ensure_aps()
        job_id = f"{workflow_name}_daily_{hour:02d}{minute:02d}"
        # 找旧 job_id 并删除
        for old_id in list(self._jobs.keys()):
            if old_id.startswith(f"{workflow_name}_daily_"):
                if self._aps.get_job(old_id):
                    self._aps.remove_job(old_id)
                self._jobs.pop(old_id, None)
                logger.info(f"[WorkflowScheduler] 移除旧定时: {old_id}")
        # 注册新时间
        self._aps.add_job(
            self._run_async,
            CronTrigger(hour=hour, minute=minute),
            id=job_id,
            args=[workflow_name],
            replace_existing=True,
        )
        self._jobs[job_id] = {
            "workflow": workflow_name,
            "trigger": f"daily {hour:02d}:{minute:02d}",
        }
        logger.info(
            f"[WorkflowScheduler] 重新调度: {workflow_name} → "
            f"每天 {hour:02d}:{minute:02d}"
        )
        return job_id

    def get_job(self, workflow_name: str) -> dict[str, Any] | None:
        """按 workflow 名查找 job"""
        for job_id, info in self._jobs.items():
            if info.get("workflow") == workflow_name:
                job = {"id": job_id, **info}
                if self._aps is not None:
                    aps_job = self._aps.get_job(job_id)
                    if aps_job is not None:
                        job["next_run_time"] = (
                            aps_job.next_run_time.isoformat()
                            if aps_job.next_run_time else None
                        )
                return job
        return None

    def start(self):
        """启动 APScheduler"""
        if not APSCHEDULER_AVAILABLE:
            logger.warning("[WorkflowScheduler] APScheduler 未安装，跳过 start()")
            return
        self._ensure_aps()
        if not self._aps.running:
            self._aps.start()
            logger.info("[WorkflowScheduler] APScheduler 已启动")

    def stop(self):
        """停止 APScheduler"""
        if self._aps is not None and self._aps.running:
            self._aps.shutdown()
            logger.info("[WorkflowScheduler] APScheduler 已停止")

    def _ensure_aps(self):
        """确保 APScheduler 实例化"""
        if self._aps is None:
            self._aps = AsyncIOScheduler()

    async def _run_async(self, workflow_name: str):
        """APScheduler 触发器包装（async context）"""
        try:
            ctx = await self.executor.run(workflow_name)
            logger.info(
                f"[WorkflowScheduler] 定时触发完成: {workflow_name} "
                f"status={ctx.status}"
            )
        except Exception as e:
            logger.error(f"[WorkflowScheduler] 定时触发失败: {workflow_name}: {e}")


# 模块级单例
_scheduler: WorkflowScheduler | None = None


def get_workflow_scheduler() -> WorkflowScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = WorkflowScheduler()
    return _scheduler