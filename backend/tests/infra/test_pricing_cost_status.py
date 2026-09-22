"""calculate_llm_cost_with_status —— Token 成本计量第一阶段（2026-09-22）。

覆盖验收要求的六类用例：普通模型 / 缓存不重复计费 / 无缓存命中 /
无缓存价格（estimated）/ 无价格（unpriced）/ Decimal 精度。

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
    PriceTable,
    calculate_llm_cost_with_status,
)


def _table(rows: list[dict]) -> PriceTable:
    return PriceTable.from_rows(rows)


def _llm_rows(
    input_price: str | None,
    output_price: str | None,
    cache_read_price: str | None = None,
    currency: str = "CNY",
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
            "price_per_unit": price, "unit": "per_1m_tokens", "currency": currency,
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
    """普通模型：input=1000 / output=500，价 3 / 9（¥/1M）。"""
    price_snapshot(_llm_rows("3", "9"))
    total, status, currency, breakdown = calculate_llm_cost_with_status(
        "m", {"input": 1000, "cache_read": 0, "output": 500},
    )
    assert status == COST_STATUS_EXACT
    assert currency == "CNY"
    assert total == Decimal("0.007500")
    assert breakdown["input_cost"] == pytest.approx(0.003, abs=1e-9)
    assert breakdown["cached_input_cost"] == 0.0
    assert breakdown["output_cost"] == pytest.approx(0.0045, abs=1e-9)


def test_cached_tokens_not_double_counted(price_snapshot):
    """缓存模型（最关键回归）：input=1000 **含** cached=600 → billable=400。

    价：input ¥3 / cache_read ¥0.3 / output ¥9。
    禁止出现 input_tokens 全量 × input_price + cached × cache_price 的双算。
    """
    price_snapshot(_llm_rows("3", "9", cache_read_price="0.3"))
    total, status, currency, breakdown = calculate_llm_cost_with_status(
        "m", {"input": 400, "cache_read": 600, "output": 500},
    )
    assert status == COST_STATUS_EXACT
    # 400/1M*3 + 600/1M*0.3 + 500/1M*9 = 0.0012 + 0.00018 + 0.0045
    assert total == Decimal("0.005880")
    assert breakdown["input_cost"] == pytest.approx(0.0012, abs=1e-9)
    assert breakdown["cached_input_cost"] == pytest.approx(0.00018, abs=1e-9)
    assert breakdown["output_cost"] == pytest.approx(0.0045, abs=1e-9)


def test_zero_cache_hit_is_exact(price_snapshot):
    """无缓存命中（cached=0）：即使没有 cache_read 价格行也是 exact。"""
    price_snapshot(_llm_rows("3", "9"))
    total, status, _, breakdown = calculate_llm_cost_with_status(
        "m", {"input": 1000, "cache_read": 0, "output": 500},
    )
    assert status == COST_STATUS_EXACT
    assert breakdown["cached_input_cost"] == 0.0


def test_cache_hit_without_cache_price_is_estimated(price_snapshot):
    """缓存命中但未配置 cache_read 价：cached 按普通 input 价保守估算。"""
    price_snapshot(_llm_rows("3", "9", cache_read_price=None))
    total, status, _, breakdown = calculate_llm_cost_with_status(
        "m", {"input": 400, "cache_read": 600, "output": 500},
    )
    assert status == COST_STATUS_ESTIMATED
    # 缓存按 input 价：600/1M*3 = 0.0018
    assert breakdown["cached_input_cost"] == pytest.approx(0.0018, abs=1e-9)
    # 400/1M*3 + 600/1M*3 + 500/1M*9 = 0.0012 + 0.0018 + 0.0045
    assert total == Decimal("0.007500")


def test_unpriced_model_records_zero_without_error(price_snapshot, monkeypatch):
    """没有价格：cost=0、status=unpriced，不影响调用（函数本身不抛错）。"""
    price_snapshot([])  # PG 无价格行
    monkeypatch.setattr(
        pricing, "calculate_fallback_cost",
        lambda *_args, **_kw: Decimal("0"),
    )
    total, status, currency, breakdown = calculate_llm_cost_with_status(
        "m", {"input": 1000, "cache_read": 0, "output": 500},
    )
    assert status == COST_STATUS_UNPRICED
    assert total == Decimal("0")
    assert currency == "USD"
    assert breakdown == {
        "input_cost": 0.0, "cached_input_cost": 0.0, "output_cost": 0.0,
    }


def test_decimal_precision_no_float_drift(price_snapshot):
    """Decimal 精度：不复现 0.1+0.2 型 float 误差。"""
    price_snapshot(_llm_rows("0.1", "0.2"))
    total, status, _, _ = calculate_llm_cost_with_status(
        "m", {"input": 100000, "cache_read": 0, "output": 100000},
    )
    # 100000/1M*0.1 + 100000/1M*0.2 = 0.01 + 0.02 = 0.03（float 会得 0.030000000000000002）
    assert status == COST_STATUS_EXACT
    assert total == Decimal("0.030000")


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
    assert total == Decimal("0.002000")
    assert currency == "USD"
