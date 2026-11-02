"""整批金额上限的真实PG并发验证；禁止连接共享业务库。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import os
from threading import Barrier
from uuid import uuid4

import psycopg2
import pytest

from backend.infra.llm.quota import (
    BudgetPolicy, PostgresQuotaStore, QuotaExceeded, ResolvedBudgetPolicies,
)


@pytest.fixture
def store(monkeypatch):
    from backend.config.database import MEMORY_DB_CONFIG

    dsn = os.getenv('CS_Q3_PG_DSN')
    parameters = psycopg2.extensions.parse_dsn(dsn) if dsn else MEMORY_DB_CONFIG
    database = parameters.get('dbname', '')
    assert database == 'cs_q3' or database.endswith('_test'), '必须使用隔离测试库'

    @contextmanager
    def connect():
        connection = psycopg2.connect(**parameters)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    instance = PostgresQuotaStore(connection_factory=connect)
    policy = BudgetPolicy('platform', 'default', Decimal('100'), Decimal('1000'))
    monkeypatch.setattr(instance, 'resolve', lambda *args: ResolvedBudgetPolicies(policy, policy))
    return instance


def test_run_cap_serializes_concurrent_reservations_across_users(store):
    run = f'run-cap-{uuid4().hex}'
    barrier = Barrier(2)

    def reserve(index):
        barrier.wait(timeout=5)
        try:
            return store.reserve(
                user_id=f'{run}-user-{index}', tenant_id=f'{run}-tenant-{index}',
                amount_cny=Decimal('0.06'), request_id=run,
                request_limit_cny=Decimal('0.10'),
            )
        except QuotaExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [1, 2]))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 1
    store.release(accepted[0])


def test_run_cap_counts_settled_and_review_but_releases_unused_money(store):
    run = f'run-cap-{uuid4().hex}'

    def reserve(amount):
        return store.reserve(
            user_id=run, tenant_id=run, amount_cny=Decimal(amount),
            request_id=run, request_limit_cny=Decimal('0.10'),
        )

    settled = reserve('0.06')
    store.settle(settled, Decimal('0.04'))
    pending = reserve('0.06')
    store.mark_needs_review(pending, 'unknown_provider_cost')
    with pytest.raises(QuotaExceeded):
        reserve('0.01')
    other = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('0.10'),
        request_id=run + '-other', request_limit_cny=Decimal('0.10'),
    )
    store.release(other)
