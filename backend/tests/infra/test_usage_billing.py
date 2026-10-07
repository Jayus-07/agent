"""price_usage 唯一计费入口 —— Billing Contract（2026-10-07 收口）。

历史版本（2026-09-22）断言「USD 价格表 → 返回 USD」；2026-10-01 本位币切换
后运行时已改出 CNY，旧断言与新契约矛盾（本文件 4 用例因此长期红）。
本轮按冻结的 Billing Contract 重写：金额恒 CNY、原生成本单列、单价快照
原生币种、enforce/observe 同算法。

价格快照通过 monkeypatch `get_current_price_table` 注入内存 PriceTable，
不依赖 PG 实库。
"""
from decimal import Decimal

import pytest

from backend.infra.llm import pricing
from backend.infra.llm.pricing import (
    COST_STATUS_ESTIMATED,
    COST_STATUS_EXACT,
    COST_STATUS_UNPRICED,
    NormalizedUsage,
    PriceTable,
    calculate_llm_cost_with_status,
    price_usage,
)


def _table(rows: list[dict]) -> PriceTable:
    return PriceTable.from_rows(rows)


def _llm_rows(
    input_price: str | None,
    output_price: str | None,
    cache_read_price: str | None = None,
    currency: str = "CNY",
    version: str = "2026-10-07-v1",
) -> list[dict]:
    rows: list[dict] = []
    for dimension, price in (
        ("input", input_price), ("output", output_price),
        ("cache_read", cache_read_price),
    ):
        if price is None:
            continue
        rows.append({
            "model_name": "m", "component": "llm", "dimension": dimension,
            "price_per_unit": price, "unit": "per_1m_tokens",
            "currency": currency, "price_table_version": version,
        })
    return rows


@pytest.fixture()
def price_snapshot(monkeypatch):
    """把 PG 价格快照替换为内存表；空列表 = PG 无任何价格行。"""
    holder: dict[str, list[dict]] = {"rows": []}

    def _install(rows: list[dict]) -> None:
        holder["rows"] = rows

    def _fake_get(model_name: str, component: str) -> PriceTable:
        return _table(holder["rows"])

    monkeypatch.setattr(pricing, "get_current_price_table", _fake_get)
    yield _install


def test_plain_model_exact(price_snapshot):
    """普通模型（CNY 价）：input=1000 / output=500，价 3 / 9（¥/1M）。"""
    price_snapshot(_llm_rows("3", "9"))
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=False,
    )
    assert result.cost_status == COST_STATUS_EXACT
    assert result.pricing_source == pricing.PRICING_SOURCE_APPROVED_TABLE
    assert result.native_currency == "CNY"
    assert result.native_cost == Decimal("0.007500")
    assert result.billed_cost_cny == Decimal("0.007500")   # 原生即本位币，不换汇
    assert result.fx_rate is None
    assert result.price_version == "2026-10-07-v1"
    assert result.input_cost_cny == Decimal("0.003000")
    assert result.output_cost_cny == Decimal("0.004500")


def test_exact_usd_converts_to_cny_once(price_snapshot):
    """P0-02：USD 价 → 原生成本与记账成本分列，汇率只乘一次。

    价格 input $1 / output $2，Usage 各 1M，FX=7.2：
    native = $3.00，billed = ¥21.6。禁止出现 21.6×7.2=155.52 或裸 3。
    """
    price_snapshot(_llm_rows("1", "2", currency="USD"))
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        enforce=False,
    )
    assert result.native_cost == Decimal("3.000000")
    assert result.native_currency == "USD"
    assert result.fx_rate == Decimal("7.20")
    assert result.billed_cost_cny == Decimal("21.600000")
    # wrapper 四元组同样只出 CNY（金额与 BillingResult 一致）
    total, status, currency, _ = calculate_llm_cost_with_status(
        "m", {"input": 1_000_000, "cache_read": 0, "output": 1_000_000},
    )
    assert total == Decimal("21.600000")
    assert status == COST_STATUS_EXACT
    assert currency == "CNY"


