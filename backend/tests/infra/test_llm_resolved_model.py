# -*- coding: utf-8 -*-
"""test_llm_resolved_model.py — P1 模型归属链路单测（2026-09-22）

覆盖（用户验收规格）：
1. stream chunk 无 model metadata → usage 从调用上下文 ResolvedModelContext
   取真实模型（不再回退 import 期常量 LLM_MODEL）
2. 请求覆盖 / 模拟 DB 动态切模 → 新请求立即记录新模型
3. main 与 tool_selector 不同模型 → usage 分开归属、不串模型
4. price_unknown → usage 仍记录真实 model_id，cost_status 正确
5. 并发请求 → ContextVar 隔离，模型归属不串线
6. fallback 接管 → 用量记到 fallback 模型
全部离线（桩 LLM / 桩解析，不碰真实供应商）。
"""
import threading

import pytest

from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.resolved_model import (
    get_current_resolved_model,
    reset_current_resolved_model,
)


# ── 桩件 ──────────────────────────────────────────────────────

class _Chunk:
    """最小 AIMessageChunk 替身：带 usage 但无 response_metadata（流式常态）。"""

    def __init__(self, content: str, usage_metadata: dict | None = None):
        self.content = content
        self.usage_metadata = usage_metadata
        self.response_metadata = {}


class _FakeStreamLLM:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, *args, **kwargs):
        for c in self._chunks:
            yield c


class _FakeInvokeLLM:
    """invoke/stream 双通道替身：返回带 usage 但无 response_metadata 的消息。"""

    def __init__(self):
        self.calls = 0

    def _msg(self):
        return _Chunk("回答内容", usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
        })

    def invoke(self, *args, **kwargs):
        self.calls += 1
        return self._msg()

    def stream(self, *args, **kwargs):
        self.calls += 1
        yield self._msg()


_USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


@pytest.fixture(autouse=True)
def _isolate_context(monkeypatch):
    """每用例隔离：清模型上下文 / 请求覆盖 / turn 用量。"""
    reset_current_resolved_model()
    proxy_mod._request_model_var.set("")
    proxy_mod.reset_turn_usage()
    proxy_mod._last_call_meta_var.set({})
    proxy_mod._last_tokens_var.set({})
    yield
    reset_current_resolved_model()
    proxy_mod._request_model_var.set("")
    proxy_mod.reset_turn_usage()
    proxy_mod._last_call_meta_var.set({})
    proxy_mod._last_tokens_var.set({})


def _patch_resolution(monkeypatch, model: str, provider: str = "fake", target=None):
    """把模型解析桩到指定模型（含 provider 解析）；target 缺省用 invoke 替身。"""
    monkeypatch.setattr(proxy_mod, "get_active_model_name", lambda: model)
    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda m: provider)
    monkeypatch.setattr(proxy_mod, "_resolve_active_llm",
                        lambda: target if target is not None else _FakeInvokeLLM())


# ── 1/2. stream / invoke 归属 + DB 动态切模 ────────────────────

class TestStreamAttribution:
    def test_stream_usage_uses_call_context_not_constant(self, monkeypatch):
        """chunk 无 metadata：usage 记到解析出的 model-b，而非 LLM_MODEL 常量。"""
        _patch_resolution(monkeypatch, "model-b", target=_FakeStreamLLM([
            _Chunk("你", usage_metadata=_USAGE),
            _Chunk("好"),
        ]))

        chunks = list(proxy_mod.llm.stream("问题"))

        assert [c.content for c in chunks] == ["你", "好"]
        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "model-b"
        assert meta["total_tokens"] == 15
        # 上下文登记可查
        ctx = get_current_resolved_model()
        assert ctx is not None and ctx.model_id == "model-b"
        # 整轮用量按 model-b 累加（trace finish 消费口径）
        assert "model-b" in proxy_mod.get_turn_usage()
        assert proxy_mod.LLM_MODEL not in proxy_mod.get_turn_usage() or \
            proxy_mod.LLM_MODEL == "model-b"

    def test_request_override_wins(self, monkeypatch):
        """请求级模型覆盖 → usage 记覆盖模型，binding_source=request_override。"""
        _patch_resolution(monkeypatch, "default-model")
        proxy_mod._request_model_var.set("override-model")

        list(proxy_mod.llm.stream("q"))

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "override-model"
        assert meta["binding_source"] == "request_override"
        assert meta["model_role"] == "main"

    def test_db_dynamic_switch_immediate(self, monkeypatch):
        """模拟 DB 绑定切模：不重启，下一次请求立即记录新模型。"""
        monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda m: "fake")
        monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: _FakeInvokeLLM())
        current = {"m": "model-a"}
        monkeypatch.setattr(proxy_mod, "get_active_model_name",
                            lambda: current["m"])
        monkeypatch.setattr(proxy_mod, "_resolve_call_context",
                            lambda role="main", model_name=None:
                            proxy_mod.ResolvedModelContext(
                                model_id=current["m"],
                                provider="fake", role=role,
                                binding_source="db_binding"))

        list(proxy_mod.llm.stream("第一问"))
        assert proxy_mod._last_call_meta_var.get()["model"] == "model-a"
        assert proxy_mod._last_call_meta_var.get()["binding_source"] == "db_binding"

        current["m"] = "model-b"  # DB 切模：下一次请求立即生效
        list(proxy_mod.llm.stream("第二问"))
        assert proxy_mod._last_call_meta_var.get()["model"] == "model-b"


