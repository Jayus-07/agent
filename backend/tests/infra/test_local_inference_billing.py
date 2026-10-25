"""本地推理不伪造云端价格，也不能绕过模型调用限额。"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.infra.llm import budget, models, pricing


def test_local_inference_keeps_usage_without_cloud_cash_or_fake_price(monkeypatch):
    monkeypatch.setattr(models, "lookup_model_entry", lambda name: {"name": name})
    monkeypatch.setattr(models, "get_model_billing", lambda name: "local")
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *a: pytest.fail("本地推理不应要求云端单价"))
    result = pricing.price_usage(
        "local-model", "embedding", pricing.NormalizedUsage(input_tokens=123),
        enforce=True, usage_source="estimated",
    )
    assert result.input_tokens == 123
    assert result.billed_cost_cny == Decimal("0")
    assert result.pricing_source == "local_inference"
    assert result.usage_source == "estimated"
    assert result.cost_status == "estimated"


def test_local_call_limit_still_enforced_and_no_cloud_reservation(monkeypatch):
    monkeypatch.setattr(models, "lookup_model_entry", lambda name: {"name": name})
    monkeypatch.setattr(models, "get_model_billing", lambda name: "local")
    quota = SimpleNamespace(reserve=lambda **k: pytest.fail("本地调用不应预占云端账单"))
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_calls=1), mode="enforce", quota_store=quota,
        user_id="u", tenant_id="t",
    )
    assert state.reserve("primary", model_name="local-model", component="embedding") is None
    with pytest.raises(budget.RequestBudgetExceeded):
        state.reserve("primary", model_name="local-model", component="embedding")


def test_unknown_provider_still_requires_cloud_price(monkeypatch):
    monkeypatch.setattr(models, "lookup_model_entry", lambda name: None)
    monkeypatch.setattr(models, "get_model_billing", lambda name: "local")
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *a: pricing.PriceTable([]))
    monkeypatch.setattr(pricing, "has_registry_unit_price", lambda name: False)
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_calls=1), mode="enforce",
        quota_store=SimpleNamespace(), user_id="u", tenant_id="t",
    )
    with pytest.raises(budget.RequestBudgetExceeded, match="price_unavailable"):
        state.reserve("primary", model_name="unknown-model", component="embedding")
