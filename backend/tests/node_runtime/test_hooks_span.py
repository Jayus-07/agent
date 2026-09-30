"""tests/node_runtime/test_hooks_span.py — CsExpertHooks span 生命周期（M14/D14）

锁：span 开启/收口时机与参数（kind=SpanKind.CS_EXPERT）/ 失败分三种 status /
软失败（collector 异常不影响 hooks 调用方）/ 领域 metrics 不受 span 影响。
TravelExpertHooks 同构回归（STOP F 冻结口径不变）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.core.node_runtime.context import ExecutionContext


@pytest.fixture()
def collector(monkeypatch):
    """mock trace_collector：记录 start/end 调用（hooks 为调用时 import，
    patch tracer 模块属性即可生效）。"""
    import backend.observability.tracer as tracer_mod

    fake = MagicMock()
    monkeypatch.setattr(tracer_mod, "trace_collector", fake)
    return fake


def _ctx(name="knowledge", deadline=60.0):
    return ExecutionContext(node_name=name, domain="cs", deadline=deadline)


class TestCsExpertHooksSpan:
    def test_success_opens_and_closes_span(self, collector):
        from backend.core.node_runtime.hooks import CsExpertHooks
        from backend.observability.tracer import SpanKind

        hooks = CsExpertHooks()
        hooks.on_start(_ctx())
        span = collector.start_span.call_args
        assert span.args[0] == "cs_expert_knowledge"
        assert span.kwargs["kind"] == SpanKind.CS_EXPERT.value
        assert span.kwargs["type"] == "agent"

        hooks.on_success(_ctx(), status="success", duration_ms=123)
        end = collector.end_span.call_args
        assert end.args[0] is collector.start_span.return_value
        assert end.kwargs["status"] == "success"
        assert end.kwargs["metrics"]["expert_status"] == "success"
        assert end.kwargs["metrics"]["duration_ms"] == 123

    def test_error_timeout_marks_error_status(self, collector):
        from backend.core.node_runtime.hooks import CsExpertHooks

        hooks = CsExpertHooks()
        hooks.on_start(_ctx())
        hooks.on_error(_ctx(), kind="timeout", duration_ms=60_000)
        end = collector.end_span.call_args
        assert end.kwargs["status"] == "error"
        assert end.kwargs["metrics"]["expert_status"] == "timeout"

    def test_error_exception_carries_message(self, collector):
        from backend.core.node_runtime.hooks import CsExpertHooks

        hooks = CsExpertHooks()
        hooks.on_start(_ctx())
        hooks.on_error(_ctx(), kind="exception", duration_ms=5,
                       exc=ValueError("boom"))
        end = collector.end_span.call_args
        assert end.kwargs["status"] == "error"
        assert end.kwargs["metrics"]["expert_status"] == "failed"
        assert end.kwargs["metrics"]["error"] == "boom"

    def test_domain_metrics_still_recorded(self, collector, monkeypatch):
        """span 补齐不得影响既有领域 metrics 埋点（M9 口径保持）。"""
        from backend.core.node_runtime.hooks import CsExpertHooks

        recorded = []
        import backend.observability.metrics as m
        monkeypatch.setattr(
            m, "record_cs_expert_result",
            lambda name, status: recorded.append((name, status)))

        hooks = CsExpertHooks()
        hooks.on_start(_ctx())
        hooks.on_success(_ctx(), status="success", duration_ms=1)
        hooks.on_error(_ctx(), kind="timeout", duration_ms=2)
        assert recorded == [("knowledge", "success"), ("knowledge", "timeout")]

    def test_collector_failure_is_soft(self, collector):
        """collector.start_span 抛异常 → hooks 不崩、后续收口为 no-op。"""
        from backend.core.node_runtime.hooks import CsExpertHooks

        collector.start_span.side_effect = RuntimeError("no trace")
        hooks = CsExpertHooks()
        hooks.on_start(_ctx())                     # 不抛
        hooks.on_success(_ctx(), status="success", duration_ms=1)  # end no-op
        collector.end_span.assert_not_called()

    def test_end_span_failure_is_soft(self, collector):
        from backend.core.node_runtime.hooks import CsExpertHooks

        collector.end_span.side_effect = RuntimeError("write fail")
        hooks = CsExpertHooks()
        hooks.on_start(_ctx())
        hooks.on_success(_ctx(), status="success", duration_ms=1)  # 不抛

    def test_end_without_start_is_noop(self, collector):
        from backend.core.node_runtime.hooks import CsExpertHooks

        hooks = CsExpertHooks()
        hooks.on_success(_ctx(), status="success", duration_ms=1)
        collector.end_span.assert_not_called()


class TestTravelExpertHooksRegression:
    def test_span_lifecycle_unchanged(self, collector):
        """STOP F 迁移实现回归：travel 专家 span 口径保持。"""
        from backend.core.node_runtime.hooks import TravelExpertHooks

        hooks = TravelExpertHooks()
        hooks.on_start(_ctx("poi"))
        assert collector.start_span.call_args.args[0] == "travel_expert_poi"
        hooks.on_success(_ctx("poi"), status="success", duration_ms=9)
        assert collector.end_span.call_args.kwargs["status"] == "success"
