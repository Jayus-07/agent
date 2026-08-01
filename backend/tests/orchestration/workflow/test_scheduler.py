"""test_scheduler.py — WorkflowScheduler + APScheduler

覆盖：
- run_now 手动触发
- register_daily / register_interval (patch APSCHEDULER_AVAILABLE)
- list_jobs
- start / stop
- APScheduler 未装时降级
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch as mp

import pytest

from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.scheduler import (
    APSCHEDULER_AVAILABLE,
    WorkflowScheduler,
    get_workflow_scheduler,
)
from backend.orchestration.workflow import workflow, step


# ─────────────────────────────────────────────────────────────
# run_now
# ─────────────────────────────────────────────────────────────

class TestSchedulerRunNow:
    """手动触发 workflow"""

    def test_run_now_executes_workflow(
        self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm
    ):
        """run_now 调 executor 跑 workflow"""
        @workflow(name="sched_wf")
        class TWF:
            @step()
            async def s(self, ctx):
                return {"ok": True}

        fresh_registry.register(TWF)
        executor = WorkflowExecutor(registry=fresh_registry)
        scheduler = WorkflowScheduler(registry=fresh_registry, executor=executor)

        ctx = asyncio.run(scheduler.run_now("sched_wf", {"x": 1}))
        assert ctx.status == "success"

    def test_run_now_unknown_workflow_returns_failed(
        self, fresh_registry, patched_trace_collector, patched_persistence
    ):
        """run_now 未知 workflow 返回 failed"""
        scheduler = WorkflowScheduler(registry=fresh_registry)
        ctx = asyncio.run(scheduler.run_now("nonexistent"))
        assert ctx.status == "failed"


# ─────────────────────────────────────────────────────────────
# register_daily / register_interval
# ─────────────────────────────────────────────────────────────

class TestSchedulerRegistration:
    """定时注册"""

    def test_register_daily_with_apscheduler_available(
        self, patched_apscheduler, fresh_registry, patched_trace_collector, patched_persistence
    ):
        """APScheduler 可用时注册 cron"""
        @workflow(name="daily_sched")
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        scheduler = WorkflowScheduler(registry=fresh_registry)
        scheduler.register_daily("daily_sched", hour=9, minute=0)

        # APScheduler.add_job 被调用
        patched_apscheduler.add_job.assert_called_once()
        call_args = patched_apscheduler.add_job.call_args
        # args 第一个位置是 trigger function，kwargs["args"]=[workflow_name]
        assert callable(call_args.args[0])
        # workflow_name 通过 kwargs args 传递
        assert call_args.kwargs.get("args") == ["daily_sched"]
        # trigger 是 CronTrigger（位置参数 1）
        from apscheduler.triggers.cron import CronTrigger
        assert isinstance(call_args.args[1], CronTrigger)

    def test_register_daily_without_apscheduler_raises(
        self, monkeypatch, fresh_registry
    ):
        """APScheduler 未装时 register_daily 抛 RuntimeError"""
        @workflow(name="daily_sched")
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        monkeypatch.setattr(
            "backend.orchestration.workflow.scheduler.APSCHEDULER_AVAILABLE",
            False,
        )
        scheduler = WorkflowScheduler(registry=fresh_registry)

        with pytest.raises(RuntimeError, match="APScheduler"):
            scheduler.register_daily("daily_sched", hour=9)

    def test_register_interval(self, patched_apscheduler, fresh_registry):
        """register_interval 也调 add_job"""
        @workflow(name="interval_wf")
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        scheduler = WorkflowScheduler(registry=fresh_registry)
        scheduler.register_interval("interval_wf", seconds=300)

        patched_apscheduler.add_job.assert_called_once()
        # trigger='interval' 是关键字参数
        assert "interval" in str(patched_apscheduler.add_job.call_args)


# ─────────────────────────────────────────────────────────────
# list_jobs
# ─────────────────────────────────────────────────────────────

class TestSchedulerListJobs:
    """列定时任务"""

    def test_list_jobs_empty_initially(self, fresh_registry):
        """初始空列表"""
        scheduler = WorkflowScheduler(registry=fresh_registry)
        assert scheduler.list_jobs() == []

    def test_list_jobs_after_register(
        self, patched_apscheduler, fresh_registry
    ):
        """注册后能列出"""
        @workflow(name="sched1")
        class T1:
            @step()
            async def s(self, ctx): return {}

        @workflow(name="sched2")
        class T2:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(T1)
        fresh_registry.register(T2)
        scheduler = WorkflowScheduler(registry=fresh_registry)

        scheduler.register_daily("sched1", hour=9)
        scheduler.register_interval("sched2", seconds=300)

        jobs = scheduler.list_jobs()
        assert len(jobs) == 2
        workflows = {j["workflow"] for j in jobs}
        assert workflows == {"sched1", "sched2"}


# ─────────────────────────────────────────────────────────────
# start / stop
# ─────────────────────────────────────────────────────────────

class TestSchedulerLifecycle:
    """start / stop"""

    def test_start_when_apscheduler_unavailable(self, monkeypatch, fresh_registry):
        """APScheduler 未装时 start 不崩（仅 warning）"""
        monkeypatch.setattr(
            "backend.orchestration.workflow.scheduler.APSCHEDULER_AVAILABLE",
            False,
        )
        scheduler = WorkflowScheduler(registry=fresh_registry)
        # 不抛
        scheduler.start()

    def test_start_apscheduler_when_available(self, patched_apscheduler, fresh_registry):
        """APScheduler 可用时调 start()"""
        patched_apscheduler.running = False
        scheduler = WorkflowScheduler(registry=fresh_registry)
        scheduler.start()
        patched_apscheduler.start.assert_called_once()

    def test_stop_when_apscheduler_unavailable(self, monkeypatch, fresh_registry):
        """APScheduler 未装时 stop 不崩"""
        monkeypatch.setattr(
            "backend.orchestration.workflow.scheduler.APSCHEDULER_AVAILABLE",
            False,
        )
        scheduler = WorkflowScheduler(registry=fresh_registry)
        # 不抛
        scheduler.stop()

    def test_stop_apscheduler_when_running(self, patched_apscheduler, fresh_registry):
        """APScheduler.running=True 时调 shutdown"""
        @workflow(name="stop_wf")
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        scheduler = WorkflowScheduler(registry=fresh_registry)
        # 触发 _ensure_apscheduler（调 register_daily 即可）
        scheduler.register_daily("stop_wf", hour=9)

        # 把 mock 的 running 设为 True
        patched_apscheduler.running = True
        scheduler.stop()
        patched_apscheduler.shutdown.assert_called_once()


# ─────────────────────────────────────────────────────────────
# APSCHEDULER_AVAILABLE 常量
# ─────────────────────────────────────────────────────────────

class TestSchedulerConstants:
    """模块级常量"""

    def test_apscheduler_available_default(self):
        """默认 APSCHEDULER_AVAILABLE 跟随 import 时检测"""
        # 这个值在 import 时确定
        from backend.orchestration.workflow import scheduler as sched_mod
        # 如果没装 → False；装了 → True
        assert isinstance(sched_mod.APSCHEDULER_AVAILABLE, bool)