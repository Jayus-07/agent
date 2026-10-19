"""请求已结算成本不得再次计入后续预占；未决调用仍占用硬额度。"""
import threading
from decimal import Decimal

import pytest

from backend.infra.llm.budget import (
    RequestBudget,
    RequestBudgetExceeded,
    RequestBudgetLimits,
)
from backend.infra.llm.quota import QuotaExceeded


class _HardQuota:
    """模拟0.50元日/月硬限；包含已花费及尚未结算的预占。"""

    def __init__(self, used="0.107015"):
        self.used = Decimal(used)
        self.pending = {}
        self.lock = threading.Lock()
        self.counter = 0

    def reserve(self, *, amount_cny, **_kwargs):
        with self.lock:
            held = sum(self.pending.values(), Decimal(0))
            if self.used + held + amount_cny > Decimal("0.50"):
                raise QuotaExceeded("user:eval", "day")
            self.counter += 1
            self.pending[self.counter] = amount_cny
            return self.counter

    def settle(self, reservation, cost):
        with self.lock:
            del self.pending[reservation]
            self.used += Decimal(cost)


def _budget(quota, max_cost="0.392985"):
    return RequestBudget(
        RequestBudgetLimits(max_cost=max_cost), mode="enforce",
        user_id="eval-user", tenant_id="eval", quota_store=quota,
    )


@pytest.mark.parametrize("kind", ["primary", "retry", "fallback"])
def test_settled_call_does_not_block_next_call_below_hard_limit(kind):
    """复现本机Smoke：首条花费0.000002元后，第二条仍有合法预算。"""
    quota = _HardQuota()
    budget = _budget(quota)
    first = budget.reserve("primary")
    quota.settle(first, "0.000002")
    budget.record_usage(total_tokens=4, cost="0.000002")

    second = budget.reserve(kind)
    quota.settle(second, "0.000003")
    budget.record_usage(total_tokens=6, cost="0.000003")

    assert budget.snapshot().calls == 2
    assert quota.used == Decimal("0.107020")
    assert quota.pending == {}


def test_inflight_reservation_still_blocks_competing_call():
    """不按预计单价缩小未决预占，也不释放未知usage来换取并发。"""
    quota = _HardQuota()
    budget = _budget(quota)
    first = budget.reserve("primary")
    quota.settle(first, "0.000002")
    budget.record_usage(cost="0.000002")
    pending = budget.reserve("primary")

    with pytest.raises(QuotaExceeded):
        budget.reserve("primary")

    assert pending in quota.pending
    assert budget.snapshot().calls == 2
    assert quota.used + sum(quota.pending.values()) == Decimal("0.50")


def test_request_cost_limit_still_blocks_before_quota_reservation():
    quota = _HardQuota(used="0")
    budget = _budget(quota, max_cost="0.10")
    first = budget.reserve("primary")
    quota.settle(first, "0.10")
    budget.record_usage(cost="0.10")

    with pytest.raises(RequestBudgetExceeded, match="request_cost"):
        budget.reserve("primary")

    assert budget.snapshot().calls == 1
    assert quota.pending == {}
