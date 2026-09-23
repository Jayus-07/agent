"""Model Governance STOP C —— Streaming usage（C13）。

provider 不回 usage chunk / 客户端提前中断时：显式打点 + warning，
禁止静默丢量。astream/stream 两条包装路径都覆盖。
"""
from __future__ import annotations

import asyncio

import pytest

from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.resolved_model import reset_current_resolved_model


@pytest.fixture(autouse=True)
def _state():
    proxy_mod._last_tokens_var.set({})
    proxy_mod._last_call_meta_var.set({})
    reset_current_resolved_model()
    proxy_mod.set_request_model("")
    yield
    proxy_mod._last_tokens_var.set({})
    proxy_mod._last_call_meta_var.set({})
    reset_current_resolved_model()
    proxy_mod.set_request_model("")


class _Chunk:
    def __init__(self, text="", usage_metadata=None):
        self.content = text
        self.usage_metadata = usage_metadata
        self.response_metadata = {}


class _FakeStreamer:
    """底层模型：astream 产 3 个内容 chunk、无 usage chunk。"""

    def __init__(self, with_usage=False):
        self._with_usage = with_usage

    async def astream(self, *args, **kwargs):
        for i in range(3):
            usage = {"total_tokens": 10} if (
                self._with_usage and i == 2) else None
            yield _Chunk(text=f"chunk{i}", usage_metadata=usage)

    def stream(self, *args, **kwargs):
        for i in range(3):
            usage = {"total_tokens": 10} if (
                self._with_usage and i == 2) else None
            yield _Chunk(text=f"chunk{i}", usage_metadata=usage)


def _inc_counter(monkeypatch) -> list:
    incs = []

    class _FakeCounter:
        def inc(self, n=1):
            incs.append(n)

    import backend.observability.metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "llm_usage_missing_total", _FakeCounter(),
                        raising=False)
    return incs


def _reserve_noop(monkeypatch):
    calls = []
    import backend.infra.llm.budget as budget_mod

    monkeypatch.setattr(budget_mod, "reserve_model_call",
                        lambda *a, **k: calls.append(("reserve", k)))
    monkeypatch.setattr(budget_mod, "release_model_reservation",
                        lambda: calls.append(("release",)))
    return calls


def test_astream_without_usage_chunk_increments_missing(monkeypatch):
    incs = _inc_counter(monkeypatch)
    _reserve_noop(monkeypatch)
    monkeypatch.setattr(proxy_mod, "_enforce_rate_limit", lambda uid: None)
    monkeypatch.setattr(proxy_mod, "_preflight_context",
                        lambda args, kwargs: args)
    monkeypatch.setattr(proxy_mod, "get_active_model_name",
                        lambda: "doubao-seed-2.0-mini")

    monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                        lambda: _FakeStreamer(with_usage=False))
    proxy_inst = proxy_mod._LLMProxy()
    bound = proxy_inst.astream

    async def _consume():
        async for _ in bound():
            pass

    asyncio.run(_consume())
    assert incs == [1]
    # 无 usage 时清空 meta（不残留上一次调用数据）
    assert proxy_mod._last_call_meta_var.get() == {}


def test_astream_with_usage_chunk_records(monkeypatch):
    """对照：有 usage chunk 时正常记录、不打缺失点。"""
    incs = _inc_counter(monkeypatch)
    _reserve_noop(monkeypatch)
    monkeypatch.setattr(proxy_mod, "_enforce_rate_limit", lambda uid: None)
    monkeypatch.setattr(proxy_mod, "_preflight_context",
                        lambda args, kwargs: args)
    monkeypatch.setattr(proxy_mod, "get_active_model_name",
                        lambda: "doubao-seed-2.0-mini")

    monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                        lambda: _FakeStreamer(with_usage=True))
    proxy_inst = proxy_mod._LLMProxy()
    bound = proxy_inst.astream

    async def _consume():
        # 断言须在同一 asyncio context 内：ContextVar 在 task 子上下文 set，
        # 主 context 读不到（行为正确，隔离是特性）
        async for _ in bound():
            pass
        assert proxy_mod._last_tokens_var.get()["total_tokens"] == 10

    asyncio.run(_consume())
    assert incs == []


def test_sync_stream_without_usage_chunk_increments_missing(monkeypatch):
    incs = _inc_counter(monkeypatch)
    _reserve_noop(monkeypatch)
    monkeypatch.setattr(proxy_mod, "_enforce_rate_limit", lambda uid: None)
    monkeypatch.setattr(proxy_mod, "_preflight_context",
                        lambda args, kwargs: args)
    monkeypatch.setattr(proxy_mod, "get_active_model_name",
                        lambda: "doubao-seed-2.0-mini")

    monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                        lambda: _FakeStreamer(with_usage=False))
    proxy_inst = proxy_mod._LLMProxy()
    for _ in proxy_inst.stream():
        pass
    assert incs == [1]
