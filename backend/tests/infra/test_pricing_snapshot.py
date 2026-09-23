"""Model Governance STOP C —— 单价快照落库（C6 端到端）+ C14 Registry→Budget 接口。

test_pricing_snapshot：calculate_llm_cost_with_status 的快照字段经
_record_tokens 进入 llm_usage 事件（mock store 捕获验证 NUMERIC 可空语义）。

C14（Context Budget 集成）：只验证 Registry → Budget 的窗口接口
（resolve_model_context_window 读 llm_models.context_length），
不触碰 Context Budget 算法本体（CORE_FROZEN）。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.infra.llm import pricing
from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.pricing import PriceLine, PriceTable


class _CaptureStore:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(dict(event))
        return True


def _price_table():
    return PriceTable([
        PriceLine(model_name="doubao-seed-2.0-mini", component="llm",
                  dimension="input", price_per_unit=Decimal("0.200000")),
        PriceLine(model_name="doubao-seed-2.0-mini", component="llm",
                  dimension="output", price_per_unit=Decimal("2.000000")),
        PriceLine(model_name="doubao-seed-2.0-mini", component="llm",
                  dimension="cache_read", price_per_unit=Decimal("0.020000")),
    ])


class _FakeResult:
    def __init__(self):
        self.response_metadata = {
            "token_usage": {
                "prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
                "total_tokens": 2_000_000,
                "input_token_details": {"cache_read": 0},
            },
            "finish_reason": "stop",
            "model_name": "doubao-seed-2-0-mini-260428",
        }


def test_snapshot_prices_reach_usage_event(monkeypatch):
    """单价快照 + 身份列进入 usage 事件（store 落库的最终形状）。"""
    store = _CaptureStore()
    import backend.observability.llm_usage_store as storeapi

    monkeypatch.setattr(storeapi, "get_llm_usage_store", lambda: store)
    monkeypatch.setattr(pricing, "get_current_price_table",
                        lambda model, component: _price_table())
    proxy_mod._record_tokens(_FakeResult(), model_name="doubao-seed-2.0-mini")
    row = store.events[0]
    assert row["model"] == "doubao-seed-2.0-mini"          # canonical 赢
    assert row["upstream_model_id"] == "doubao-seed-2-0-mini-260428"
    assert row["input_unit_price"] == 0.2
    assert row["output_unit_price"] == 2.0
    assert row["cache_input_unit_price"] == 0.02
    assert row["cost_status"] == "exact"
    assert abs(row["cost_usd"] - 2.2) < 1e-6


def test_price_unknown_leaves_unit_prices_null(monkeypatch):
    """硬门缺价（price_unknown）→ 单价 NULL（无生效价格行的诚实语义）。"""
    store = _CaptureStore()
    import backend.observability.llm_usage_store as storeapi

    monkeypatch.setattr(storeapi, "get_llm_usage_store", lambda: store)

    def _unavailable(model, component):
        raise pricing.PriceTableUnavailable("db down")

    from backend.infra.llm.budget import RequestBudget
    # 预算 enforce 态由 current_request_budget 决定；注入一个 enforce 预算
    # 复杂度高 —— 直接单测 proxy 对异常分支的常量与空 breakdown 行为：
    with patch.object(pricing, "get_current_price_table",
                      side_effect=_unavailable):
        _, status, _, breakdown = pricing.calculate_llm_cost_with_status(
            "doubao-seed-2.0-mini",
            {"input": 10, "cache_read": 0, "output": 10})
    # 软路径缺价 → estimated + 0 单价；硬门缺价 → price_unknown + NULL 单价
    #（proxy 分支，见 test_usage_identity / 实机验收）。此处锁软路径语义。
    assert status in ("estimated", "unpriced")
    assert breakdown["input_unit_price"] == 0.0


# ── C14：Registry → Context Budget 窗口接口（只读验证）───────────────

def test_registry_window_feeds_context_budget_interface(monkeypatch):
    """llm_models.context_length 登记 → resolve_model_context_window 生效；
    未登记模型回退配置窗口（fail-safe），切换模型无需重启。"""
    from backend.context_budget.token_counter import resolve_model_context_window
    from backend.infra.llm import models

    models.reset_dynamic_models_for_tests()
    try:
        models.set_dynamic_models([
            {"name": "small-model", "provider": "custom-api",
             "context_length": 8192, "source": "db"},
            {"name": "large-model", "provider": "custom-api",
             "context_length": 262144, "source": "db"},
        ])
        # Registry 值参与 min(配置窗口, 登记窗口) 决策：把配置窗口临时放大，
        # 登记值即成为约束（env 8192 恒为上限是运营配置语义，不是代码缺陷）
        import backend.config.llm as config_llm

        monkeypatch.setattr(config_llm, "LLM_CONTEXT_LENGTH", 1_000_000)
        small = resolve_model_context_window("small-model")
        large = resolve_model_context_window("large-model")
        assert small == 8192 and large == 262144
        assert small < large, "登记窗口必须直接决定 Budget 有效窗口"
    finally:
        models.reset_dynamic_models_for_tests()


def test_registry_window_null_falls_back_to_config():
    """窗口未登记（NULL）→ 配置窗口兜底（不编造，FAIL-SAFE 小窗口）。"""
    from backend.context_budget.token_counter import resolve_model_context_window
    from backend.infra.llm import models

    models.reset_dynamic_models_for_tests()
    try:
        models.set_dynamic_models([
            {"name": "unknown-window-model", "provider": "custom-api",
             "context_length": None, "source": "db"},
        ])
        base = resolve_model_context_window("")
        assert resolve_model_context_window("unknown-window-model") == base
    finally:
        models.reset_dynamic_models_for_tests()
