"""tests/tool_runtime/test_execution_deadline_propagation.py — 工具执行时限跨层传播

2026-10-28 P1（父子时限不一致）回归基线。此前事实：父 rag.search 工具 45s
超时后，rag-service 内部 LLM 仍按 120s 读取超时继续生成（122s 才失败），
SDK 重试再放大总耗时。

契约：
  1. scoped_execution_timeout 以 ContextVar 表达"绝对到期时刻"：嵌套只收窄
     不放宽，退出恢复外层；同步线程经 to_thread/copy_context 继承剩余时限；
  2. provider_deadline_kwargs 仅识别 langchain_openai/langchain_anthropic
     驱动；已有更短超时只收窄不放宽；过期直接抛 TimeoutError；
  3. LLM 代理每次尝试（invoke/ainvoke/流式/备用模型）前按剩余时限收窄，
     过期不再发起出站请求；
  4. rag client ask 在时限内发送 X-Agent-Deadline-Seconds 并封顶读超时；
     rag-server /ask 校验头值并以本地单调钟还原为执行作用域。
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.core.tool_runtime import deadline as dl
from backend.core.tool_runtime.deadline import (
    check_execution_deadline,
    provider_deadline_kwargs,
    remaining_execution_seconds,
    scoped_execution_timeout,
)


# ── 1. 作用域与剩余时限 ──

def test_remaining_none_outside_scope() -> None:
    assert remaining_execution_seconds() is None


def test_scope_sets_and_restores() -> None:
    assert remaining_execution_seconds() is None
    with scoped_execution_timeout(10.0):
        remaining = remaining_execution_seconds()
        assert remaining is not None and 0 < remaining <= 10.0
    assert remaining_execution_seconds() is None


def test_nested_scope_only_narrows() -> None:
    with scoped_execution_timeout(10.0):
        with scoped_execution_timeout(3.0):
            assert remaining_execution_seconds() <= 3.0
        # 内层退出后恢复外层，不会把外层放宽
        assert 3.0 < remaining_execution_seconds() <= 10.0
        with scoped_execution_timeout(50.0):
            # 更长的内层不得放宽外层时限
            assert remaining_execution_seconds() <= 10.0


def test_expired_scope_raises_on_enter() -> None:
    with pytest.raises(TimeoutError):
        with scoped_execution_timeout(0.0):
            pass


def test_sync_thread_inherits_deadline_via_to_thread() -> None:
    seen: dict[str, float | None] = {}

    def sync_tool() -> None:
        seen["remaining"] = remaining_execution_seconds()
        seen["thread"] = threading.get_ident()

    async def scenario() -> None:
        seen["loop_thread"] = threading.get_ident()
        with scoped_execution_timeout(8.0):
            await asyncio.to_thread(sync_tool)

    asyncio.run(scenario())
    assert seen["thread"] != seen["loop_thread"]
    assert seen["remaining"] is not None and 0 < seen["remaining"] <= 8.0


def test_executor_sync_tool_observes_deadline() -> None:
    from backend.core.tool_runtime.executor import safe_tool_executor
    from backend.core.tool_runtime.models import ToolStatus
    from backend.core.tool_runtime.policy import ToolPolicy

    seen: dict[str, float | None] = {}

    def sync_tool() -> dict:
        seen["remaining"] = remaining_execution_seconds()
        return {"ok": True}

    async def scenario() -> object:
        return await safe_tool_executor.run(
            tool_key="test.deadline_scope",
            call=sync_tool,
            policy=ToolPolicy(
                circuit_breaker=False, retries=0, bulkhead_wait_ms=0,
                timeout_ms=5000,
            ),
        )

    result = asyncio.run(scenario())
    assert result.status is ToolStatus.SUCCESS
    assert seen["remaining"] is not None and 0 < seen["remaining"] <= 5.0


# ── 2. provider_deadline_kwargs ──

class _FakeOpenAIModel:
    __module__ = "langchain_openai.chat_models.base"
    request_timeout = 120.0

    def invoke(self, *args, **kwargs):  # 供 bound method 取 __self__
        return "ok"


class _FakeAnthropicModel:
    __module__ = "langchain_anthropic.chat_models"
    default_request_timeout = 15.0

    def invoke(self, *args, **kwargs):
        return "ok"


class _FakeUnknownModel:
    __module__ = "backend.tests.some_other_driver"

    def invoke(self, *args, **kwargs):
        return "ok"


def test_provider_kwargs_noop_without_scope() -> None:
    call = _FakeOpenAIModel().invoke
    assert provider_deadline_kwargs(call, {"a": 1}) == {"a": 1}


def test_provider_kwargs_caps_long_timeout() -> None:
    call = _FakeOpenAIModel().invoke
    with scoped_execution_timeout(30.0):
        capped = provider_deadline_kwargs(call, {})
    assert capped["timeout"] == pytest.approx(30.0, abs=0.5)


def test_provider_kwargs_never_loosens_existing() -> None:
    call = _FakeOpenAIModel().invoke
    with scoped_execution_timeout(200.0):
        capped = provider_deadline_kwargs(call, {})
    assert capped["timeout"] == 120.0


def test_provider_kwargs_respects_caller_explicit_timeout() -> None:
    call = _FakeOpenAIModel().invoke
    with scoped_execution_timeout(30.0):
        capped = provider_deadline_kwargs(call, {"timeout": 10.0})
    assert capped["timeout"] == 10.0


def test_provider_kwargs_anthropic_field_not_loosened() -> None:
    """回归：ChatAnthropic 超时字段是 default_request_timeout，
    漏查会把更短的既有超时放宽成剩余时限。"""
    call = _FakeAnthropicModel().invoke
    with scoped_execution_timeout(30.0):
        capped = provider_deadline_kwargs(call, {})
    assert capped["timeout"] == 15.0


def test_provider_kwargs_httpx_timeout_all_components_capped() -> None:
    class _Model(_FakeOpenAIModel):
        request_timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)

    call = _Model().invoke
    with scoped_execution_timeout(30.0):
        capped = provider_deadline_kwargs(call, {})
    value = capped["timeout"]
    assert isinstance(value, httpx.Timeout)
    components = value.as_dict()
    assert all(v is not None and v <= 30.0 for v in components.values())
    assert components["read"] == pytest.approx(30.0, abs=0.5)


def test_provider_kwargs_unknown_driver_untouched() -> None:
    call = _FakeUnknownModel().invoke
    with scoped_execution_timeout(30.0):
        assert provider_deadline_kwargs(call, {"x": 1}) == {"x": 1}


def test_provider_kwargs_raises_after_expiry() -> None:
    call = _FakeOpenAIModel().invoke
    token = dl._execution_deadline.set(time.monotonic() - 0.01)
    try:
        with pytest.raises(TimeoutError):
            provider_deadline_kwargs(call, {})
    finally:
        dl._execution_deadline.reset(token)


def test_check_deadline_noop_without_scope() -> None:
    check_execution_deadline()  # 不抛


# ── 3. LLM 代理：每次尝试前收窄，过期不出站 ──

class _RecordingBreaker:
    def __init__(self) -> None:
        self.sync_calls: list[dict] = []
        self.async_calls: list[dict] = []

    def call(self, fn, *args, **kwargs):
        self.sync_calls.append(kwargs)
        return "sync-result"

    async def acall(self, fn, *args, **kwargs):
        self.async_calls.append(kwargs)
        return "async-result"


@pytest.fixture()
def resilience_env(monkeypatch):
    """打桩预算/熔断/身份，直接驱动真实 _call_with_resilience 循环。"""
    from backend.infra.llm import budget as budget_mod
    from backend.infra.llm import proxy as proxy_mod

    breaker = _RecordingBreaker()
    reserve_calls: list[tuple] = []

    monkeypatch.setattr(proxy_mod, "_resilience_call_identity",
                        lambda: ("test-model", "main", "openai"))
    monkeypatch.setattr(proxy_mod, "_breaker_for_resolved_model",
                        lambda *a, **k: breaker)
    monkeypatch.setattr(
        budget_mod, "reserve_model_call",
        lambda *a, **k: reserve_calls.append(k),
    )
    monkeypatch.setattr(budget_mod, "release_model_reservation", lambda *a, **k: None)
    monkeypatch.setattr(budget_mod, "review_model_reservation", lambda *a, **k: None)
    monkeypatch.setattr(budget_mod, "current_reservation_id", lambda: "")
    return breaker, reserve_calls


def test_proxy_sync_attempt_caps_kwargs(resilience_env) -> None:
    from backend.infra.llm.proxy import _call_with_resilience

    breaker, _ = resilience_env
    call = _FakeOpenAIModel().invoke
    with scoped_execution_timeout(30.0):
        result = _call_with_resilience(call, "msgs")
    assert result == "sync-result"
    timeout = breaker.sync_calls[0].get("timeout")
    assert isinstance(timeout, float) and 0 < timeout <= 30.0


def test_proxy_sync_expired_skips_reserve_and_provider(resilience_env) -> None:
    from backend.infra.llm.proxy import _call_with_resilience

    breaker, reserve_calls = resilience_env
    call = _FakeOpenAIModel().invoke
    token = dl._execution_deadline.set(time.monotonic() - 0.01)
    try:
        with pytest.raises(TimeoutError):
            _call_with_resilience(call, "msgs")
    finally:
        dl._execution_deadline.reset(token)
    assert breaker.sync_calls == []
    assert reserve_calls == []


def test_proxy_async_attempt_caps_kwargs(resilience_env) -> None:
    from backend.infra.llm.proxy import _acall_with_resilience

    breaker, _ = resilience_env
    call = _FakeOpenAIModel().invoke
    with scoped_execution_timeout(30.0):
        result = asyncio.run(_acall_with_resilience(call, "msgs"))
    assert result == "async-result"
    timeout = breaker.async_calls[0].get("timeout")
    assert isinstance(timeout, float) and 0 < timeout <= 30.0


# ── 4. LLM 代理流式包装：调用点收窄 + chunk 边界检查 ──

class _FakeStreamModel:
    __module__ = "langchain_openai.chat_models.base"
    request_timeout = 120.0

    def __init__(self, chunks: list[str], delay: float = 0.0) -> None:
        self._chunks = chunks
        self._delay = delay
        self.stream_kwargs: list[dict] = []
        self.astream_kwargs: list[dict] = []

    def stream(self, *args, **kwargs):
        self.stream_kwargs.append(kwargs)
        for chunk in self._chunks:
            if self._delay:
                time.sleep(self._delay)
            yield chunk

    async def astream(self, *args, **kwargs):
        self.astream_kwargs.append(kwargs)
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk


@pytest.fixture()
def stream_env(monkeypatch):
    from backend.infra.llm import budget as budget_mod
    from backend.infra.llm import proxy as proxy_mod

    model = _FakeStreamModel(["a", "b"])
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: model)
    monkeypatch.setattr(proxy_mod, "_enforce_rate_limit", lambda user_id: None)
    monkeypatch.setattr(proxy_mod, "_resolve_call_context", lambda: None)
    monkeypatch.setattr(
        proxy_mod, "_preflight_context",
        lambda args, kwargs=None, extra_reserved_tokens=0, call_method="invoke": args,
    )
    monkeypatch.setattr(proxy_mod, "get_active_model_name", lambda: "test-model")
    monkeypatch.setattr(budget_mod, "reserve_model_call", lambda *a, **k: None)
    monkeypatch.setattr(budget_mod, "release_model_reservation", lambda *a, **k: None)
    monkeypatch.setattr(budget_mod, "review_model_reservation", lambda *a, **k: None)
    monkeypatch.setattr(
        proxy_mod, "_settle_estimated_stream_usage", lambda *a, **k: True,
    )
    return model


def test_stream_caps_kwargs_in_scope(stream_env) -> None:
    from backend.infra.llm.proxy import llm

    with scoped_execution_timeout(30.0):
        chunks = list(llm.stream("x"))
    assert chunks == ["a", "b"]
    timeout = stream_env.stream_kwargs[0].get("timeout")
    assert isinstance(timeout, float) and 0 < timeout <= 30.0


def test_stream_noop_without_scope(stream_env) -> None:
    from backend.infra.llm.proxy import llm

    chunks = list(llm.stream("x"))
    assert chunks == ["a", "b"]
    assert "timeout" not in stream_env.stream_kwargs[0]


def test_stream_aborts_after_total_deadline(stream_env) -> None:
    """chunk 边界检查：单次 read 超时 ≠ 总时长上限，超时即中止后续 chunk。"""
    from backend.infra.llm.proxy import llm

    stream_env._chunks = ["a", "b"]
    stream_env._delay = 0.25
    items: list[str] = []
    with pytest.raises(TimeoutError):
        with scoped_execution_timeout(0.3):
            for chunk in llm.stream("x"):
                items.append(chunk)
    assert items == ["a"]


def test_astream_caps_and_aborts_after_total_deadline(stream_env) -> None:
    from backend.infra.llm.proxy import llm

    async def collect() -> tuple[list[str], float | None]:
        got: list[str] = []
        with scoped_execution_timeout(0.3):
            try:
                async for chunk in llm.astream("x"):
                    got.append(chunk)
            except TimeoutError:
                pass
        return got, stream_env.astream_kwargs[0].get("timeout")

    stream_env._delay = 0.25
    got, timeout = asyncio.run(collect())
    assert got == ["a"]
    assert isinstance(timeout, float) and 0 < timeout <= 0.3


# ── 5. rag client：时限头与读超时封顶 ──

def test_deadline_request_parts_without_deadline() -> None:
    from backend.rag.client import _ASK_TIMEOUT_S, _deadline_request_parts

    headers, timeout = _deadline_request_parts(None)
    assert headers == {}
    assert timeout == _ASK_TIMEOUT_S


def test_deadline_request_parts_with_deadline() -> None:
    from backend.rag.client import _deadline_request_parts

    headers, timeout = _deadline_request_parts(45.0)
    value = float(headers["X-Agent-Deadline-Seconds"])
    assert 43.0 <= value <= 44.0  # 扣回传余量 1s
    assert isinstance(timeout, httpx.Timeout)
    components = timeout.as_dict()
    assert components["read"] == pytest.approx(45.0, abs=0.01)
    assert components["connect"] == 5.0  # 长短分离不被放大


def _wire_proxy(captured: dict) -> "object":
    from backend.rag.client import RAGServiceProxy

    def handler(request: httpx.Request) -> httpx.Response:
        captured["deadline"] = request.headers.get("X-Agent-Deadline-Seconds")
        return httpx.Response(200, json={"answer": "ok", "meta": {}})

    return RAGServiceProxy(
        base_url="http://test-rag:8090",
        transport=httpx.MockTransport(handler),
    )


def test_ask_sends_deadline_header_over_wire() -> None:
    captured: dict = {}
    proxy = _wire_proxy(captured)
    with scoped_execution_timeout(45.0):
        answer = proxy.ask("问题", user_id="u1", tenant_id="default")
    assert answer == "ok"
    value = float(captured["deadline"])
    assert 43.0 <= value <= 44.0


def test_ask_without_scope_sends_no_deadline_header() -> None:
    captured: dict = {}
    proxy = _wire_proxy(captured)
    proxy.ask("问题", user_id="u1", tenant_id="default")
    assert captured["deadline"] is None


# ── 6. rag-server：头校验与本地时限还原 ──

def test_parse_parent_deadline_seconds() -> None:
    from backend.services.rag_server import parse_parent_deadline_seconds

    assert parse_parent_deadline_seconds(None) is None
    assert parse_parent_deadline_seconds("") is None
    assert parse_parent_deadline_seconds("abc") is None
    assert parse_parent_deadline_seconds("nan") is None
    assert parse_parent_deadline_seconds("inf") is None
    assert parse_parent_deadline_seconds("-5") == 0.0
    assert parse_parent_deadline_seconds("99999") == 3600.0
    assert parse_parent_deadline_seconds("44.5") == pytest.approx(44.5)


def test_ask_deadline_usable_deducts_elapsed_and_margin() -> None:
    from backend.services.rag_server import ask_deadline_usable

    received = time.perf_counter()
    fake_request = SimpleNamespace(
        headers={"X-Agent-Deadline-Seconds": "45.0"},
        state=SimpleNamespace(rag_received_at=received),
    )
    time.sleep(0.05)
    usable = ask_deadline_usable(fake_request, received)
    # 45 − elapsed(≈0.05) − 0.5 服务端余量
    assert 43.0 < usable < 44.6


def test_ask_deadline_usable_without_header_or_request() -> None:
    from backend.services.rag_server import ask_deadline_usable

    assert ask_deadline_usable(None, time.perf_counter()) is None
    fake_request = SimpleNamespace(headers={}, state=SimpleNamespace())
    assert ask_deadline_usable(fake_request, time.perf_counter()) is None


def test_ask_binds_local_deadline_scope(monkeypatch) -> None:
    """带时限头的 /ask 在 pipeline.ask 执行期间必须处于本地执行作用域内；
    无头请求保持旧行为（无作用域）。"""
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from backend.services import rag_server as m

    monkeypatch.setattr(m, "_internal_token", lambda: "sekrit")
    monkeypatch.setattr(
        "backend.prompts.hot_reload.ensure_prompt_snapshot_fresh_async", AsyncMock(),
    )
    seen: dict[str, float | None] = {}

    def _stub_ask(*args, **kwargs):
        seen["remaining"] = remaining_execution_seconds()
        return "测试答案"

    monkeypatch.setattr(m, "_get_pipeline", lambda: SimpleNamespace(
        ask=_stub_ask, last_answer_meta={},
    ))
    client = TestClient(m.app, raise_server_exceptions=False)

    resp = client.post(
        "/ask",
        json={"question": "测试问题"},
        headers={
            "X-Internal-Token": "sekrit",
            "X-Agent-Deadline-Seconds": "30.0",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "测试答案"
    assert seen["remaining"] is not None
    assert 0 < seen["remaining"] <= 30.0

    resp2 = client.post(
        "/ask",
        json={"question": "测试问题"},
        headers={"X-Internal-Token": "sekrit"},
    )
    assert resp2.status_code == 200
    assert seen["remaining"] is None
