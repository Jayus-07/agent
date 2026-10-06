"""STOP CS-A P0-3 回归：请求取消契约（cancellation propagation）。"""
from __future__ import annotations

import threading

import pytest

from backend.core.request_context import (
    RequestCancelled,
    bind_cancel_event,
    is_cancelled,
    raise_if_cancelled,
)


class TestCancelToken:

    def test_no_event_bound_not_cancelled(self):
        bind_cancel_event(None)
        assert is_cancelled() is False
        raise_if_cancelled("any")  # 不抛

    def test_unset_event_not_cancelled(self):
        event = threading.Event()
        bind_cancel_event(event)
        try:
            assert is_cancelled() is False
        finally:
            bind_cancel_event(None)

    def test_set_event_raises_with_stage(self):
        event = threading.Event()
        event.set()
        bind_cancel_event(event)
        try:
            assert is_cancelled() is True
            with pytest.raises(RequestCancelled) as exc_info:
                raise_if_cancelled("cs_supervisor")
            assert exc_info.value.stage == "cs_supervisor"
        finally:
            # ContextVar 泄漏会毒化同线程后续测试（expert/cs 节点误判取消）
            bind_cancel_event(None)

    def test_contextvar_propagates_into_copied_context(self):
        """ContextVar 载体可随 copy_context 传播（LangGraph 节点线程机制）。"""
        import contextvars

        event = threading.Event()
        event.set()
        bind_cancel_event(event)
        try:

            def _check():
                return is_cancelled()

            ctx = contextvars.copy_context()
            assert ctx.run(_check) is True
        finally:
            bind_cancel_event(None)


class TestCSGraphNodeCancellation:
    """cs_graph_node：入口检查 + RequestCancelled → cancelled 更新（非兜底话术）。"""

    def test_cancelled_at_entry_returns_cancelled_update(self, monkeypatch):
        event = threading.Event()
        event.set()
        from backend.core import request_context

        token = request_context._current_cancel_event.set(event)
        try:
            # get_cs_graph 不应被调用（入口检查先于 invoke）
            monkeypatch.setattr(
                "backend.orchestration.graph.cs_graph_node.get_cs_graph",
                lambda: (_ for _ in ()).throw(AssertionError("graph must not run")),
            )
            from backend.orchestration.graph.cs_graph_node import cs_graph_node

            update = cs_graph_node({
                "question": "hi",
                "cs_context": {
                    "authenticated_user_id": "u1",
                    "session_id": "s1",
                    "conversation_id": "c1",
                    "tenant_id": "t1",
                },
            })
        finally:
            request_context._current_cancel_event.reset(token)

        assert update["final_answer"] == ""  # 不产生正常回复 → runner 不落库
        ctx = update["cs_context"]
        assert ctx["cancelled"] is True
        assert ctx["cancel_stage"] == "cs_graph_entry"

    def test_graph_exception_still_falls_back(self, monkeypatch):
        """非取消异常仍走兜底（取消语义不吞掉真实故障）。"""
        from backend.core import request_context

        request_context._current_cancel_event.set(None)
        try:
            monkeypatch.setattr(
                "backend.orchestration.graph.cs_graph_node.get_cs_graph",
                lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            from backend.orchestration.graph.cs_graph_node import cs_graph_node

            update = cs_graph_node({"cs_context": {}})
            assert "暂时不可用" in update["final_answer"]
        finally:
            request_context._current_cancel_event.set(None)


class TestExpertBoundary:

    def test_run_expert_safely_raises_when_cancelled(self):
        """Expert 开始前的取消检查不被 SWALLOW_TO_STATUS 吞成 failed。"""
        from backend.customer_service.experts.base import run_expert_safely

        event = threading.Event()
        event.set()
        bind_cancel_event(event)
        try:
            with pytest.raises(RequestCancelled) as exc_info:
                run_expert_safely("knowledge", lambda s: {}, {})
            assert exc_info.value.stage == "cs_expert:knowledge"
        finally:
            bind_cancel_event(None)

    def test_run_expert_safely_normal_when_not_cancelled(self):
        bind_cancel_event(None)
        from backend.customer_service.experts.base import run_expert_safely

        result = run_expert_safely("query", lambda s: {"status": "success"}, {})
        assert result["status"] == "success"
