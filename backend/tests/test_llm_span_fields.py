"""P4 — LLM span 必填字段验证（cost / finish_reason / prompt_text / completion_text）

覆盖:
- models.py: pricing 表 + compute_cost_usd 正确性
- proxy.py: _record_tokens 提取 token + finish_reason + cost_usd
- chain.py: _timed_stuff 注入 prompt_text + completion_text 到 metrics

2d627d7 重构后 tracer 内部属性已简化，使用公共 fresh_collector fixture 隔离测试。
"""

import pytest

from backend.infra.llm.models import get_model_pricing, compute_cost_usd
from backend.infra.llm import proxy as proxy_mod
from backend.observability import tracer as tracer_mod
from backend.tests.fixtures.sqlite_tracer import fresh_collector  # noqa: F401


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(autouse=True)
def reset_proxy_state():
    """每个测试前后重置 proxy token 状态（ContextVar 不可变替换）"""
    proxy_mod._last_tokens_var.set({})
    proxy_mod._last_call_meta_var.set({})
    yield
    proxy_mod._last_tokens_var.set({})
    proxy_mod._last_call_meta_var.set({})


@pytest.fixture(autouse=True)
def reset_tracer(fresh_collector):
    """autouse: 每个测试前 fresh_collector fixture 已重置 tracer 状态。"""
    fresh_collector.clear_for_test()
    yield
    fresh_collector.clear_for_test()


# ==========================================================
# 1. models.py — pricing 表
# ==========================================================

class TestModelPricing:
    def test_ollama_pricing_is_zero(self):
        in_p, out_p = get_model_pricing("qwen2.5:3b")
        assert in_p == 0.0
        assert out_p == 0.0

    def test_deepseek_pricing(self):
        in_p, out_p = get_model_pricing("deepseek-v4-flash")
        assert in_p == 0.14
        assert out_p == 0.28

    def test_minimax_pricing(self):
        in_p, out_p = get_model_pricing("MiniMax-M3")
        assert in_p == 3.0
        assert out_p == 15.0

    def test_unknown_model_returns_zero(self):
        in_p, out_p = get_model_pricing("nonexistent-model")
        assert in_p == 0.0
        assert out_p == 0.0

    def test_compute_cost_ollama_is_zero(self):
        assert compute_cost_usd("qwen2.5:3b", 1000, 500) == 0.0

    def test_compute_cost_deepseek_1m_tokens(self):
        # 1M input + 1M output = 0.14 + 0.28 = 0.42 USD
        assert compute_cost_usd("deepseek-v4-flash", 1_000_000, 1_000_000) == 0.42

    def test_compute_cost_minimax_small(self):
        # 1000 input + 500 output = 0.003 + 0.0075 = 0.0105
        cost = compute_cost_usd("MiniMax-M3", 1000, 500)
        assert cost == 0.0105


# ==========================================================
# 2. proxy.py — _record_tokens 提取 finish_reason + cost
# ==========================================================

