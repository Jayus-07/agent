"""Q4/Q8 预算策略、周期和价格表纯逻辑契约。"""

from decimal import Decimal
from datetime import datetime, timezone

import pytest


def _policy(scope_type, scope_id, daily, monthly, enforcement="hard", **kwargs):
    from backend.infra.llm.quota import BudgetPolicy

    return BudgetPolicy(
        scope_type=scope_type,
        scope_id=scope_id,
        daily_limit_cny=Decimal(str(daily)),
        monthly_limit_cny=Decimal(str(monthly)),
        enforcement=enforcement,
        **kwargs,
    )


def test_policy_inheritance_user_then_tenant_then_platform():
    from backend.infra.llm.quota import resolve_budget_policies

    platform = _policy("platform", "default", 20, 300)
    tenant_default = _policy("tenant_default", "default", 60, 1000)
    tenant = _policy("tenant", "tenant-a", 80, 1200)
    user = _policy("user", "user-a", 3, 50)

    resolved = resolve_budget_policies(
        user_id="user-a",
        tenant_id="tenant-a",
        platform_default=platform,
        tenant_default=tenant_default,
        tenant=tenant,
        user=user,
    )
    assert resolved.user == user
    assert resolved.tenant == tenant

    inherited = resolve_budget_policies(
        user_id="user-b",
        tenant_id="tenant-a",
        platform_default=platform,
        tenant_default=tenant_default,
        tenant=tenant,
        user=None,
    )
    assert inherited.user == tenant
    assert inherited.tenant == tenant

    platform_fallback = resolve_budget_policies(
        user_id="user-c",
        tenant_id="tenant-c",
        platform_default=platform,
        tenant_default=None,
        tenant=None,
        user=None,
    )
    assert platform_fallback.user == platform
    assert platform_fallback.tenant == platform


def test_budget_periods_use_shanghai_midnight_and_utc_storage():
    from backend.infra.llm.quota import budget_periods

    now = datetime(2026, 9, 18, 16, 30, tzinfo=timezone.utc)
    periods = budget_periods(now, "Asia/Shanghai")
    assert periods.day_start_utc.isoformat() == "2026-09-18T16:00:00+00:00"
    assert periods.month_start_utc.isoformat() == "2026-08-31T16:00:00+00:00"


def test_price_table_calculates_six_dimensions_to_six_places():
    from backend.infra.llm.pricing import PriceTable

    table = PriceTable.from_rows([
        {"model_name": "m1", "component": "llm", "dimension": "input", "price_per_unit": "1.000000"},
        {"model_name": "m1", "component": "llm", "dimension": "output", "price_per_unit": "2.000000"},
        {"model_name": "m1", "component": "llm", "dimension": "cache_read", "price_per_unit": "0.100000"},
        {"model_name": "m1", "component": "llm", "dimension": "cache_write", "price_per_unit": "0.200000"},
        {"model_name": "m1", "component": "llm", "dimension": "reasoning", "price_per_unit": "3.000000"},
        {"model_name": "m1", "component": "llm", "dimension": "tool_call", "price_per_unit": "0.050000", "unit": "per_call"},
    ])
    cost = table.calculate_cost(
        "m1",
        "llm",
        {"input": 1000, "output": 500, "cache_read": 100,
         "cache_write": 50, "reasoning": 20, "tool_call": 2},
    )
    assert cost == Decimal("0.102080")


def test_missing_price_is_hard_failure_but_observe_only_alerts():
    from backend.infra.llm.pricing import MissingModelPrice, PriceTable

    table = PriceTable.from_rows([])
    with pytest.raises(MissingModelPrice):
        table.require("missing", "llm", enforce=True)
    assert table.require("missing", "llm", enforce=False) is None


def test_postgres_quota_settlement_is_idempotent_for_same_reservation():
    """迟到处理重放时，同一 reservation 只能把 used_cny 增加一次。"""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from backend.infra.llm.quota import PostgresQuotaStore

    state = {
        "status": "reserved", "ledger_updates": 0, "settled_amount": None,
        "sql": [],
    }

    class _Cursor:
        def __init__(self):
            self._rows = []
            self._one = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            normalized = " ".join(sql.lower().split())
            state["sql"].append(normalized)
            self._rows = []
            self._one = None
            if normalized.startswith("select scope_type"):
                assert "where id = %s and status = 'reserved'" in normalized
                assert "for update" in normalized
                if state["status"] == "reserved":
                    self._rows = [(
                        "user", "fake-user", "day", "2026-10-18",
                        Decimal("0.500000"),
                    )]
            elif normalized.startswith("update budget_ledger"):
                state["ledger_updates"] += 1
            elif normalized.startswith("update budget_reservations"):
                assert "where id = %s and status = 'reserved'" in normalized
                if state["status"] == "reserved":
                    state["status"] = "settled"
                    state["settled_amount"] = params[0]

        def fetchall(self):
            return list(self._rows)

        def fetchone(self):
            return self._one

    class _Connection:
        def cursor(self):
            return _Cursor()

    @contextmanager
    def connection_factory():
        yield _Connection()

    store = PostgresQuotaStore(connection_factory=connection_factory)
    reservation = SimpleNamespace(reservation_id="stable-reservation-id")
    store.settle(reservation, Decimal("0.123456"))
    store.settle(reservation, Decimal("0.123456"))

    assert state["status"] == "settled"
    assert state["ledger_updates"] == 1
    assert state["settled_amount"] == Decimal("0.123456")
    assert sum(sql.startswith("update budget_ledger") for sql in state["sql"]) == 1


def test_quota_manager_hard_soft_and_audit_modes():
    from backend.infra.llm.quota import QuotaExceeded, QuotaManager

    hard = _policy("tenant", "tenant-a", "1", "10", "hard")
    manager = QuotaManager.from_policies(hard)
    manager.record("tenant-a", "user-a", Decimal("0.9"))
    with pytest.raises(QuotaExceeded):
        manager.reserve("tenant-a", "user-a", Decimal("0.2"))

    soft = _policy("tenant", "tenant-b", "1", "10", "soft")
    soft_manager = QuotaManager.from_policies(soft)
    soft_manager.record("tenant-b", "user-b", Decimal("0.9"))
    assert soft_manager.reserve("tenant-b", "user-b", Decimal("0.2")).blocked is False

    audit = _policy("tenant", "test-tenant", "1", "10", "audit", audit_exempt=True)
    audit_manager = QuotaManager.from_policies(audit)
    audit_manager.record("test-tenant", "user-c", Decimal("2"))
    reservation = audit_manager.reserve("test-tenant", "user-c", Decimal("2"))
    assert reservation.blocked is False
    assert reservation.audit_exempt is True
