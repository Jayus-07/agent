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


def test_local_call_no_cloud_reservation_and_calls_still_counted(monkeypatch):
    """本地调用不预占云端账单；max_calls 自 231675e 起只记账不拒绝。"""
    monkeypatch.setattr(models, "lookup_model_entry", lambda name: {"name": name})
    monkeypatch.setattr(models, "get_model_billing", lambda name: "local")
    quota = SimpleNamespace(reserve=lambda **k: pytest.fail("本地调用不应预占云端账单"))
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_calls=1), mode="enforce", quota_store=quota,
        user_id="u", tenant_id="t",
    )
    assert state.reserve("primary", model_name="local-model", component="embedding") is None
    # 超过 max_calls 不再拒绝（231675e：调用数只用于用量观测），
    # 但仍要如实记账，云端账单桩同样不得被触碰。
    assert state.reserve("primary", model_name="local-model", component="embedding") is None
    assert state.calls == 2


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


def test_local_record_usage_with_review_reason_never_creates_review(monkeypatch):
    """T2：本地模型无预占时，即使携带 review_reason 也只返回 not_reserved。

    embedding/rerank 本地调用（127.0.0.1:8088）不预占云端账单；历史事故是
    未定价调用经 review_reason 挂进待对账造成堆积，这里守住：无预占 =
    永不创建 needs_review，quota_store 的标记入口被触碰即失败。
    """
    quota = SimpleNamespace(
        mark_needs_review=lambda *a, **k: pytest.fail("无预占不得转待对账"),
    )
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(), mode="enforce", quota_store=quota,
        user_id="u", tenant_id="t",
    )
    budget._states["req-local-record"] = state
    request_token = budget._current_request_id.set("req-local-record")
    reservation_token = budget._current_quota_reservation.set(None)
    try:
        status = budget.record_model_usage(
            prompt_tokens=8, review_reason="embedding_usage_or_price_unavailable",
        )
    finally:
        budget._states.pop("req-local-record", None)
        budget._current_request_id.reset(request_token)
        budget._current_quota_reservation.reset(reservation_token)
    assert status == "not_reserved"
    assert state.total_tokens == 8


def test_local_scoped_model_call_no_reservation_no_review(monkeypatch):
    """T1 扩展：本地模型走 scoped_model_call 全链路不预占、finally 不挂待对账。

    scoped_model_call 的 finally 会以 provider_usage_unavailable 收敛预占；
    本地模型没有预占，该收敛必须是无害 no-op，不得触碰任何云端账目入口。
    """
    monkeypatch.setattr(models, "lookup_model_entry", lambda name: {"name": name})
    monkeypatch.setattr(models, "get_model_billing", lambda name: "local")
    quota = SimpleNamespace(
        reserve=lambda **k: pytest.fail("本地调用不应预占云端账单"),
        settle=lambda *a, **k: pytest.fail("本地调用不应产生结算"),
        mark_needs_review=lambda *a, **k: pytest.fail("本地调用不得进入待对账"),
    )
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(), mode="enforce", quota_store=quota,
        user_id="u", tenant_id="t",
    )
    budget._states["req-local-scoped"] = state
    request_token = budget._current_request_id.set("req-local-scoped")
    reservation_token = budget._current_quota_reservation.set(None)
    try:
        with budget.scoped_model_call("local-model", "embedding"):
            pass  # 模拟本地调用本体：预占跳过、无结算
    finally:
        budget._states.pop("req-local-scoped", None)
        budget._current_request_id.reset(request_token)
        budget._current_quota_reservation.reset(reservation_token)
    assert state.calls == 1