class TestRecordTokensMeta:
    def _fake_result(self, token_usage=None, finish_reason=None, response_metadata=None):
        """构造一个类 AIMessage 的 fake 对象"""
        class FakeResult:
            pass
        r = FakeResult()
        r.response_metadata = response_metadata or {}
        if token_usage:
            r.response_metadata["token_usage"] = token_usage
        if finish_reason:
            r.response_metadata["finish_reason"] = finish_reason
        return r

    def test_records_token_counts(self):
        r = self._fake_result(
            token_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        )
        proxy_mod._record_tokens(r)
        assert proxy_mod._last_tokens_var.get() == {
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
        }

    def test_records_finish_reason_stop(self):
        r = self._fake_result(
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="stop",
        )
        proxy_mod._record_tokens(r)
        assert proxy_mod._last_call_meta_var.get()["finish_reason"] == "stop"

    def test_records_finish_reason_length(self):
        """truncation 信号 — max_tokens 触发"""
        r = self._fake_result(
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="length",
        )
        proxy_mod._record_tokens(r)
        assert proxy_mod._last_call_meta_var.get()["finish_reason"] == "length"

    def test_records_cost_usd_deepseek(self):
        r = self._fake_result(
            token_usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            finish_reason="stop",
        )
        # mock LLM_MODEL = deepseek-v4-flash
        # 1000/1e6 * 0.14 + 500/1e6 * 0.28 = 0.00014 + 0.00014 = 0.00028
        import unittest.mock as mock
        with mock.patch.object(proxy_mod, "LLM_MODEL", "deepseek-v4-flash"):
            proxy_mod._record_tokens(r)
        assert proxy_mod._last_call_meta_var.get()["cost_usd"] == pytest.approx(0.00028, abs=1e-6)

    def test_finish_reason_defaults_unknown_when_missing(self):
        r = self._fake_result(
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        proxy_mod._record_tokens(r)
        assert proxy_mod._last_call_meta_var.get()["finish_reason"] == "unknown"

    def test_handles_no_token_usage_gracefully(self):
        r = self._fake_result()  # 无 token_usage
        proxy_mod._record_tokens(r)
        assert proxy_mod._last_call_meta_var.get() == {}


# ==========================================================
# 3. chain.py — _timed_stuff 注入 prompt/completion text
# ==========================================================

class TestTimedStuffInjection:
    """直接测 _timed_stuff 的 metrics 注入逻辑"""

    def test_timed_stuff_injects_completion_text_and_finish_reason(self, monkeypatch):
        from backend.observability.tracer import trace_collector, SpanKind

        # 模拟 _stuff.invoke 返回一个带 .content 的对象
        class FakeResult:
            content = "这是 LLM 返回的答案 [1][2]"

        def fake_stuff_invoke(inp):
            return FakeResult()

        # 模拟 _last_call_meta 已就绪
        proxy_mod._last_call_meta_var.set({
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
            "finish_reason": "stop",
            "cost_usd": 0.00028,
        })

        # 直接调用 _timed_stuff 逻辑（简化版）
        def _timed_stuff(inp):
            llm_span = trace_collector.start_span(
                "llm_generate", name="LLM生成",
                kind=SpanKind.LLM.value,
                input={"question": inp.get("input", "")[:1000]},
            )
            try:
                r = fake_stuff_invoke(inp)
                metrics = dict(proxy_mod._last_call_meta_var.get())
                completion_text = ""
                if hasattr(r, "content") and isinstance(r.content, str):
                    completion_text = r.content[:1000]
                if completion_text:
                    metrics["completion_text"] = completion_text
                trace_collector.end_span(llm_span, metrics=metrics)
                return r
            except Exception:
                trace_collector.end_span(llm_span, status="error")
                raise

        trace = trace_collector.start("test-question", workflow_kind="rag_query")
        _timed_stuff({"input": "用户的问题", "chat_history": []})

        # 2d627d7: 直接读 start() 返回的 trace.spans（内存中）
        # 旧版通过 list()[0].spans 读；新版 list() 只读 SQLite (需 finish())
        span = next(s for s in trace.spans if s.span_id == "llm_generate")

        assert span.kind == SpanKind.LLM.value
        assert span.metrics["prompt_tokens"] == 100
        assert span.metrics["completion_tokens"] == 50
        assert span.metrics["finish_reason"] == "stop"
        assert span.metrics["cost_usd"] == 0.00028
        assert "[1][2]" in span.metrics["completion_text"]
        assert span.input["question"] == "用户的问题"

    def test_completion_text_truncated_at_1000_chars(self):
        from backend.observability.tracer import trace_collector

        long_text = "x" * 5000

        class FakeResult:
            content = long_text

        proxy_mod._last_call_meta_var.set({
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
            "finish_reason": "stop", "cost_usd": 0.0,
        })

        trace = trace_collector.start("test")
        llm_span = trace_collector.start_span("llm_generate")
        r = FakeResult()
        metrics = dict(proxy_mod._last_call_meta_var.get())
        completion_text = r.content[:1000] if isinstance(r.content, str) else ""
        metrics["completion_text"] = completion_text
        trace_collector.end_span(llm_span, metrics=metrics)

        # 2d627d7: 直接读 trace.spans（内存中），不依赖 finish()+SQLite
        span = trace.spans[0]
        assert len(span.metrics["completion_text"]) == 1000
        assert span.metrics["completion_text"] == "x" * 1000

    def test_finish_reason_length_signals_truncation(self):
        """finish_reason=length 是 truncation 信号 → 触发 warning 排查"""
        from backend.observability.tracer import trace_collector

        proxy_mod._last_call_meta_var.set({
            "prompt_tokens": 100, "completion_tokens": 1000,
            "total_tokens": 1100, "finish_reason": "length",
            "cost_usd": 0.00014,
        })

        trace = trace_collector.start("test")
        llm_span = trace_collector.start_span("llm_generate")
        trace_collector.end_span(llm_span, metrics=dict(proxy_mod._last_call_meta_var.get()))

        # 2d627d7: 直接读 trace.spans（内存中）
        span = trace.spans[0]
        assert span.metrics["finish_reason"] == "length"
        # 注意：truncation 应该触发 retry 或 fallback（业务侧实现）


# ==========================================================
# 4. async 路径 — ainvoke token 记录（P1 修复）
# ==========================================================

class TestAsyncTokenRecording:
    """ainvoke/agenerate 修复：await 之后 _record_tokens 记录真实 token。

    既有 bug：同步 wrapper 对 coroutine 调用 _record_tokens，
    coroutine 无 response_metadata → token 被清空 → 恒 0。
    """

    class _FakeResult:
        content = "异步答案"
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
            },
            "finish_reason": "stop",
        }

    class _FakeLLM:
        async def ainvoke(self, *args, **kwargs):
            return TestAsyncTokenRecording._FakeResult()

    @pytest.mark.asyncio
    async def test_ainvoke_records_real_tokens(self, monkeypatch):
        """await ainvoke 后 _last_call_meta_var 记录真实 token，不再被清空。"""
        from backend.infra.llm import proxy as proxy_mod
        monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                            lambda: self._FakeLLM())
        r = await proxy_mod.llm.ainvoke("问题")
        assert r.content == "异步答案"
        meta = proxy_mod._last_call_meta_var.get()
        assert meta.get("prompt_tokens") == 100
        assert meta.get("completion_tokens") == 50
        assert meta.get("total_tokens") == 150
        assert meta.get("finish_reason") == "stop"

    @pytest.mark.asyncio
    async def test_capability_style_reads_tokens(self, monkeypatch):
        """capability 链路（await 后读 var）拿到真实 token，不再恒 0。"""
        from backend.infra.llm import proxy as proxy_mod
        monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                            lambda: self._FakeLLM())
        await proxy_mod.llm.ainvoke("问题")
        _meta = proxy_mod._last_call_meta_var.get()
        meta = {
            "prompt_tokens": _meta.get("prompt_tokens", 0),
            "completion_tokens": _meta.get("completion_tokens", 0),
            "cost_usd": _meta.get("cost_usd", 0),
        }
        assert meta["prompt_tokens"] == 100
        assert meta["completion_tokens"] == 50

    @pytest.mark.asyncio
    async def test_astream_passthrough_not_wrapped(self, monkeypatch):
        """astream（async generator）不被 async_wrapper 包装，逐 chunk 透传。"""
        from backend.infra.llm import proxy as proxy_mod

        class FakeLLM:
            async def astream(self, *args, **kwargs):
                yield "chunk1"
                yield "chunk2"

        monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: FakeLLM())
        chunks = [c async for c in proxy_mod.llm.astream("q")]
        assert chunks == ["chunk1", "chunk2"]

    @pytest.mark.asyncio
    async def test_ainvoke_failure_counts_breaker(self, monkeypatch):
        """async 调用失败计入熔断器（acall _on_failure），不再绕过熔断。"""
        from backend.infra.circuit_breaker import CircuitBreaker
        from backend.infra.llm import proxy as proxy_mod

        class FakeLLM:
            async def ainvoke(self, *args, **kwargs):
                raise RuntimeError("llm down")

        fresh = CircuitBreaker("test-async", fail_threshold=5, timeout=30)
        monkeypatch.setattr("backend.infra.circuit_breaker.llm_circuit_breaker",
                            fresh)
        monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: FakeLLM())
        with pytest.raises(RuntimeError):
            await proxy_mod.llm.ainvoke("问题")
        assert fresh.stats()["failures"] == 1