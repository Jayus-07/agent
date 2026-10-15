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
    customer_id: int | None = None
    product_id: int | None = None
    user_id = ""

    def order_no(label: str) -> str:
        return f"{prefix}{label.replace('_', '-')}"

    rows: dict[str, int] = {}
    fixture_schema_ready = False
    pool: ThreadedConnectionPool | None = None
    try:
        with admin:
            with admin.cursor() as setup:
                setup.execute(
                    """SELECT
                        EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'order' AND table_name = 'orders'
                              AND column_name = 'customer_id' AND data_type = 'integer'
                        ),
                        EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'product' AND table_name = 'products'
                              AND column_name = 'sku' AND is_nullable = 'NO'
                        ),
                        EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'product' AND table_name = 'products'
                              AND column_name = 'product_name' AND is_nullable = 'NO'
                        ),
                        EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'customer' AND table_name = 'customers'
                              AND column_name = 'id' AND data_type = 'integer'
                        ),
                        to_regclass('"order".order_items') IS NOT NULL,
                        to_regclass('"order".refunds') IS NOT NULL"""
                )
                if not all(setup.fetchone()):
                    pytest.fail(
                        "退款 PG 验收要求先运行仓库业务迁移；未找到正式的 "
                        "orders.customer_id INTEGER / products.sku NOT NULL / "
                        "products.product_name NOT NULL 契约"
                    )
                fixture_schema_ready = True

                setup.execute(
                    "INSERT INTO customer.customers (name) VALUES (%s) RETURNING id",
                    (f"退款 PG 测试客户 {suffix}",),
                )
                customer_id = int(setup.fetchone()[0])
                user_id = str(customer_id)

                setup.execute(
                    "INSERT INTO product.products (sku, product_name) "
                    "VALUES (%s, %s) RETURNING id",
                    (f"REFUND-PG-{suffix}", f"退款 PG 商品 {suffix}"),
                )
                product_id = int(setup.fetchone()[0])

                setup.execute("SET LOCAL TIME ZONE 'UTC'")
                now = datetime.now(timezone.utc).replace(tzinfo=None)
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
                            order_no(label), customer_id, 49.95, status,
                            "paid", created_at,
                        ),
                    )
                    rows[label] = int(setup.fetchone()[0])

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
            options="-c timezone=UTC",
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
            if fixture_schema_ready:
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
                        if product_id is not None:
                            cleanup.execute(
                                "DELETE FROM product.products WHERE id = %s",
                                (product_id,),
                            )
                        if customer_id is not None:
                            cleanup.execute(
                                "DELETE FROM customer.customers WHERE id = %s",
                                (customer_id,),
                            )
        finally:
            admin.close()
