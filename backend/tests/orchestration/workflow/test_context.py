"""test_context.py — WorkflowContext

覆盖：
- 字段默认值
- mark_success / mark_failed / mark_partial
- duration_ms 计算
- summary 导出
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from backend.orchestration.workflow.context import WorkflowContext


# ─────────────────────────────────────────────────────────────
# 字段默认值
# ─────────────────────────────────────────────────────────────

class TestContextDefaults:
    """WorkflowContext 默认值"""

    def test_minimal_construction(self):
        ctx = WorkflowContext(workflow_name="wf", run_id="r1")
        assert ctx.workflow_name == "wf"
        assert ctx.run_id == "r1"
        assert ctx.inputs == {}
        assert ctx.outputs == {}
        assert ctx.trace_id is None
        assert ctx.status == "running"
        assert ctx.error is None
        assert ctx.skip_steps == set()

    def test_started_at_set_on_creation(self):
        ctx = WorkflowContext("wf", "r1")
        assert isinstance(ctx.started_at, datetime)

    def test_finished_at_none_initially(self):
        ctx = WorkflowContext("wf", "r1")
        assert ctx.finished_at is None

    def test_duration_ms_none_when_not_finished(self):
        ctx = WorkflowContext("wf", "r1")
        assert ctx.duration_ms is None


# ─────────────────────────────────────────────────────────────
# mark_* 方法
# ─────────────────────────────────────────────────────────────

class TestContextStatus:
    """mark_* 方法"""

    def test_mark_success_sets_status(self):
        ctx = WorkflowContext("wf", "r1")
        ctx.mark_success()
        assert ctx.status == "success"
        assert ctx.finished_at is not None

    def test_mark_failed_sets_status_and_error(self):
        ctx = WorkflowContext("wf", "r1")
        ctx.mark_failed("出错了")
        assert ctx.status == "failed"
        assert ctx.error == "出错了"
        assert ctx.finished_at is not None

    def test_mark_partial_sets_status(self):
        ctx = WorkflowContext("wf", "r1")
        ctx.mark_partial()
        assert ctx.status == "partial"
        assert ctx.finished_at is not None


# ─────────────────────────────────────────────────────────────
# duration_ms
# ─────────────────────────────────────────────────────────────

class TestContextDuration:
    """duration_ms 计算"""

    def test_duration_ms_after_success(self):
        ctx = WorkflowContext("wf", "r1")
        time.sleep(0.01)
        ctx.mark_success()
        # 至少 10ms
        assert ctx.duration_ms >= 10

    def test_duration_ms_after_failed(self):
        ctx = WorkflowContext("wf", "r1")
        time.sleep(0.01)
        ctx.mark_failed("err")
        assert ctx.duration_ms >= 10


# ─────────────────────────────────────────────────────
# summary
# ─────────────────────────────────────────────────────

class TestContextSummary:
    """summary() 导出"""

    def test_summary_returns_dict(self):
        ctx = WorkflowContext("wf", "r1", inputs={"x": 1})
        ctx.outputs = {"s1": {"v": 2}}
        ctx.mark_success()
        summary = ctx.summary()
        assert isinstance(summary, dict)

    def test_summary_contains_key_fields(self):
        ctx = WorkflowContext("wf", "r1", inputs={"x": 1})
        ctx.outputs = {"s1": {"v": 2}}
        ctx.mark_success()
        summary = ctx.summary()
        assert summary["workflow_name"] == "wf"
        assert summary["run_id"] == "r1"
        assert summary["status"] == "success"
        assert summary["outputs_keys"] == ["s1"]
        assert summary["error"] is None
        assert summary["skip_steps"] == []

    def test_summary_includes_skip_steps(self):
        ctx = WorkflowContext("wf", "r1")
        ctx.skip_steps.add("s1")
        ctx.skip_steps.add("s2")
        ctx.mark_partial()
        summary = ctx.summary()
        assert set(summary["skip_steps"]) == {"s1", "s2"}
        assert summary["status"] == "partial"

    def test_summary_durations_are_isoformat(self):
        ctx = WorkflowContext("wf", "r1")
        ctx.mark_success()
        summary = ctx.summary()
        assert isinstance(summary["started_at"], str)
        assert isinstance(summary["finished_at"], str)