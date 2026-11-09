"""summary() 未结算预留口径（2026-11-10 待对账治理 1.4）：按预占单去重。

同一笔预占在 user/tenant × day/month 的 budget_ledger 上是 4 行同值，
旧实现行级 SUM 把占额放大数倍（实测 ¥78.60 显示成 ¥228）。修复后
`unsettled_reserved` = 在途预占（reserved）+ 待对账占额（needs_review），
逐单只算一次，与 reconciliation_summary 口径一致。

断言一律用前后增量：隔离测试库可能有历史会话留下的占额残留，
绝对断言会被无关数据污染。
"""
from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import psycopg2
import pytest

from backend.infra.llm.quota import (
    BudgetPolicy,
    PostgresQuotaStore,
    ResolvedBudgetPolicies,
)


@pytest.fixture
def store(monkeypatch):
    from backend.config.database import MEMORY_DB_CONFIG

    parameters = dict(MEMORY_DB_CONFIG)
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
    monkeypatch.setattr(
        instance, 'resolve', lambda *args: ResolvedBudgetPolicies(policy, policy),
    )
    return instance


def test_summary_counts_reservation_once_across_ledger_rows(store):
    """T9：一笔预占写 4 行账本（user/tenant × day/month），summary 只算 ¥1。"""
    run = f'sum-dedup-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    before = unsettled()
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('1.00'),
    )
    in_flight = unsettled() - before
    assert in_flight == Decimal('1.00')

    store.settle(reservation, Decimal('0'))
    assert unsettled() - before == Decimal('0')


def test_summary_includes_needs_review_held_amount(store):
    """T9 补充：待对账（needs_review）占额同样逐单只算一次。"""
    run = f'sum-review-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    before = unsettled()
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('2.00'),
    )
    store.mark_needs_review(reservation, 'unknown_provider_cost')
    assert unsettled() - before == Decimal('2.00')


def test_summary_matches_reconciliation_held_amount(store):
    """T10：summary 未结算预留增量 == reconciliation 待对账占额增量。

    静息增量下两者应严格相等（同一去重口径的两个出口）。
    """
    run = f'sum-recon-{uuid4().hex}'

    def unsettled() -> Decimal:
        return Decimal(store.summary()['unsettled_reserved'])

    def recon_held() -> Decimal:
        return Decimal(store.reconciliation_summary()['held_cny'])

    summary_before, recon_before = unsettled(), recon_held()
    reservation = store.reserve(
        user_id=run, tenant_id=run, amount_cny=Decimal('3.50'),
    )
    store.mark_needs_review(reservation, 'unknown_provider_cost')
    assert unsettled() - summary_before == Decimal('3.50')
    assert recon_held() - recon_before == Decimal('3.50')
