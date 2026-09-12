"""StreamLatencyTracker 单测 — TTFT/TPOT 采样逻辑。

TPOT = (末 delta − 首 delta) / (delta 数 − 1)；delta < 2 不采样。
"""
import pytest

from backend.observability.metrics import StreamLatencyTracker


@pytest.fixture(autouse=True)
def _isolate_metrics():
    """用独立 registry 前的简单手段：Histogram 值不可读清零，这里只验证不抛错，
    数值断言通过 tracker 内部状态与 monkeypatch 的 observe 捕获完成。"""
    yield


def test_ttft_recorded_on_first_delta(monkeypatch):
    observed = {}
    import backend.observability.metrics as m
    monkeypatch.setattr(
        m.chat_ttft_seconds, "observe", lambda v: observed.setdefault("ttft", v)
    )

    tracker = StreamLatencyTracker(start=100.0)
    assert tracker.delta_count == 0
    tracker.on_delta(101.5)
    assert tracker.delta_count == 1
    assert observed["ttft"] == pytest.approx(1.5)


def test_tpot_computed_on_finish(monkeypatch):
    observed = {}
    import backend.observability.metrics as m
    monkeypatch.setattr(
        m.chat_ttft_seconds, "observe", lambda v: None
    )
    monkeypatch.setattr(
        m.chat_tpot_seconds, "observe", lambda v: observed.setdefault("tpot", v)
    )

    tracker = StreamLatencyTracker(start=0.0)
    tracker.on_delta(1.0)   # TTFT = 1.0s
    tracker.on_delta(1.1)
    tracker.on_delta(1.3)   # decode 0.3s / 2 间隔 = 0.15s per token
    tracker.finish()
    assert observed["tpot"] == pytest.approx(0.15)


def test_finish_idempotent(monkeypatch):
    calls = []
    import backend.observability.metrics as m
    monkeypatch.setattr(
        m.chat_tpot_seconds, "observe", lambda v: calls.append(v)
    )

    tracker = StreamLatencyTracker(start=0.0)
    tracker.on_delta(0.5)
    tracker.on_delta(1.0)
    tracker.finish()
    tracker.finish()
    assert len(calls) == 1


def test_skip_tpot_when_single_delta(monkeypatch):
    calls = []
    import backend.observability.metrics as m
    monkeypatch.setattr(
        m.chat_tpot_seconds, "observe", lambda v: calls.append(v)
    )

    tracker = StreamLatencyTracker(start=0.0)
    tracker.on_delta(0.5)   # 只有 1 个 delta：无 decode 过程，TTFT 采样但 TPOT 跳过
    tracker.finish()
    assert calls == []


def test_no_delta_no_metrics():
    tracker = StreamLatencyTracker(start=0.0)
    tracker.finish()  # 不应抛错，也不采样
    assert tracker.delta_count == 0
