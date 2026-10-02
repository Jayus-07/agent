"""旅游请求执行治理：并发许可必须绑定真实执行寿命。"""
import threading
import time

import pytest


def test_capacity_and_same_session_are_rejected_until_real_completion():
    from backend.travel.request_runtime import RequestExecutor, RequestRejected

    executor = RequestExecutor(workers=1, timeout_s=0.05)
    started = threading.Event()
    release = threading.Event()
    def slow(control):
        started.set()
        release.wait(2)
        return "late"
    handle = executor.submit("t", "u", "c", slow)
    assert started.wait(1)
    with pytest.raises(RequestRejected) as error:
        executor.submit("t", "v", "d", lambda c: "unexpected")
    assert error.value.status_code == 429
    handle.control.cancel("cancelled")
    with pytest.raises(RequestRejected):
        executor.submit("t", "u", "c", lambda c: "unexpected")
    release.set()
    handle.future.result(1)
    executor.shutdown()


def test_same_session_conflict_without_exhausting_capacity():
    from backend.travel.request_runtime import RequestExecutor, RequestRejected

    executor = RequestExecutor(workers=2)
    release = threading.Event()
    a = executor.submit("t", "u", "c", lambda c: release.wait(1))
    try:
        with pytest.raises(RequestRejected) as error:
            executor.submit("t", "u", "c", lambda c: None)
        assert error.value.status_code == 409
        b = executor.submit("t", "v", "c", lambda c: "separate")
        assert b.future.result(1) == "separate"
    finally:
        release.set()
        a.future.result(1)
        executor.shutdown()


def test_deadline_and_context_are_visible_inside_node_boundary():
    from backend.travel.request_runtime import (
        RequestExecutor, RunStopped, check_run, remaining_budget,
    )

    executor = RequestExecutor(workers=1, timeout_s=0.02)
    def work(control):
        assert 0 < remaining_budget(10) <= 0.020001
        time.sleep(0.04)
        check_run()
    handle = executor.submit("t", "u", "c", work)
    with pytest.raises(RunStopped) as error:
        handle.future.result(1)
    assert error.value.reason == "timeout"
    executor.shutdown()


def test_redis_required_fails_closed_without_shared_store():
    from backend.travel.request_runtime import RequestExecutor, RequestRejected

    executor = RequestExecutor(workers=1, lease_store=lambda: None)
    with pytest.raises(RequestRejected) as error:
        executor.submit("t", "u", "c", lambda c: "must not run")
    assert error.value.status_code == 503
    executor.shutdown()


def test_cancelled_control_prevents_node_and_tool_execution():
    from backend.travel.graph_builder import _evented_node
    from backend.travel.core.events import run_travel_tool
    from backend.travel.request_runtime import RequestExecutor, RunStopped

    calls = []
    executor = RequestExecutor(workers=1)
    def work(control):
        control.cancel()
        with pytest.raises(RunStopped):
            _evented_node("test", lambda s: calls.append("node"))({})
        with pytest.raises(RunStopped):
            run_travel_tool("test", "test", lambda: calls.append("tool"))
    executor.submit("t", "u", "c", work).future.result(1)
    assert calls == []
    executor.shutdown()


def test_child_call_retains_capacity_after_outer_work_returns():
    import contextvars
    from backend.travel.request_runtime import (
        RequestExecutor, RequestRejected, child_execution,
    )

    executor = RequestExecutor(workers=1)
    child_started, release = threading.Event(), threading.Event()
    def work(control):
        def child():
            with child_execution():
                child_started.set()
                release.wait(1)
        context = contextvars.copy_context()
        threading.Thread(target=context.run, args=(child,)).start()
        assert child_started.wait(1)
        return "finished outer"
    handle = executor.submit("t", "u", "c", work)
    assert child_started.wait(1)
    try:
        with pytest.raises(RequestRejected):
            executor.submit("t", "v", "d", lambda c: None)
        assert not handle.future.done()
    finally:
        release.set()
        assert handle.future.result(1) == "finished outer"
        executor.shutdown()
