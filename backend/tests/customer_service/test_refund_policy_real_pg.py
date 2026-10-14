"""退款政策与候选查询的真实 PostgreSQL 集成验收。"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from psycopg2.pool import ThreadedConnectionPool

pytestmark = pytest.mark.pg


def test_refund_policy_and_candidates_against_isolated_postgres(monkeypatch):
    """候选 SQL、资格查询和重复退款闸门须通过真实只读执行器。"""
    dsn = os.getenv("REFUND_POLICY_PG_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("设置 REFUND_POLICY_PG_TEST_DSN 后运行隔离 PostgreSQL 验收")

    admin = psycopg2.connect(dsn)
    cursor = admin.cursor()
    cursor.execute("SELECT current_database()")
    database_name = str(cursor.fetchone()[0]).lower()
    if not (
        database_name.endswith("_test")
        or database_name.startswith("test_")
        or "sandbox" in database_name
    ):
        admin.close()
        pytest.fail(
            "退款 PG 验收只允许名称明确标记为 test 或 sandbox 的隔离数据库"
        )

    suffix = uuid.uuid4().hex[:12]
    prefix = f"REFUND-PG-{suffix}-"
    user_id = f"refund-pg-user-{suffix}"

    def order_no(label: str) -> str:
        return f"{prefix}{label.replace('_', '-')}"

    rows: dict[str, int] = {}
    pool: ThreadedConnectionPool | None = None
    try:
        with admin:
            with admin.cursor() as setup:
                setup.execute('CREATE SCHEMA IF NOT EXISTS "order"')
                setup.execute("CREATE SCHEMA IF NOT EXISTS product")
                setup.execute(
                    '''CREATE TABLE IF NOT EXISTS "order".orders (
                        id BIGSERIAL PRIMARY KEY,
                        order_no TEXT NOT NULL UNIQUE,
                        customer_id TEXT NOT NULL,
                        total_amount NUMERIC NOT NULL,
                        status TEXT NOT NULL,
                        payment_status TEXT NOT NULL,
                        created_at TIMESTAMPTZ
                    )'''
                )
                setup.execute(
                    '''CREATE TABLE IF NOT EXISTS product.products (
                        id BIGSERIAL PRIMARY KEY,
                        product_name TEXT NOT NULL
                    )'''
                )
                setup.execute(
                    '''CREATE TABLE IF NOT EXISTS "order".order_items (
                        id BIGSERIAL PRIMARY KEY,
                        order_id BIGINT NOT NULL,
                        product_id BIGINT NOT NULL,
                        quantity INTEGER NOT NULL,
                        price NUMERIC NOT NULL,
                        cost NUMERIC NOT NULL
                    )'''
                )
                setup.execute(
                    '''CREATE TABLE IF NOT EXISTS "order".refunds (
                        id BIGSERIAL PRIMARY KEY,
                        order_id BIGINT NOT NULL
                    )'''
                )

                now = datetime.now(timezone.utc)
                cases = {
                    "inside": ("paid", now - timedelta(days=5)),
                    "boundary_inside": (
                        "paid", now - timedelta(days=7) + timedelta(minutes=2),
                    ),
                    "outside": (
                        "paid", now - timedelta(days=7) - timedelta(minutes=2),
                    ),
                    "wrong_status": ("cancelled", now - timedelta(days=1)),
                    "missing_time": ("paid", None),
                    "duplicate": ("paid", now - timedelta(days=1)),
                }
                for label, (status, created_at) in cases.items():
                    setup.execute(
                        '''INSERT INTO "order".orders
                            (order_no, customer_id, total_amount, status,
                             payment_status, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           RETURNING id''',
                        (
                            order_no(label), user_id, 49.95, status,
                            "paid", created_at,
                        ),
                    )
                    rows[label] = int(setup.fetchone()[0])

                setup.execute(
                    "INSERT INTO product.products (product_name) VALUES (%s) "
                    "RETURNING id",
                    (f"退款 PG 商品 {suffix}",),
                )
                product_id = int(setup.fetchone()[0])
                for order_id in rows.values():
                    setup.execute(
                        '''INSERT INTO "order".order_items
                            (order_id, product_id, quantity, price, cost)
                           VALUES (%s, %s, 1, 49.95, 25.00)''',
                        (order_id, product_id),
                    )
                setup.execute(
                    'INSERT INTO "order".refunds (order_id) VALUES (%s)',
                    (rows["duplicate"],),
                )

        pool = ThreadedConnectionPool(
            minconn=1, maxconn=2, dsn=dsn,
            application_name="refund_policy_isolated_pg_test",
        )
        from backend.sql import executor

        monkeypatch.setattr(executor, "_get_pool", lambda: pool)
        monkeypatch.setattr(
            "backend.config.customer_service.CS_BUSINESS_GATEWAY_MODE",
            "sandbox",
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_REFUND_POLICY_VERSION",
            "refund-pg-test-v1",
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_REFUND_WINDOW_DAYS", "7",
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_REFUNDABLE_STATUSES", "paid",
        )

        from backend.customer_service.errors import OrderNotEligibleError
        from backend.customer_service.service.refund_service import RefundService

        service = RefundService()
        candidates = service.list_refund_candidates(user_id)
        candidate_numbers = {item["order_no"] for item in candidates["candidates"]}
        assert candidates["window_days"] == 7
        assert candidates["policy_version"] == "refund-pg-test-v1"
        assert candidate_numbers == {
            order_no("inside"), order_no("boundary_inside"),
        }

        assert service.check_refund_eligibility(
            user_id, order_no("inside"),
        ).eligible is True
        assert service.check_refund_eligibility(
            user_id, order_no("boundary_inside"),
        ).eligible is True
        with pytest.raises(OrderNotEligibleError, match="超过 7 天"):
            service.check_refund_eligibility(user_id, order_no("outside"))
        with pytest.raises(OrderNotEligibleError, match="不允许退款"):
            service.check_refund_eligibility(user_id, order_no("wrong_status"))
        with pytest.raises(OrderNotEligibleError, match="下单时间"):
            service.check_refund_eligibility(user_id, order_no("missing_time"))
        with pytest.raises(OrderNotEligibleError, match="已存在退款记录"):
            service.check_refund_eligibility(user_id, order_no("duplicate"))
        assert service.list_refund_candidates(
            f"another-user-{suffix}",
        )["candidates"] == []
    finally:
        if pool is not None:
            pool.closeall()
        try:
            with admin:
                with admin.cursor() as cleanup:
                    cleanup.execute(
                        'DELETE FROM "order".refunds WHERE order_id = ANY(%s)',
                        (list(rows.values()),),
                    )
                    cleanup.execute(
                        'DELETE FROM "order".order_items WHERE order_id = ANY(%s)',
                        (list(rows.values()),),
                    )
                    cleanup.execute(
                        'DELETE FROM "order".orders WHERE order_no LIKE %s',
                        (f"{prefix}%",),
                    )
                    cleanup.execute(
                        "DELETE FROM product.products WHERE product_name = %s",
                        (f"退款 PG 商品 {suffix}",),
                    )
        finally:
            admin.close()
