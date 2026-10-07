"""Model Governance STOP C —— Streaming usage（C13）+ 估算兜底（2026-10-02）。

provider 不回 usage chunk / 客户端提前中断时：
- 估算兜底开启（默认）：按本地字符统计估算结算，cost_status='estimated'，
  预占正常结算、不进待对账；
- 估算兜底关闭 / 无法估算（零输出且无 prompt）：维持原路径——显式打点 +
  转待对账（needs_review），禁止静默丢量。
astream/stream 两条包装路径都覆盖。
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

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
    """底层模型：astream/stream 产 3 个内容 chunk、无 usage chunk。"""

    def __init__(self, with_usage=False, text="chunk{}"):
        self._with_usage = with_usage
        self._text = text

    async def astream(self, *args, **kwargs):
        for i in range(3):
            usage = {"total_tokens": 10} if (
                self._with_usage and i == 2) else None
            yield _Chunk(text=self._text.format(i), usage_metadata=usage)

    def stream(self, *args, **kwargs):
        for i in range(3):
            usage = {"total_tokens": 10} if (
                self._with_usage and i == 2) else None
            yield _Chunk(text=self._text.format(i), usage_metadata=usage)


def _inc_counters(monkeypatch) -> dict[str, list]:
    incs: dict[str, list] = {"missing": [], "estimated": []}

    class _FakeCounter:
        def __init__(self, key):
            self._key = key

        def inc(self, n=1):
            incs[self._key].append(n)

    import backend.observability.metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "llm_usage_missing_total",
                        _FakeCounter("missing"), raising=False)
    monkeypatch.setattr(metrics_mod, "llm_usage_estimated_total",
                        _FakeCounter("estimated"), raising=False)
    return incs


def _pricing_stub(monkeypatch) -> list:
    """定价打桩：返回确定 BillingResult，隔离测试环境的价格库。

    2026-10-07 Billing 收口：proxy 结算路径已切换到唯一入口 price_usage，
    打桩点同步迁移（旧 calculate_llm_cost_with_status 不再被调用）。
    """
    calls = []

    def _fake(model, component, usage, *, enforce, usage_source="provider"):
        calls.append((model, usage))
        from backend.infra.llm.pricing import BillingResult

        return BillingResult(
            model_name=model, component=component,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            native_cost=Decimal("0.002"), native_currency="USD",
            billed_cost_cny=Decimal("0.0144"), fx_rate=Decimal("7.20"),
            input_cost_cny=Decimal("0.0072"), output_cost_cny=Decimal("0.0072"),
            cost_status="estimated", usage_source=usage_source,
            pricing_source="registry_fallback",
        )

    import backend.infra.llm.pricing as pricing_mod
    monkeypatch.setattr(pricing_mod, "price_usage", _fake)
    return calls


def _reserve_noop(monkeypatch) -> list:
    calls = []
    import backend.infra.llm.budget as budget_mod

    monkeypatch.setattr(budget_mod, "reserve_model_call",
                        lambda *a, **k: calls.append(("reserve", k)))
    monkeypatch.setattr(budget_mod, "release_model_reservation",
                        lambda: calls.append(("release",)))
    monkeypatch.setattr(budget_mod, "record_model_usage",
                        lambda **kw: calls.append(("record", kw)))
    return calls


def _enable_estimation(monkeypatch, enabled: bool) -> None:
    import backend.config.llm as llm_config
    monkeypatch.setattr(llm_config, "LLM_USAGE_ESTIMATION_ENABLED", enabled)


def test_astream_without_usage_chunk_settles_estimated(monkeypatch):
    """缺 usage + 有输出：按估算结算（estimated），不进待对账。"""
    incs = _inc_counters(monkeypatch)
    calls = _reserve_noop(monkeypatch)
    _pricing_stub(monkeypatch)
    _enable_estimation(monkeypatch, True)
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

    async def _consume():
        async for _ in bound():
            pass
        # 断言须在同一 asyncio context 内：ContextVar 在 task 子上下文 set，
        # 主 context 读不到（与 with-usage 对照测试同款约束）
        assert calls[-1] == ("record", {
            "prompt_tokens": 0, "completion_tokens": 5, "total_tokens": 5,
            "cost": Decimal("0.0144"),  # Billing V2：预算结算收 billed_cost_cny
        })
        meta = proxy_mod._last_call_meta_var.get()
        assert meta["cost_status"] == "estimated"
        assert meta["binding_source"] == "estimated"
        assert meta["finish_reason"] == "estimated_no_usage"
        assert meta["total_tokens"] == 5
        assert meta["cost_cny"] == 0.0144
        assert meta["cost_usd"] == 0.002  # 原生 USD 审计口径

    asyncio.run(_consume())
    # 缺失事件照常计数 + 估算结算计数（计数器是普通对象，跨上下文可见）
    assert incs["missing"] == [1]
    assert incs["estimated"] == [1]


def test_astream_without_usage_falls_back_when_estimation_disabled(monkeypatch):
    """估算开关关闭：维持原 needs_review 路径（meta 清空 + 缺失计数）。"""
    incs = _inc_counters(monkeypatch)
    _reserve_noop(monkeypatch)
    _enable_estimation(monkeypatch, False)
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
    assert incs["missing"] == [1]
    assert incs["estimated"] == []
    # 无 usage 且未估算时清空 meta（不残留上一次调用数据）
    assert proxy_mod._last_call_meta_var.get() == {}


def test_astream_zero_output_falls_back_to_needs_review(monkeypatch):
    """零输出且无 prompt 入参：无法估算 → 回落待对账路径。"""
    incs = _inc_counters(monkeypatch)
    _reserve_noop(monkeypatch)
    _enable_estimation(monkeypatch, True)
    monkeypatch.setattr(proxy_mod, "_enforce_rate_limit", lambda uid: None)
    monkeypatch.setattr(proxy_mod, "_preflight_context",
                        lambda args, kwargs: args)
    monkeypatch.setattr(proxy_mod, "get_active_model_name",
                        lambda: "doubao-seed-2.0-mini")

    monkeypatch.setattr(
        proxy_mod, "_resolve_active_llm",
        lambda: _FakeStreamer(with_usage=False, text=""),
    )
    proxy_inst = proxy_mod._LLMProxy()
    bound = proxy_inst.astream

    async def _consume():
        async for _ in bound():
            pass

    asyncio.run(_consume())
    assert incs["estimated"] == []
    assert proxy_mod._last_call_meta_var.get() == {}


def test_astream_with_usage_chunk_records(monkeypatch):
    """对照：有 usage chunk 时正常记录、不打缺失点。"""
    incs = _inc_counters(monkeypatch)
    _reserve_noop(monkeypatch)
    _enable_estimation(monkeypatch, True)
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
    assert incs["missing"] == []
    assert incs["estimated"] == []


def test_sync_stream_without_usage_chunk_settles_estimated(monkeypatch):
    incs = _inc_counters(monkeypatch)
    calls = _reserve_noop(monkeypatch)
    _pricing_stub(monkeypatch)
    _enable_estimation(monkeypatch, True)
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
    assert incs["missing"] == [1]
    assert incs["estimated"] == [1]
    assert calls[-1][0] == "record"
    assert calls[-1][1]["total_tokens"] == 5
    assert proxy_mod._last_call_meta_var.get()["cost_status"] == "estimated"