# ── 3. main 与 tool_selector 分开归属 ─────────────────────────

class TestSelectorAttribution:
    def test_bound_proxy_records_selector_model(self, monkeypatch):
        """tool_selector 专用模型：usage 记专用模型，role/binding_source 正确。"""
        _patch_resolution(monkeypatch, "main-model")
        fake = _FakeInvokeLLM()
        bound = proxy_mod._BoundLLMProxy(
            fake, model_name="selector-model", role="tool_selector")

        bound.invoke("选工具")

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "selector-model"
        assert meta["model_role"] == "tool_selector"
        assert meta["binding_source"] in ("db_binding", "env_default", "code_default")
        # 整轮用量里 main 与 selector 各归各类
        turn = proxy_mod.get_turn_usage()
        assert "selector-model" in turn
        assert "main-model" not in turn

    def test_main_and_selector_do_not_mix(self, monkeypatch):
        """同一轮先 main 后 selector：两个模型的用量分行累计、互不覆盖。"""
        _patch_resolution(monkeypatch, "main-model")
        proxy_mod.llm.invoke("主问题")

        bound = proxy_mod._BoundLLMProxy(
            _FakeInvokeLLM(), model_name="selector-model", role="tool_selector")
        bound.invoke("选工具")

        turn = proxy_mod.get_turn_usage()
        assert set(turn.keys()) == {"main-model", "selector-model"}
        assert turn["main-model"]["calls"] == 1
        assert turn["selector-model"]["calls"] == 1


# ── 4. price_unknown ─────────────────────────────────────────

class TestPriceUnknown:
    def test_usage_still_records_real_model(self, monkeypatch):
        """缺价：usage 照记真实 model_id，cost_status 透传（price_unknown）。"""
        _patch_resolution(monkeypatch, "unpriced-model")
        import decimal

        import backend.infra.llm.pricing as pricing_mod
        from backend.infra.llm.pricing import BillingResult

        def _fake_price(model, component, usage, *, enforce, usage_source="provider"):
            assert model == "unpriced-model"  # 计价收到的就是真实模型
            return BillingResult(
                model_name=model, component=component,
                native_cost=decimal.Decimal("0.001"), native_currency="USD",
                billed_cost_cny=decimal.Decimal("0.0072"), fx_rate=decimal.Decimal("7.2"),
                cost_status="price_unknown",
                usage_source=usage_source,
                pricing_source=pricing_mod.PRICING_SOURCE_REGISTRY_FALLBACK,
            )

        monkeypatch.setattr(pricing_mod, "price_usage", _fake_price)

        proxy_mod.llm.invoke("问题")

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "unpriced-model"
        assert meta["cost_status"] == "price_unknown"
        assert meta["total_tokens"] == 15


# ── 5. 并发隔离 ───────────────────────────────────────────────

class TestConcurrency:
    def test_contextvar_isolation_across_threads(self, monkeypatch):
        """并发请求：各线程模型归属互不串线（ContextVar 按上下文隔离）。"""
        monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda m: "fake")
        monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: _FakeInvokeLLM())
        results = {}
        barrier = threading.Barrier(2)

        def _worker(name: str, model: str):
            proxy_mod._request_model_var.set(model)
            barrier.wait()
            proxy_mod.llm.invoke("问题")
            results[name] = proxy_mod._last_call_meta_var.get()["model"]

        t1 = threading.Thread(target=_worker, args=("a", "thread-model-a"))
        t2 = threading.Thread(target=_worker, args=("b", "thread-model-b"))
        t1.start(); t2.start(); t1.join(); t2.join()

        assert results["a"] == "thread-model-a"
        assert results["b"] == "thread-model-b"


# ── 6. fallback 接管归属 ──────────────────────────────────────

class TestFallbackAttribution:
    def test_fallback_usage_recorded_under_fallback_model(self, monkeypatch):
        """主模型失败 → fallback 接管：usage 记 fallback 模型。"""
        _patch_resolution(monkeypatch, "primary-model")

        class _PrimaryFails:
            def invoke(self, *args, **kwargs):
                raise RuntimeError("参数错误")  # 非瞬时 → 直接走 fallback

        monkeypatch.setattr(proxy_mod, "_resolve_active_llm", lambda: _PrimaryFails())
        monkeypatch.setattr(proxy_mod, "_get_fallback_llm",
                            lambda: _FakeInvokeLLM())
        monkeypatch.setattr(proxy_mod, "_configured_fallback_model",
                            lambda: "fallback-model")

        proxy_mod.llm.invoke("问题")

        meta = proxy_mod._last_call_meta_var.get()
        assert meta["model"] == "fallback-model"
        assert meta["binding_source"] == "fallback"
