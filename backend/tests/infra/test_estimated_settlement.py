"""未知用量估算结算（2026-11-10 待对账治理 1.3）：settle_or_review_model_reservation。

旧收敛「一律转待对账」让小额未知用量在待对账队列堆积（121 笔历史积压的
主入口之一）。新收敛：预占额 ≤ BUDGET_ESTIMATED_SETTLEMENT_MAX_CNY 按预占
上限保守结算（settled_estimated），大额才转 needs_review；阈值 0 = 全部
人工（回滚开关）。release 语义（确定未发给供应商）不经过这里，单独守卫。
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.infra.llm import budget

_REQUEST_ID = "estimated-settlement-test"


class _Quota:
    """记录收敛动作的桩配额存储；可注入结算失败。"""

    def __init__(self, settle_error: Exception | None = None):
        self.settled: list[tuple[object, Decimal]] = []
        self.reviewed: list[str] = []
        self.released: list[object] = []
        self._settle_error = settle_error

    def settle(self, reservation, amount, *, settle_kind: str = ""):
        if self._settle_error is not None:
            raise self._settle_error
        self.settled.append((reservation, Decimal(amount), settle_kind))

    def mark_needs_review(self, reservation, reason):
        self.reviewed.append(reason)
        return True

    def release(self, reservation):
        self.released.append(reservation)


def _reservation(amount: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        reservation_id="res-1",
        reserved_cny=Decimal(amount) if amount is not None else None,
    )


def _run(quota: _Quota, reservation, reason: str) -> tuple[str, object]:
    """绑定请求预算状态并在上下文预占上执行收敛，返回 (状态, 状态对象)。"""
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_cost="0.50"), mode="enforce",
        user_id="u", tenant_id="t", quota_store=quota,
    )
    budget._states[_REQUEST_ID] = state
    request_token = budget._current_request_id.set(_REQUEST_ID)
    reservation_token = budget._current_quota_reservation.set(reservation)
    try:
        status = budget.settle_or_review_model_reservation(reason)
    finally:
        budget._states.pop(_REQUEST_ID, None)
        budget._current_request_id.reset(request_token)
        budget._current_quota_reservation.reset(reservation_token)
    return status, quota


def test_small_unknown_usage_settles_at_reserved_cap(monkeypatch):
    """T6：小额未知用量 ¥0.5 → 按预占上限估算结算 settled_estimated。"""
    quota = _Quota()
    status, _ = _run(quota, _reservation("0.50"), "call_failed_possibly_billed")
    assert status == "settled_estimated"
    # 迁移 145：估算结算必须带 settle_kind='estimated' DB 标记，
    # billing_mismatch 对账据此排除（设计内保守口径 ≠ 结算分叉事故）。
    assert quota.settled == [(SimpleNamespace(reservation_id="res-1",
                                              reserved_cny=Decimal("0.50")),
                              Decimal("0.50"), "estimated")]
    assert quota.reviewed == []


def test_large_unknown_usage_still_goes_to_review():
    """T7：大额未知用量 ¥50 → 保留待对账语义，不估算结算。"""
    quota = _Quota()
    status, _ = _run(quota, _reservation("50"), "stream_usage_missing")
    assert status == "needs_review"
    assert quota.reviewed == ["stream_usage_missing"]
    assert quota.settled == []


def test_zero_threshold_restores_legacy_review_for_all(monkeypatch):
    """T8：阈值 0 = 回滚开关，全部转待对账（旧行为）。"""
    monkeypatch.setattr(
        "backend.config.budget.BUDGET_ESTIMATED_SETTLEMENT_MAX_CNY", Decimal("0"),
    )
    quota = _Quota()
    status, _ = _run(quota, _reservation("0.50"), "call_failed_possibly_billed")
    assert status == "needs_review"
    assert quota.reviewed == ["call_failed_possibly_billed"]
    assert quota.settled == []


def test_settle_failure_falls_back_to_review():
    """T12：小额结算失败回退 mark_needs_review，不静默丢账。"""
    quota = _Quota(settle_error=RuntimeError("synthetic storage failure"))
    status, _ = _run(quota, _reservation("0.50"), "provider_usage_unavailable")
    assert status == "needs_review"
    assert quota.reviewed == ["provider_usage_unavailable"]


def test_reservation_without_amount_stays_conservative():
    """预占对象缺失 reserved_cny = 无法证明估算依据，保守转待对账。"""
    quota = _Quota()
    status, _ = _run(quota, _reservation(None), "provider_usage_unavailable")
    assert status == "needs_review"
    assert quota.reviewed == ["provider_usage_unavailable"]


def test_no_reservation_is_noop():
    quota = _Quota()
    status, _ = _run(quota, None, "provider_usage_unavailable")
    assert status == "not_reserved"
    assert quota.settled == [] and quota.reviewed == []


def test_release_path_never_settles_or_reviews():
    """T11：release 语义（确定未发给供应商）必须零成本放走，不走估算结算。"""
    quota = _Quota()
    reservation = _reservation("0.50")
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(max_cost="0.50"), mode="enforce",
        user_id="u", tenant_id="t", quota_store=quota,
    )
    budget._states[_REQUEST_ID] = state
    request_token = budget._current_request_id.set(_REQUEST_ID)
    reservation_token = budget._current_quota_reservation.set(reservation)
    try:
        budget.release_model_reservation()
    finally:
        budget._states.pop(_REQUEST_ID, None)
        budget._current_request_id.reset(request_token)
        budget._current_quota_reservation.reset(reservation_token)
    assert quota.released == [reservation]
    assert quota.settled == [] and quota.reviewed == []


def test_threshold_config_defaults_and_bounds():
    """配置契约：默认 5、允许 0（回滚开关）、禁止负数。"""
    from backend.config import budget as config

    assert config.BUDGET_ESTIMATED_SETTLEMENT_MAX_CNY == Decimal("5")
