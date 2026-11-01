"""慢 Provider 不得无限排队或在超时后提前归还执行容量。"""
import threading

import pytest

from backend.providers.travel.live import resilience


@pytest.fixture(autouse=True)
def _fresh_provider_slots(monkeypatch):
    """v16/v17 run 实证：同 worker 前序用例的 loader hang 3s 内 slot 释放晚于
    本用例开始（BoundedSemaphore 模块级共享）→ 4 线程只有 3 个拿到槽。
    本文件用例各自从满容量开始（容量语义不变，只隔离前序泄漏）。"""
    monkeypatch.setattr(resilience, "_provider_slots",
                        threading.BoundedSemaphore(4))


def test_single_flight_reclaims_unique_key_locks():
    flight = resilience._SingleFlight()
    for i in range(1000):
        assert flight.run(str(i), lambda: "ok")[0] == "ok"
    assert len(flight._locks) == 0


def test_provider_bulkhead_rejects_fifth_call_without_queuing(monkeypatch):
    monkeypatch.setattr(resilience, "resolve_budget", lambda op: 2)
    release = threading.Event()
    started = [threading.Event() for _ in range(4)]
    threads = []
    errors = []
    def run(index):
        def loader():
            started[index].set()
            release.wait(3)
        try:
            resilience.call_with_budget("place", loader)
        except TimeoutError:
            errors.append(index)
    fifth_finished = threading.Event()
    fifth_result = []
    def fifth():
        try:
            resilience.call_with_budget("place", lambda: "must not queue")
        except TimeoutError:
            fifth_result.append("rejected")
        finally:
            fifth_finished.set()
    try:
        for i in range(4):
            thread = threading.Thread(target=run, args=(i,))
            threads.append(thread)
            thread.start()
        # 等待窗口只覆盖线程调度（2 核 runner 慢时 1s 内可能起不来，
        # v16 run 41264049361 实证）；拒绝本身是同步即时的（acquire
        # blocking=False），语义断言在 fifth_result == ["rejected"]
        assert all(event.wait(5) for event in started)
        thread = threading.Thread(target=fifth)
        threads.append(thread)
        thread.start()
        assert fifth_finished.wait(5)
        assert fifth_result == ["rejected"]
    finally:
        release.set()
        for thread in threads:
            thread.join(3)


def test_provider_timeout_keeps_parent_execution_permit(monkeypatch):
    from backend.travel.request_runtime import RequestExecutor, RequestRejected

    monkeypatch.setattr(resilience, "resolve_budget", lambda op: 0.02)
    executor = RequestExecutor(workers=1)
    entered, release, outer_finished = threading.Event(), threading.Event(), threading.Event()
    def work(control):
        def loader():
            entered.set()
            release.wait(2)
        with pytest.raises(TimeoutError):
            resilience.call_with_budget("place", loader)
        outer_finished.set()
    handle = executor.submit("t", "u", "c", work)
    try:
        assert entered.wait(1) and outer_finished.wait(1)
        assert not handle.future.done()
        with pytest.raises(RequestRejected):
            executor.submit("t", "v", "d", lambda c: None)
    finally:
        release.set()
        handle.future.result(1)
        executor.shutdown()
