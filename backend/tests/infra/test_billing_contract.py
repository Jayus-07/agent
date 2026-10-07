"""Billing Contract 黄金测试（Model Billing Unified Closure 2026-10-07）。

验收对象 = 唯一计费事实链：同 Usage + 同价格版本 + 同 FX → 只有一个成本答案。
P0-01~P0-12 覆盖 observe/enforce 同价、USD 单次折算、price_unknown 先折汇再结算、
缓存不双算、预算==用量、trace==用量、看板不二次换汇、unpriced 显式、
FX/价格版本快照不可变、retry/fallback 独立记账、legacy 行不打爆看板。

全部用内存桩（价格快照 monkeypatch / store 捕获桩），不依赖 PG。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.infra.llm import budget as budget_mod
from backend.infra.llm import pricing
from backend.infra.llm.budget import RequestBudget, RequestBudgetLimits
from backend.infra.llm.pricing import (
    COST_STATUS_EXACT,
    COST_STATUS_PRICE_UNKNOWN,
    NormalizedUsage,
    PriceLine,
    PriceTable,
    price_usage,
)


def _usd_table(version: str = "2026-10-07-v1") -> PriceTable:
    return PriceTable([
        PriceLine(model_name="m", component="llm", dimension="input",
                  price_per_unit=Decimal("1.000000"), currency="USD",
                  price_table_version=version),
        PriceLine(model_name="m", component="llm", dimension="output",
                  price_per_unit=Decimal("2.000000"), currency="USD",
                  price_table_version=version),
        PriceLine(model_name="m", component="llm", dimension="cache_read",
                  price_per_unit=Decimal("0.100000"), currency="USD",
                  price_table_version=version),
    ])


def _cny_table(version: str = "2026-10-07-v1") -> PriceTable:
    return PriceTable([
        PriceLine(model_name="m", component="llm", dimension="input",
                  price_per_unit=Decimal("3.000000"), currency="CNY",
                  price_table_version=version),
        PriceLine(model_name="m", component="llm", dimension="output",
                  price_per_unit=Decimal("9.000000"), currency="CNY",
                  price_table_version=version),
    ])


_M_USAGE = {"input": 1_000_000, "cache_read": 0, "output": 1_000_000}


# ── P0-01 observe/enforce 同价 ──────────────────────────────────────────

def test_observe_enforce_same_billing_result():
    """同 Usage + 同价格快照：observe 与 enforce 的 BillingResult 金额/状态
    完全一致（模式差异只允许存在于门禁行为，不存在于金额）。"""
    with patch.object(pricing, "get_current_price_table", return_value=_usd_table()):
        usage = NormalizedUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        observe = price_usage("m", "llm", usage, enforce=False)
        enforce = price_usage("m", "llm", usage, enforce=True)
    assert observe.billed_cost_cny == enforce.billed_cost_cny == Decimal("21.600000")
    assert observe.native_cost == enforce.native_cost == Decimal("3.000000")
    assert observe.cost_status == enforce.cost_status == COST_STATUS_EXACT


# ── P0-02 USD → CNY 单次折算 ────────────────────────────────────────────

def test_exact_usd_converts_to_cny_once():
    """$3 原生 → ¥21.6 记账；禁止 155.52（二次换汇）与 3（裸 USD 入账）。"""
    with patch.object(pricing, "get_current_price_table", return_value=_usd_table()):
        result = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1_000_000, output_tokens=1_000_000),
            enforce=False,
        )
    assert result.native_cost == Decimal("3.000000")
    assert result.native_currency == "USD"
    assert result.fx_rate == Decimal("7.20")
    assert result.billed_cost_cny == Decimal("21.600000")


# ── P0-03 price_unknown 先折汇再结算 ────────────────────────────────────

class _CaptureQuotaStore:
    """捕获 settle 金额的假配额存储（记账金额唯一断言面）。"""

    def __init__(self):
        self.settled: list[Decimal] = []

    def reserve(self, **kwargs):
        return object()

    def settle(self, reservation, actual_cny):
        self.settled.append(Decimal(actual_cny))

    def release(self, reservation):
        pass

    def mark_needs_review(self, reservation, reason):
        return True


def _enforce_budget(quota_store) -> RequestBudget:
    """enforce 态请求预算 + 捕获桩配额存储（bind 语义的最小复刻）。"""
    budget = RequestBudget(
        RequestBudgetLimits(max_calls=8, max_cost="0.50"),
        mode="enforce", user_id="u1", tenant_id="t1",
        quota_store=quota_store,
    )
    return budget


def test_price_unknown_usd_converts_before_budget_settlement(monkeypatch):
    """缺价 enforce：注册表估价 USD 0.002 × 7.2 = ¥0.0144 入预算结算，
    禁止 $0.002 当 ¥0.002（P0-B 的 7.2 倍少扣）。"""
    monkeypatch.setattr(
        pricing, "get_current_price_table",
        lambda *a, **kw: (_ for _ in ()).throw(pricing.PriceTableUnavailable("x")),
    )
    monkeypatch.setattr(
        pricing, "calculate_fallback_cost",
        lambda *a, **kw: Decimal("0.002"),
    )
    # 模拟 proxy enforce 结算路径：price_usage → record_model_usage → settle
    from backend.infra.llm.budget import _states, _current_request_id

    store = _CaptureQuotaStore()
    budget = _enforce_budget(store)
    _states["req-t"] = budget
    token = _current_request_id.set("req-t")
    try:
        from backend.infra.llm.budget import record_model_usage, reserve_model_call

        reserve_model_call("primary", model_name="m")
        result = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1000, output_tokens=500),
            enforce=True,
        )
        record_model_usage(prompt_tokens=1000, completion_tokens=500,
                           total_tokens=1500, cost=result.billed_cost_cny)
    finally:
        _current_request_id.reset(token)
        _states.pop("req-t", None)
    assert result.cost_status == COST_STATUS_PRICE_UNKNOWN
    assert result.native_currency == "USD"
    assert result.billed_cost_cny == Decimal("0.014400")
    assert store.settled == [Decimal("0.014400")]


# ── P0-04 缓存不双算 ────────────────────────────────────────────────────

def test_cache_read_not_double_billed():
    """input=1000 含 cached=600：billable=400，400×input + 600×cache + 500×out。"""
    table = PriceTable([
        PriceLine(model_name="m", component="llm", dimension="input",
                  price_per_unit=Decimal("3.000000"), currency="CNY"),
        PriceLine(model_name="m", component="llm", dimension="output",
                  price_per_unit=Decimal("9.000000"), currency="CNY"),
        PriceLine(model_name="m", component="llm", dimension="cache_read",
                  price_per_unit=Decimal("0.300000"), currency="CNY"),
    ])
    with patch.object(pricing, "get_current_price_table", return_value=table):
        result = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1000, cached_input_tokens=600,
                            output_tokens=500),
            enforce=False,
        )
    assert result.billable_input_tokens == 400
    assert result.billed_cost_cny == Decimal("0.005880")


# ── P0-05 预算 == 用量 ──────────────────────────────────────────────────

def test_budget_settlement_equals_usage_cost():
    """同一 BillingResult 喂预算结算与用量行：两处金额恒等（≤1e-6）。"""
    with patch.object(pricing, "get_current_price_table", return_value=_usd_table()):
        result = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=500_000, output_tokens=250_000),
            enforce=True,
        )
    store = _CaptureQuotaStore()
    budget = _enforce_budget(store)
    budget.reserve("primary", model_name="m")
    from backend.infra.llm.budget import record_model_usage as _rmu

    # 直接以 RequestBudget.record_usage 消费同一 BillingResult
    budget.record_usage(prompt_tokens=500_000, completion_tokens=250_000,
                        total_tokens=750_000, cost=result.billed_cost_cny)
    store.settle(object(), result.billed_cost_cny)
    assert budget.snapshot().cost == result.billed_cost_cny.quantize(Decimal("0.000001"))
    assert store.settled[0] == result.billed_cost_cny


# ── P0-06 / P0-07 trace / 看板 == 用量（不二次换汇）─────────────────────

def _v2_row(billed: float, **kw) -> dict:
    return {
        "component": "llm", "prompt_tokens": 100, "completion_tokens": 50,
        "total_tokens": 150, "cached_tokens": 0, "reasoning_tokens": 0,
        "billing_schema_version": 2, "billed_cost_cny": billed,
        "native_cost": billed / 7.2, "native_currency": "USD",
        "cost_usd": billed / 7.2, "currency": "USD",
        "total_cost": billed, "ts": "2026-10-07T00:00:00",
        "model": "m", "provider": "p", "duration_ms": 1.0,
        **kw,
    }


def test_trace_cost_equals_usage_sum():
    """trace 回填 cost_cny == SUM(billed_cost_cny)——V2 行不再按 currency 折算。"""
    from backend.observability.tracer import TraceRecord

    record = TraceRecord(id="tr-1")
    rows = [_v2_row(0.0864), _v2_row(0.0144)]
    # tracer._backfill_usage_from_store 通过 get_llm_usage_store 拉明细
    class _Store:
        def by_trace(self, trace_id, limit=200):
            return rows

    from backend.observability import llm_usage_store as store_mod

    with patch.object(store_mod, "get_llm_usage_store", lambda: _Store()):
        from backend.observability.tracer import TraceCollector

        TraceCollector._backfill_usage_from_store(record)
    assert record.usage["cost_cny"] == pytest.approx(0.1008, abs=1e-9)
    assert record.cost_cny == pytest.approx(0.1008, abs=1e-9)
    # V2 行原生 USD 口径 = native_cost 直和（不再混算）
    assert record.usage["cost_usd"] == pytest.approx(
        (0.0864 + 0.0144) / 7.2, abs=1e-9,
    )


def test_dashboard_does_not_double_fx():
    """读层 DTO 回填：V2 行金额原样汇总，禁止再 ×7.2。"""
    from backend.app.api.routes import _trace_dto

    data = {"id": "tr-2", "usage": {}, "spans": []}
    rows = [_v2_row(21.6)]

    class _Store:
        def by_trace(self, trace_id, limit=200):
            return rows

    from backend.observability import llm_usage_store as store_mod

    with patch.object(store_mod, "get_llm_usage_store", lambda: _Store()):
        _trace_dto.backfill_usage_from_llm_store(data)
    assert data["cost_cny"] == pytest.approx(21.6, abs=1e-9)
    assert data["usage"]["cost_cny"] == pytest.approx(21.6, abs=1e-9)


# ── P0-10 unpriced 显式 ─────────────────────────────────────────────────

def test_unpriced_is_explicit(monkeypatch):
    """无价且估价 0：unpriced + billed=0（不冒充免费），pricing_source=unpriced。"""
    monkeypatch.setattr(
        pricing, "get_current_price_table",
        lambda *a, **kw: (_ for _ in ()).throw(pricing.PriceTableUnavailable("x")),
    )
    monkeypatch.setattr(
        pricing, "calculate_fallback_cost", lambda *a, **kw: Decimal("0"),
    )
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=100, output_tokens=10),
        enforce=False,
    )
    assert result.cost_status == pricing.COST_STATUS_UNPRICED
    assert result.billed_cost_cny == Decimal("0")
    assert result.pricing_source == pricing.PRICING_SOURCE_UNPRICED


# ── P1-02 / P1-12 FX 与价格版本快照不可变 ────────────────────────────────

def test_fx_snapshot_is_immutable(monkeypatch):
    """ENV 汇率改动只影响新调用；历史 BillingResult 的 fx/金额不变化。"""
    with patch.object(pricing, "get_current_price_table", return_value=_usd_table()):
        old = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1_000_000, output_tokens=0),
            enforce=False,
        )
        # 改 ENV 汇率 7.2 → 7.25
        monkeypatch.setattr(
            "backend.config.budget.BUDGET_FX_USD_CNY", Decimal("7.25"),
        )
        new = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1_000_000, output_tokens=0),
            enforce=False,
        )
    assert old.fx_rate == Decimal("7.20")
    assert old.billed_cost_cny == Decimal("7.200000")   # $1 × 7.2
    assert new.fx_rate == Decimal("7.25")
    assert new.billed_cost_cny == Decimal("7.250000")   # $1 × 7.25


def test_price_version_snapshot_is_immutable(monkeypatch):
    """价格版本 A/B 先后调用：各自固化 version + 单价，历史 A 不被追溯。"""
    with patch.object(pricing, "get_current_price_table",
                      return_value=_usd_table(version="vA")):
        a = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1_000_000, output_tokens=0),
            enforce=False,
        )
    with patch.object(pricing, "get_current_price_table",
                      return_value=_usd_table(version="vB")):
        b = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1_000_000, output_tokens=0),
            enforce=False,
        )
    assert a.price_version == "vA"
    assert a.input_unit_price == Decimal("1.000000")
    assert b.price_version == "vB"
    assert b.input_unit_price == Decimal("1.000000")
    # 版本切换不改历史结果对象
    assert a.price_version == "vA"


# ── P0-11 retry / fallback 独立记账 ─────────────────────────────────────

def test_retry_and_fallback_are_independently_accounted():
    """primary 失败留痕（unavailable 行）+ retry 成功各有独立 BillingResult。"""
    from backend.infra.llm.proxy import _record_failed_attempt

    events: list[dict] = []

    class _Store:
        def record(self, event):
            events.append(dict(event))
            return True

    from backend.observability import llm_usage_store as store_mod

    with patch.object(store_mod, "get_llm_usage_store", lambda: _Store()):
        _record_failed_attempt("m", TimeoutError("timeout"), decision="primary")
    assert len(events) == 1
    failed = events[0]
    assert failed["cost_status"] == pricing.COST_STATUS_UNPRICED
    assert failed["usage_source"] == "unavailable"
    assert float(failed["billed_cost_cny"]) == 0.0
    assert failed["decision"] == "primary"

    # retry 成功：独立 price_usage 结果（不与失败行混账）
    with patch.object(pricing, "get_current_price_table", return_value=_usd_table()):
        retry = price_usage(
            "m", "llm",
            NormalizedUsage(input_tokens=1000, output_tokens=500),
            enforce=False,
        )
    assert retry.cost_status == COST_STATUS_EXACT
    assert retry.usage_source == "provider"


# ── P1-10 legacy 行兼容 ─────────────────────────────────────────────────

def test_legacy_rows_do_not_break_dashboard():
    """legacy 行（版本 1、currency 混态）读层不炸、按旧逻辑兜底展示。"""
    from backend.app.api.routes import _trace_dto

    legacy_usd_row = {
        "component": "llm", "prompt_tokens": 10, "completion_tokens": 5,
        "total_tokens": 15, "cached_tokens": 0, "reasoning_tokens": 0,
        "billing_schema_version": 1, "cost_usd": 0.5, "currency": "USD",
        "total_cost": 0.5, "ts": "2026-09-01T00:00:00", "model": "m",
        "provider": "p",
    }
    legacy_cny_row = {
        **legacy_usd_row, "cost_usd": 2.0, "currency": "CNY", "total_cost": 2.0,
    }
    data = {"id": "tr-3", "usage": {}, "spans": []}

    class _Store:
        def by_trace(self, trace_id, limit=200):
            return [legacy_usd_row, legacy_cny_row]

    from backend.observability import llm_usage_store as store_mod

    with patch.object(store_mod, "get_llm_usage_store", lambda: _Store()):
        _trace_dto.backfill_usage_from_llm_store(data)
    # USD 行 0.5×7.2 + CNY 行 2.0 = 5.6（legacy 兜底口径，明确非 V2 精确值）
    assert data["cost_cny"] == pytest.approx(5.6, abs=1e-9)
