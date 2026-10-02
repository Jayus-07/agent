"""慢 Provider 不得无限排队或在超时后提前归还执行容量。"""
import threading

import pytest

from backend.providers.travel.live import resilience


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
        assert all(event.wait(1) for event in started)
        thread = threading.Thread(target=fifth)
        threads.append(thread)
        thread.start()
        assert fifth_finished.wait(0.3)
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
