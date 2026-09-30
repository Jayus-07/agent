"""test_sse_executor_metrics.py — SSE 执行池观测（P1-3）。

覆盖：
  - _record_executor_wait 三态（None 跳过 / 正常 observe / 异常软失败）
  - 指标在 /metrics 文本输出可见（名称注册即暴露）
  - 端到端（_EchoAgent 假流）：producer 跑完后 active 对称归零、
    wait 分布有样本（打点路径真接线证明）
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class _EchoAgent:
    """立即完成的 fake agent：1 delta + done（同 chat_stream_hardening 模式）。"""

    def stream_events(self, question, session_id, **kwargs):
        yield {"event": "delta", "data": {"content": "ok"}}
        yield {"event": "done", "data": {"elapsed": 0.0, "sources": []}}


@pytest.fixture()
def client(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.chat as chat_mod

    monkeypatch.setattr(chat_mod, "get_multi_agent", lambda: _EchoAgent())
    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    from backend.app.server import app

    return TestClient(app)


def test_record_executor_wait_none_skips():
    from backend.app.api.routes.chat import _record_executor_wait
    from backend.observability.metrics import chat_sse_executor_wait_seconds

    before = chat_sse_executor_wait_seconds._sum.get()
    _record_executor_wait(None)  # 锚丢失：静默跳过，不抛不记
    assert chat_sse_executor_wait_seconds._sum.get() == before


def test_record_executor_wait_observes():
    import time

    from backend.app.api.routes.chat import _record_executor_wait
    from backend.observability.metrics import chat_sse_executor_wait_seconds

    before = chat_sse_executor_wait_seconds._sum.get()
    _record_executor_wait(time.monotonic() - 0.05)
    assert chat_sse_executor_wait_seconds._sum.get() > before


def test_record_executor_wait_soft_fail(monkeypatch):
    from backend.app.api.routes.chat import _record_executor_wait
    from backend.observability import metrics as metrics_mod

    monkeypatch.setattr(
        metrics_mod.chat_sse_executor_wait_seconds, "observe",
        lambda *a: 1 / 0,
    )
    _record_executor_wait(123.0)  # 不抛即通过（软失败）


def test_metrics_exposed_in_prometheus_output():
    from backend.observability.metrics import render_metrics

    body, _ = render_metrics()
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    assert "chat_sse_executor_active" in body
    assert "chat_sse_executor_wait_seconds_bucket" in body


def _hist_count(histogram) -> float:
    """读 Histogram 的累计样本数（prometheus_client 无公开 _count 属性）。"""
    for metric in histogram.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count"):
                return sample.value
    return 0.0


def test_stream_flow_active_returns_to_zero_and_wait_sampled(client):
    """端到端：流跑完 active 归零（inc/dec 对称），wait 至少一个样本。"""
    from backend.observability.metrics import (
        chat_sse_executor_active,
        chat_sse_executor_wait_seconds,
    )

    count_before = _hist_count(chat_sse_executor_wait_seconds)
    with client.stream(
        "POST", "/chat/stream",
        json={"question": "hi", "session_id": "sse-metrics-test"},
        headers={"X-API-Key": "test"},
    ) as resp:
        assert resp.status_code == 200
        body = b"".join(chunk for chunk in resp.iter_raw())
    assert b"event: done" in body
    # 流结束后（finally 已执行）：gauge 必须归零
    assert chat_sse_executor_active._value.get() == 0
    # producer 首行打点过 wait（同进程提交即执行，值可为 0，故断言样本数）
    assert _hist_count(chat_sse_executor_wait_seconds) > count_before