def test_unit_price_snapshot_is_native(price_snapshot):
    """单价快照 = 原生币种（审计恒等式：tokens × 原生单价 = native_cost）。"""
    price_snapshot(_llm_rows("1", "2", currency="USD"))
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        enforce=False,
    )
    assert result.input_unit_price == Decimal("1.000000")
    assert result.output_unit_price == Decimal("2.000000")


def test_cached_tokens_not_double_counted(price_snapshot):
    """P0-04（最关键回归）：input=1000 含 cached=600 → billable=400。

    价：input ¥3 / cache_read ¥0.3 / output ¥9。
    禁止 input 全量 × input 价 + cached × cache 价的双算。
    """
    price_snapshot(_llm_rows("3", "9", cache_read_price="0.3"))
    result = price_usage(
        "m", "llm",
        NormalizedUsage(input_tokens=1000, cached_input_tokens=600, output_tokens=500),
        enforce=False,
    )
    assert result.billable_input_tokens == 400
    assert result.cost_status == COST_STATUS_EXACT
    # 400/1M*3 + 600/1M*0.3 + 500/1M*9 = 0.0012 + 0.00018 + 0.0045
    assert result.billed_cost_cny == Decimal("0.005880")
    assert result.cached_input_cost_cny == Decimal("0.000180")


def test_zero_cache_hit_is_exact(price_snapshot):
    """无缓存命中（cached=0）：即使没有 cache_read 价格行也是 exact。"""
    price_snapshot(_llm_rows("3", "9"))
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=False,
    )
    assert result.cost_status == COST_STATUS_EXACT
    assert result.cached_input_cost_cny == Decimal("0")


def test_cache_hit_without_cache_price_is_estimated(price_snapshot):
    """缓存命中但未配置 cache_read 价：cached 按普通 input 价保守估算。"""
    price_snapshot(_llm_rows("3", "9", cache_read_price=None))
    result = price_usage(
        "m", "llm",
        NormalizedUsage(input_tokens=1000, cached_input_tokens=600, output_tokens=500),
        enforce=False,
    )
    assert result.cost_status == COST_STATUS_ESTIMATED
    assert result.cached_input_cost_cny == Decimal("0.001800")
    # 400/1M*3 + 600/1M*3 + 500/1M*9 = 0.0075
    assert result.billed_cost_cny == Decimal("0.007500")


def test_observe_enforce_same_amount(price_snapshot):
    """P0-01：同 Usage 同价格快照下，enforce 与 observe 金额/native 完全一致。

    模式差异只允许出现在 cost_status（缺价时 price_unknown vs estimated）。
    """
    price_snapshot(_llm_rows("1", "2", currency="USD"))
    usage = NormalizedUsage(input_tokens=500_000, output_tokens=250_000)
    observe = price_usage("m", "llm", usage, enforce=False)
    hard = price_usage("m", "llm", usage, enforce=True)
    assert observe.billed_cost_cny == hard.billed_cost_cny
    assert observe.native_cost == hard.native_cost
    assert observe.cost_status == hard.cost_status == COST_STATUS_EXACT


def test_enforce_missing_price_is_price_unknown_with_fx(price_snapshot, monkeypatch):
    """P0-03：enforce 缺价 → price_unknown；注册表估价 USD 先折 CNY 再结算。

    注册表估价 $0.002（由 calculate_fallback_cost 兜出）× 7.2 = ¥0.0144。
    """
    price_snapshot([])  # PG 无价格行
    monkeypatch.setattr(
        pricing, "calculate_fallback_cost",
        lambda *_args, **_kw: Decimal("0.002"),
    )
    observe = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=False,
    )
    hard = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=True,
    )
    assert observe.cost_status == COST_STATUS_ESTIMATED
    assert hard.cost_status == pricing.COST_STATUS_PRICE_UNKNOWN
    assert observe.billed_cost_cny == hard.billed_cost_cny == Decimal("0.014400")
    assert hard.pricing_source == pricing.PRICING_SOURCE_REGISTRY_FALLBACK
    assert hard.native_currency == "USD"


