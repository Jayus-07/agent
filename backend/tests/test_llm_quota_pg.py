"""Q4 PG 预算原子预占与结算集成回归。"""

from decimal import Decimal
import uuid

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG


@pytest.fixture()
def pg_quota_policy():
    tenant_id = f"quota-test-{uuid.uuid4().hex[:10]}"
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO budget_policies
                   (scope_type, scope_id, daily_limit_usd, monthly_limit_usd,
                    enforcement, timezone, updated_by)
                   VALUES ('tenant', %s, 0.100000, 0.100000,
                           'hard', 'Asia/Shanghai', 'test')""",
                (tenant_id,),
            )
    yield tenant_id
    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM budget_reservations WHERE tenant_id = %s",
                (tenant_id,),
            )
            cur.execute(
                "DELETE FROM budget_events WHERE scope_type = 'tenant' AND scope_id = %s",
                (tenant_id,),
            )
            cur.execute(
                "DELETE FROM budget_ledger WHERE scope_type = 'tenant' AND scope_id = %s",
                (tenant_id,),
            )
            cur.execute(
                "DELETE FROM budget_policies WHERE scope_type = 'tenant' AND scope_id = %s",
                (tenant_id,),
            )


def test_pg_quota_hard_limit_is_atomic_and_settled(pg_quota_policy):
    from backend.infra.llm.quota import PostgresQuotaStore, QuotaExceeded

    store = PostgresQuotaStore()
    reservation = store.reserve(
        user_id="quota-user",
        tenant_id=pg_quota_policy,
        amount_usd=Decimal("0"),
        request_id="quota-pg-test",
    )
    store.settle(reservation, Decimal("0.100000"))

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT threshold FROM budget_events
                   WHERE scope_type = 'tenant' AND scope_id = %s
                   ORDER BY threshold""",
                (pg_quota_policy,),
            )
            assert {row[0] for row in cur.fetchall()} == {
                Decimal("0.8000"), Decimal("1.0000")
            }

    with pytest.raises(QuotaExceeded):
        store.reserve(
            user_id="quota-user",
            tenant_id=pg_quota_policy,
            amount_usd=Decimal("0.020000"),
            request_id="quota-pg-test-2",
        )
