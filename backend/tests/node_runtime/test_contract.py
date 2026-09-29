"""tests/node_runtime/test_contract.py — Node Runtime 公共契约（STOP F）

锁：NodeResult 四字段冻结 / ExecutionContext 四字段冻结 / 三种 ErrorPolicy /
TimeoutStrategy 守卫 / hooks 回调时序 / fn 自报状态透传。
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.core.node_runtime import (
    ErrorPolicy,
    ExecutionContext,
    NodeResult,
    NodeRunner,
    NodeStatus,
    ObservabilityHooks,
    TimeoutStrategy,
)


class RecordingHooks(ObservabilityHooks):
    """记录回调序的测试钩子。"""

    def __init__(self):
        self.events = []

    def on_start(self, ctx):
        self.events.append(("start", ctx.node_name))

    def on_success(self, ctx, *, status, duration_ms):
        self.events.append(("success", status, duration_ms))

    def on_error(self, ctx, *, kind, duration_ms, exc=None):
        self.events.append(("error", kind, duration_ms, exc))


def _ctx(name="n1", **kw):
    return ExecutionContext(node_name=name, domain="test", **kw)


class TestFrozenModels:
    def test_node_result_exactly_four_fields(self):
        """审计 §6 风险5：通用模型禁止业务字段渗入。"""
        assert [f.name for f in dataclasses.fields(NodeResult)] == [
            "status", "error", "duration_ms", "data",
        ]

    def test_node_result_is_frozen(self):
        nr = NodeResult(status="success")
        with pytest.raises(dataclasses.FrozenInstanceError):
            nr.status = "failed"

    def test_node_status_values(self):
        assert NodeStatus.SUCCESS.value == "success"
        assert NodeStatus.FAILED.value == "failed"
        assert NodeStatus.TIMEOUT.value == "timeout"

    def test_exec_context_exactly_four_fields(self):
        assert [f.name for f in dataclasses.fields(ExecutionContext)] == [
            "node_name", "domain", "deadline", "tags",
        ]

    def test_exec_context_is_frozen(self):
        ctx = _ctx()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.node_name = "other"

    def test_policy_enum_values(self):
        assert ErrorPolicy.RAISE_THROUGH.value == "raise_through"
        assert ErrorPolicy.SWALLOW_TO_STATUS.value == "swallow_to_status"
        assert ErrorPolicy.FALLBACK.value == "fallback"
        assert TimeoutStrategy.NONE.value == "none"
        assert TimeoutStrategy.THREAD_ISOLATED.value == "thread_isolated"


class TestErrorPolicy:
    def test_swallow_to_status_returns_failed(self):
        def fn(state):
            raise ValueError("boom")

        nr = NodeRunner().run(_ctx(), fn, {})
        assert nr.status == NodeStatus.FAILED.value
        assert nr.error == "boom"
        assert nr.duration_ms >= 0

    def test_raise_through_propagates_without_on_error(self):
        hooks = RecordingHooks()

        def fn(state):
            raise ValueError("穿透")

        with pytest.raises(ValueError, match="穿透"):
            NodeRunner().run(_ctx(), fn, {}, policy=ErrorPolicy.RAISE_THROUGH,
                             hooks=hooks)
        # RAISE_THROUGH 不触发 on_error：上层观测面（如 TraceMiddleware）自会记录
        assert ("error", "exception", pytest.approx(0, abs=10_000), None) not in hooks.events
        assert not [e for e in hooks.events if e[0] == "error"]

    def test_fallback_returns_value_and_marks_error(self):
        hooks = RecordingHooks()
        fallback = {"canned": True}

        def fn(state):
            raise RuntimeError("挂了")

        nr = NodeRunner().run(_ctx(), fn, {}, policy=ErrorPolicy.FALLBACK,
                              hooks=hooks, fallback=fallback)
        assert nr.status == NodeStatus.SUCCESS.value
        assert nr.data is fallback
        # 异常本身仍经 on_error 留痕（可观测不丢）
        errors = [e for e in hooks.events if e[0] == "error"]
        assert len(errors) == 1
        assert errors[0][1] == "exception"
        assert isinstance(errors[0][3], RuntimeError)


class TestLifecycle:
    def test_success_passes_fn_status_through(self):
        hooks = RecordingHooks()

        def fn(state):
            return {"status": "skipped", "payload": 1}

        nr = NodeRunner().run(_ctx(), fn, {}, hooks=hooks)
        assert nr.status == "skipped"
        assert nr.data == {"status": "skipped", "payload": 1}
        assert hooks.events == [
            ("start", "n1"),
            ("success", "skipped", nr.duration_ms),
        ]

    def test_success_defaults_status_to_success(self):
        nr = NodeRunner().run(_ctx(), lambda s: {"x": 1}, {})
        assert nr.status == NodeStatus.SUCCESS.value
        assert nr.data == {"x": 1}

    def test_non_dict_result_defaults_to_success(self):
        nr = NodeRunner().run(_ctx(), lambda s: "raw", {})
        assert nr.status == NodeStatus.SUCCESS.value
        assert nr.data == "raw"

    def test_default_noop_hooks(self):
        """不传 hooks 时全流程可用（noop）。"""
        nr = NodeRunner().run(_ctx("n2"), lambda s: {"ok": True}, {})
        assert nr.status == "success"

    def test_hook_exception_not_swallowed_by_runner(self):
        """runner 不兜底钩子异常——钩子有 bug 应当暴露。"""
        class BadHooks(ObservabilityHooks):
            def on_start(self, ctx):
                raise RuntimeError("hook bug")

        with pytest.raises(RuntimeError, match="hook bug"):
            NodeRunner().run(_ctx(), lambda s: {}, {}, hooks=BadHooks())


class TestTimeoutStrategy:
    def test_thread_isolated_without_deadline_fails_fast(self):
        with pytest.raises(ValueError, match="THREAD_ISOLATED"):
            NodeRunner().run(_ctx(deadline=None), lambda s: {}, {},
                             timeout_strategy=TimeoutStrategy.THREAD_ISOLATED)

    def test_thread_isolated_success(self):
        nr = NodeRunner().run(_ctx(deadline=5), lambda s: {"v": 1}, {},
                              timeout_strategy=TimeoutStrategy.THREAD_ISOLATED)
        assert nr.status == NodeStatus.SUCCESS.value
        assert nr.data == {"v": 1}

    def test_thread_isolated_exception_maps_by_policy(self):
        def fn(state):
            raise ValueError("线程内异常")

        nr = NodeRunner().run(_ctx(deadline=5), fn, {},
                              timeout_strategy=TimeoutStrategy.THREAD_ISOLATED)
        assert nr.status == NodeStatus.FAILED.value
        assert nr.error == "线程内异常"

    def test_thread_isolated_timeout_returns_timeout(self):
        import time as _time

        def slow(state):
            _time.sleep(1.0)
            return {"status": "success"}

        hooks = RecordingHooks()
        nr = NodeRunner().run(_ctx(deadline=0.1), slow, {},
                              timeout_strategy=TimeoutStrategy.THREAD_ISOLATED,
                              hooks=hooks)
        assert nr.status == NodeStatus.TIMEOUT.value
        assert "timed out" in nr.error
        assert nr.duration_ms >= 100
        errors = [e for e in hooks.events if e[0] == "error"]
        assert len(errors) == 1 and errors[0][1] == "timeout"
