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
                   (scope_type, scope_id, daily_limit_cny, monthly_limit_cny,
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
            # 账本键拆分后用户层落在 ("user", "quota-user-<token>")，一并清理
            cur.execute(
                "DELETE FROM budget_ledger WHERE scope_type = 'user' "
                "AND scope_id LIKE 'quota-user-%'",
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
        user_id="quota-user-" + pg_quota_policy,
        tenant_id=pg_quota_policy,
        amount_cny=Decimal("0"),
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
            user_id="quota-user-" + pg_quota_policy,
            tenant_id=pg_quota_policy,
            amount_cny=Decimal("0.020000"),
            request_id="quota-pg-test-2",
        )


def test_concurrent_reserve_and_settle_never_deadlock(pg_quota_policy):
    """回归：reserve 与 settle/release 并发不得死锁。

    2026-10-29 生产实测 21 次 deadlock：reserve 按 user→tenant 顺序 upsert
    账本行，而 settle/release 的 reservations 行读取无 ORDER BY，按索引
    （tenant 在前）更新账本行 → 双向互持行锁。修复后所有路径统一按
    user→tenant、day→month 加锁，锁序一致则 AB-BA 结构上不可能。
    """
    from concurrent.futures import ThreadPoolExecutor

    from backend.infra.llm.quota import PostgresQuotaStore

    store = PostgresQuotaStore()
    user_id = "quota-user-" + pg_quota_policy
    rounds = 40
    errors = []

    def _reserve_then_release():
        for i in range(rounds):
            try:
                r = store.reserve(
                    user_id=user_id, tenant_id=pg_quota_policy,
                    amount_cny=Decimal("0.001"),
                    request_id=f"dlock-rel-{i}",
                )
                store.release(r)
            except Exception as exc:  # 死锁被包装为 QuotaConfigurationError
                errors.append(f"release#{i}: {exc}")
                return

    def _reserve_then_settle():
        for i in range(rounds):
            try:
                r = store.reserve(
                    user_id=user_id, tenant_id=pg_quota_policy,
                    amount_cny=Decimal("0.001"),
                    request_id=f"dlock-set-{i}",
                )
                store.settle(r, Decimal("0.001"))
            except Exception as exc:
                errors.append(f"settle#{i}: {exc}")
                return

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_reserve_then_release), pool.submit(_reserve_then_settle)]
        for f in futures:
            f.result(timeout=120)

    assert errors == [], f"并发预占/结算出现失败: {errors}"
