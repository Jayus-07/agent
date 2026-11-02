from contextvars import ContextVar
from uuid import uuid4

import pytest

from backend.infra.async_utils import sync_call_with_timeout
from backend.infra.llm import budget


def test_timeout_worker_inherits_and_restores_callers_context():
    identity = ContextVar('timeout_test_identity', default='none')
    for value in ('tenant-a', 'tenant-b'):
        token = identity.set(value)
        try:
            assert sync_call_with_timeout(identity.get, 2) == value
            sync_call_with_timeout(identity.set, 2, 'child-only')
            assert identity.get() == value
        finally:
            identity.reset(token)
    assert sync_call_with_timeout(identity.get, 2) == 'none'


def test_timeout_worker_keeps_shared_budget_and_blocks_before_provider(monkeypatch):
    monkeypatch.setattr(budget, '_config_mode', lambda: 'enforce')
    monkeypatch.setattr(budget, '_config_limits', lambda: budget.RequestBudgetLimits(
        max_total_tokens=1,
    ))
    with budget.scoped_request_budget(f'cs-timeout-{uuid4().hex}') as state:
        assert sync_call_with_timeout(budget.current_request_budget, 2) is state
        state.record_usage(prompt_tokens=1, total_tokens=1)
        with pytest.raises(budget.RequestBudgetExceeded, match='request_tokens'):
            sync_call_with_timeout(budget.reserve_model_call, 2, 'primary')


def test_run_budget_survives_nested_request_rebind_and_restores(monkeypatch):
    from decimal import Decimal

    from backend.observability.llm_usage_store import current_usage_attribution

    monkeypatch.setattr(budget, '_config_mode', lambda: 'observe')
    assert budget.current_run_budget() is None
    with budget.scoped_run_budget('eval:run-scope-test', Decimal('10')):
        with budget.scoped_request_budget('child-trace'):
            assert current_usage_attribution()['request_id'] == 'eval:run-scope-test'
            assert sync_call_with_timeout(budget.current_run_budget, 2) == (
                'eval:run-scope-test', Decimal('10'),
            )
    assert budget.current_run_budget() is None


def test_run_scope_is_passed_to_existing_quota_store(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from backend.infra.llm import pricing

    captured = []
    store = SimpleNamespace(reserve=lambda **kwargs: captured.append(kwargs))
    table = SimpleNamespace(require=lambda *args, **kwargs: None)
    monkeypatch.setattr(pricing, 'get_current_price_table', lambda *args: table)
    state = budget.RequestBudget(
        budget.RequestBudgetLimits(), mode='enforce', user_id='actor',
        tenant_id='tenant', quota_store=store,
    )
    with budget.scoped_run_budget('eval:one-run', Decimal('10')):
        state.reserve('primary', model_name='__run_scope_model__')
    assert captured[0]['request_id'] == 'eval:one-run'
    assert captured[0]['request_limit_cny'] == Decimal('10')


def test_child_quota_denial_cannot_be_hidden_by_request_context_reset(monkeypatch):
    from decimal import Decimal

    from backend.infra.llm.quota import QuotaExceeded

    monkeypatch.setattr(budget, '_config_mode', lambda: 'observe')
    denial = QuotaExceeded('request:eval:blocked', 'run')

    def child():
        with budget.scoped_request_budget('child-budget'):
            budget.note_quota_blocked(denial)

    with budget.scoped_run_budget('eval:blocked', Decimal('10')):
        sync_call_with_timeout(child, 2)
        with pytest.raises(QuotaExceeded):
            budget.check_quota_blocked()