def test_unpriced_model_records_zero_without_error(price_snapshot, monkeypatch):
    """没有价格且估价为 0：unpriced（只记 token 不计费），不冒充 exact-0。"""
    price_snapshot([])  # PG 无价格行
    monkeypatch.setattr(
        pricing, "calculate_fallback_cost",
        lambda *_args, **_kw: Decimal("0"),
    )
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=False,
    )
    assert result.cost_status == COST_STATUS_UNPRICED
    assert result.billed_cost_cny == Decimal("0")
    assert result.pricing_source == pricing.PRICING_SOURCE_UNPRICED


def test_estimated_usage_source_taints_exact(price_snapshot):
    """P0-12：usage_source='estimated' 时即使价格精确也不标 exact。"""
    price_snapshot(_llm_rows("3", "9"))
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
        enforce=False, usage_source=pricing.USAGE_SOURCE_ESTIMATED,
    )
    assert result.cost_status == COST_STATUS_ESTIMATED
    assert result.usage_source == pricing.USAGE_SOURCE_ESTIMATED


def test_unpriced_dimension_degrades_status(price_snapshot):
    """有量无价的自定义维度（reasoning 有 token 无价行）→ estimated，不静默跳过。"""
    price_snapshot(_llm_rows("3", "9"))
    result = price_usage(
        "m", "llm",
        NormalizedUsage(input_tokens=1000, output_tokens=500, reasoning_tokens=100),
        enforce=False,
    )
    assert result.cost_status == COST_STATUS_ESTIMATED
    assert result.reasoning_cost_cny == Decimal("0")


def test_reasoning_dimension_priced_when_row_exists(price_snapshot):
    """模型提供 reasoning 价行时：推理 token 可计费、可审计（P1-03）。"""
    rows = _llm_rows("3", "9")
    rows.append({
        "model_name": "m", "component": "llm", "dimension": "reasoning",
        "price_per_unit": "6", "unit": "per_1m_tokens",
        "currency": "CNY", "price_table_version": "2026-10-07-v1",
    })
    price_snapshot(rows)
    result = price_usage(
        "m", "llm",
        NormalizedUsage(input_tokens=1_000_000, output_tokens=500_000,
                        reasoning_tokens=100_000),
        enforce=False,
    )
    assert result.reasoning_cost_cny == Decimal("0.600000")
    assert result.reasoning_unit_price == Decimal("6")
    assert result.cost_status == COST_STATUS_EXACT


def test_decimal_precision_no_float_drift(price_snapshot):
    """Decimal 精度：不复现 0.1+0.2 型 float 误差。"""
    price_snapshot(_llm_rows("0.1", "0.2"))
    result = price_usage(
        "m", "llm", NormalizedUsage(input_tokens=100_000, output_tokens=100_000),
        enforce=False,
    )
    # 100000/1M*0.1 + 100000/1M*0.2 = 0.03（float 会得 0.030000000000000002）
    assert result.cost_status == COST_STATUS_EXACT
    assert result.billed_cost_cny == Decimal("0.030000")


def test_price_table_unavailable_falls_back_to_estimated(price_snapshot, monkeypatch):
    """PG 价格表不可用：退回注册表内置估价 → estimated（不抛错）。"""
    def _boom(model_name: str, component: str) -> PriceTable:
        raise pricing.PriceTableUnavailable("模型价格表不可用")

    monkeypatch.setattr(pricing, "get_current_price_table", _boom)
    monkeypatch.setattr(
        pricing, "calculate_fallback_cost",
        lambda model, p, c: Decimal("0.002"),
    )
    total, status, currency, breakdown = calculate_llm_cost_with_status(
        "m", {"input": 1000, "cache_read": 0, "output": 500},
    )
    assert status == COST_STATUS_ESTIMATED
    assert total == Decimal("0.014400")  # fallback 估价 USD 0.002 × 7.2
    assert currency == "CNY"


def test_billing_never_raises(price_snapshot):
    """billing fail-open：任何异常路径（价格层炸裂）都不向上抛。"""
    def _boom(model_name: str, component: str) -> PriceTable:
        raise RuntimeError("boom")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pricing, "get_current_price_table", _boom)
        result = price_usage(
            "m", "llm", NormalizedUsage(input_tokens=1000, output_tokens=500),
            enforce=False,
        )
    assert result.cost_status in (COST_STATUS_ESTIMATED, COST_STATUS_UNPRICED)
