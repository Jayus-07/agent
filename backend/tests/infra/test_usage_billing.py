"""Model Governance STOP C —— Billing Snapshot 与 cost 状态口径（C6/C7/C10）。

单价快照随调用落库；price missing 不当免费（unpriced 语义）；
billing fail-open（计费失败不影响主链路）。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.infra.llm import pricing
from backend.infra.llm.pricing import PriceLine, PriceTable


def _price_table(model="doubao-seed-2.0-mini", include_cache=True, currency="USD"):
    rows = [
        PriceLine(model_name=model, component="llm", dimension="input",
                  price_per_unit=Decimal("0.200000"), currency=currency),
        PriceLine(model_name=model, component="llm", dimension="output",
                  price_per_unit=Decimal("2.000000"), currency=currency),
    ]
    if include_cache:
        rows.append(PriceLine(model_name=model, component="llm",
                              dimension="cache_read",
                              price_per_unit=Decimal("0.020000"),
                              currency=currency))
    return PriceTable(rows)


def _patch_table(table):
    return patch.object(pricing, "get_current_price_table", return_value=table)


QUANTITIES = {"input": 1_000_000, "cache_read": 0, "output": 1_000_000}


def test_unit_price_snapshot_present_on_exact():
    """PG 价格在场 → 单价快照随 breakdown 返回（C6）。"""
    with _patch_table(_price_table()):
        total, status, currency, breakdown = pricing.calculate_llm_cost_with_status(
            "doubao-seed-2.0-mini", QUANTITIES)
    assert status == pricing.COST_STATUS_EXACT
    assert currency == "USD"
    assert breakdown["input_unit_price"] == 0.2
    assert breakdown["output_unit_price"] == 2.0
    assert breakdown["cache_input_unit_price"] == 0.02
    # 金额与单价自洽（per 1M tokens）
    assert total == Decimal("2.200000")


def test_snapshot_unit_prices_survive_price_change():
    """E6 前置回归：改价后同一 tokens 的单价快照跟随新价 ——
    快照随调用物化，历史行不被追溯污染（快照列由 proxy 落库）。"""
    with _patch_table(_price_table()):
        _, _, _, before = pricing.calculate_llm_cost_with_status(
            "doubao-seed-2.0-mini", QUANTITIES)
    changed = _price_table(include_cache=False)
    # 模拟改价：output 2.0 → 4.0
    rows = [PriceLine(model_name="doubao-seed-2.0-mini", component="llm",
                      dimension="input", price_per_unit=Decimal("0.200000")),
            PriceLine(model_name="doubao-seed-2.0-mini", component="llm",
                      dimension="output", price_per_unit=Decimal("4.000000"))]
    with _patch_table(PriceTable(rows)):
        _, _, _, after = pricing.calculate_llm_cost_with_status(
            "doubao-seed-2.0-mini", QUANTITIES)
    assert before["output_unit_price"] == 2.0
    assert after["output_unit_price"] == 4.0


def test_missing_cache_price_estimated_with_input_unit_snapshot():
    """缓存命中但无 cache_read 价行 → estimated，缓存单价按 input 价快照。"""
    with _patch_table(_price_table(include_cache=False)):
        total, status, currency, breakdown = pricing.calculate_llm_cost_with_status(
            "doubao-seed-2.0-mini",
            {"input": 500_000, "cache_read": 500_000, "output": 1_000_000},
        )
    assert status == pricing.COST_STATUS_ESTIMATED
    assert breakdown["cache_input_unit_price"] == 0.2  # 保守按 input 价


def test_price_table_unavailable_falls_back_no_unit_prices(monkeypatch):
    """价格表不可用 → 注册表估价（登记价 >0 → estimated），无单价快照。"""
    from backend.infra.llm import models

    models.reset_dynamic_models_for_tests()
    models.set_dynamic_models([
        {"name": "doubao-seed-2.0-mini", "provider": "custom-doubao-seed-2-0-mini",
         "input_price_per_1m": 0.2, "output_price_per_1m": 2.0, "source": "db"},
    ])
    try:
        with patch.object(pricing, "get_current_price_table",
                          side_effect=pricing.PriceTableUnavailable("db down")):
            total, status, currency, breakdown = (
                pricing.calculate_llm_cost_with_status(
                    "doubao-seed-2.0-mini", QUANTITIES))
        assert status == pricing.COST_STATUS_ESTIMATED
        # 估价 = 1M*0.2 + 1M*2.0 = 2.2
        assert total == Decimal("2.200000")
        assert breakdown["input_unit_price"] == 0.0  # 不可审计时显式为 0，不伪造
    finally:
        models.reset_dynamic_models_for_tests()


def test_unpriced_never_treated_as_free():
    """无 PG 价格且注册表估价为 0 → unpriced（只记 token 不计费，
    不冒充 exact/exact-0）。"""
    with patch.object(pricing, "get_current_price_table",
                      side_effect=pricing.PriceTableUnavailable("db down")):
        total, status, _, breakdown = pricing.calculate_llm_cost_with_status(
            "qwen2.5:3b", QUANTITIES)
    assert status == pricing.COST_STATUS_UNPRICED
    assert total == Decimal("0.000000")


def test_price_unknown_status_registered():
    """C10 枚举收口：price_unknown 是声明过的第四值（proxy 硬门缺价标记）。"""
    assert pricing.COST_STATUS_PRICE_UNKNOWN == "price_unknown"
    assert pricing.COST_STATUS_EXACT == "exact"
    assert pricing.COST_STATUS_ESTIMATED == "estimated"
    assert pricing.COST_STATUS_UNPRICED == "unpriced"


def test_billing_never_raises():
    """billing fail-open：任何异常路径（价格层炸裂）都不向上抛。"""
    with patch.object(pricing, "get_current_price_table",
                      side_effect=RuntimeError("boom")):
        total, status, currency, breakdown = pricing.calculate_llm_cost_with_status(
            "doubao-seed-2.0-mini", QUANTITIES)
    assert status in (pricing.COST_STATUS_ESTIMATED, pricing.COST_STATUS_UNPRICED)
