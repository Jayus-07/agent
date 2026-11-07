"""ProviderBreaker（评测熔断器）单元测试——可靠性盘点 D3 缺口补齐。

run_guards.py:229 的 ProviderBreaker 此前全仓零测试。口径：
滑动窗口计数 → 达阈值开路（opened_count 递增 + 审计）→ 冷却后半开清窗 →
record_success 清窗。线程安全经并发冒烟验证。
"""

import threading
from types import SimpleNamespace

import pytest

from backend.evaluation import run_guards
from backend.evaluation.run_guards import ProviderBreaker


@pytest.fixture
def fake_time(monkeypatch):
    """可控时钟：run_guards.time 替换为闭包 shim（仅 ProviderBreaker 消费）。"""
    now = [1000.0]
    monkeypatch.setattr(run_guards, "time", SimpleNamespace(time=lambda: now[0]))
    return now


def _fail(breaker: ProviderBreaker, times: int, run_id: str = "") -> None:
    for _ in range(times):
        breaker.record_failure(run_id=run_id)


def test_below_threshold_stays_closed(fake_time):
    breaker = ProviderBreaker(threshold=3, window_s=10, cooldown_s=5)
    _fail(breaker, 2)
    assert breaker.allow() is True
    assert breaker.open is False
    assert breaker.opened_count == 0


def test_threshold_opens_and_blocks(fake_time):
    breaker = ProviderBreaker(threshold=3, window_s=10, cooldown_s=5)
    _fail(breaker, 3)
    assert breaker.open is True
    assert breaker.allow() is False
    assert breaker.opened_count == 1


def test_window_prune_forgives_stale_failures(fake_time):
    breaker = ProviderBreaker(threshold=3, window_s=10, cooldown_s=5)
    _fail(breaker, 2)
    fake_time[0] += 20  # 旧失败滑出窗口
    breaker.record_failure()
    assert breaker.open is False
    assert breaker.allow() is True


def test_cooldown_expiry_half_open_resets_window(fake_time):
    breaker = ProviderBreaker(threshold=2, window_s=10, cooldown_s=5)
    _fail(breaker, 2)
    assert breaker.open is True
    fake_time[0] += 6  # 冷却结束 → 半开放行并清窗
    assert breaker.allow() is True
    assert breaker.open is False
    breaker.record_failure()
    assert breaker.open is False  # 清窗后单次失败不重开
    breaker.record_failure()
    assert breaker.open is True
    assert breaker.opened_count == 2


def test_record_success_clears_window(fake_time):
    breaker = ProviderBreaker(threshold=2, window_s=10, cooldown_s=5)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.open is False
    assert breaker.allow() is True


def test_open_records_audit_once(fake_time, monkeypatch):
    from backend.evaluation import audit as audit_mod

    recorded: list[tuple] = []
    monkeypatch.setattr(
        audit_mod, "record_operation",
        lambda kind, run_id, **kw: recorded.append((kind, run_id)),
    )
    breaker = ProviderBreaker(threshold=2, window_s=10, cooldown_s=5)
    _fail(breaker, 3, run_id="run-1")
    assert recorded == [("eval_run.provider_breaker_open", "run-1")]


def test_concurrent_failures_open_exactly_once(fake_time):
    breaker = ProviderBreaker(threshold=5, window_s=10, cooldown_s=5)
    barrier = threading.Barrier(20)

    def worker() -> None:
        barrier.wait()
        breaker.record_failure()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert breaker.opened_count == 1
    assert breaker.open is True
