"""超时工作线程必须继承请求上下文，并保持并发隔离。"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier

import pytest

from backend.infra.timeout import _timeout_windows, safe_call_with_timeout


@pytest.mark.parametrize("platform", ["Windows", "Linux"])
def test_timeout_inherits_context_and_isolates_requests(monkeypatch, platform):
    monkeypatch.setattr("platform.system", lambda: platform)
    request_id = ContextVar("timeout_test_request_id", default="")
    barrier = Barrier(2)

    def run(value):
        token = request_id.set(value)
        try:
            def invoke():
                barrier.wait(timeout=5)
                inherited = request_id.get()
                request_id.set("worker-only")
                return inherited

            assert safe_call_with_timeout(invoke, timeout=10) == value
            assert request_id.get() == value
        finally:
            request_id.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, value) for value in ("request-a", "request-b")]
        for future in futures:
            future.result(timeout=15)
    assert request_id.get() == ""


def test_thread_timeout_still_propagates_exception():
    def fail():
        raise ValueError("真实调用失败")

    with pytest.raises(ValueError, match="真实调用失败"):
        _timeout_windows(fail, 2, "超时")
