"""退款申请案件事实源的隔离 PostgreSQL 集成验收。"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.pg


def test_refund_request_persists_idempotently_with_owner_scope(monkeypatch):
    """实际 cs_case 唯一索引负责重放与并发冲突，不触碰业务退款表。"""
    dsn = os.getenv("REFUND_REQUEST_PG_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("设置 REFUND_REQUEST_PG_TEST_DSN 后运行隔离 PostgreSQL 验收")

    admin = psycopg2.connect(dsn)
    with admin.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database_name = str(cursor.fetchone()[0]).lower()
    if not (
        database_name.endswith("_test")
        or database_name.startswith("test_")
        or "sandbox" in database_name
    ):
        admin.close()
        pytest.fail("退款申请 PG 验收只允许名称明确标记为 test 或 sandbox 的隔离数据库")

    suffix = uuid.uuid4().hex[:12]
    user_id = f"refund-request-user-{suffix}"
    tenant_id = f"refund-request-tenant-{suffix}"
    conversation_id = f"refund-request-conversation-{suffix}"
    confirmation_id = str(uuid.uuid4())
    other_confirmation_id = str(uuid.uuid4())
    pooler = None
    engine = None
    try:
        migration = (
            Path(__file__).resolve().parents[2]
            / "sql" / "migrations" / "058_cs_case.sql"
        ).read_text(encoding="utf-8")
        with admin:
            with admin.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS customer_service")
                cursor.execute(migration)

        async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

        async def create_async_database():
            nonlocal engine
            engine = create_async_engine(async_dsn, pool_pre_ping=True)
            return async_sessionmaker(engine, expire_on_commit=False)

        from backend.customer_service._db_loop import run_sync
        from backend.memory import database

        pooler = run_sync(create_async_database())
        monkeypatch.setattr(database, "AsyncSessionLocal", pooler)
        monkeypatch.setattr(
            "backend.config.customer_service.CS_REFUND_REQUEST_MODE", "sandbox",
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_BUSINESS_GATEWAY_MODE", "sandbox",
        )

        from backend.customer_service.case.service import get_case_service
        from backend.customer_service.errors import RefundRequestConflictError
        from backend.customer_service.service.refund_service import (
            RefundEligibility,
            RefundService,
        )

        service = RefundService()
        monkeypatch.setattr(
            service,
            "check_refund_eligibility",
            lambda *_: RefundEligibility(
                True, "order-42", "ORD-0042", 49.95, "paid",
                policy_version="refund-pg-test-v1",
            ),
        )
        pending = {
            "action_type": "refund_request",
            "target_type": "order",
            "target_id": "order-42",
            "before_state": {
                "order_id": "order-42", "status": "paid", "amount": 49.95,
                "policy_version": "refund-pg-test-v1",
            },
            "after_state": {"reason": "商品破损", "status": "refund_requested"},
        }
        fields = {
            "confirmation_id": confirmation_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
        }

        first = service.execute_refund_request(pending, **fields)
        replay = service.execute_refund_request(pending, **fields)
        assert first.data["request"] == replay.data["request"]
        assert first.data["funds_refunded"] is False
        assert first.data["request"]["status"] == "pending_review"

        case_service = get_case_service()
        own = run_sync(case_service.async_get_refund_request(
            confirmation_id, user_id=user_id, tenant_id=tenant_id,
        ))
        foreign_user = run_sync(case_service.async_get_refund_request(
            confirmation_id, user_id=f"other-{user_id}", tenant_id=tenant_id,
        ))
        foreign_tenant = run_sync(case_service.async_get_refund_request(
            confirmation_id, user_id=user_id, tenant_id=f"other-{tenant_id}",
        ))
        assert own["request_id"] == confirmation_id
        assert foreign_user is None
        assert foreign_tenant is None
        assert run_sync(case_service.async_list_refund_requests(
            user_id=user_id, tenant_id=tenant_id,
        )) == [own]

        from backend.customer_service.business_guard import (
            BusinessOperationAlreadyActive,
            BusinessOperationIdentity,
        )
        from backend.customer_service.confirmation_store import (
            _raise_on_active_conflict,
        )

        class EmptyConfirmationRepository:
            async def find_by_identity(self, **_filters):
                return None

        identity = BusinessOperationIdentity(
            tenant_id=tenant_id,
            action_type="refund_request",
            target_type="order",
            target_id="order-42",
            semantic_fingerprint="f" * 64,
        )

        async def guard_existing_active_case():
            async with pooler() as db:
                await _raise_on_active_conflict(
                    db, EmptyConfirmationRepository(), identity,
                    user_id=user_id,
                    exclude_confirmation_id="",
                    completed_message="不可重复申请",
                )

        with pytest.raises(BusinessOperationAlreadyActive):
            run_sync(guard_existing_active_case())

        with pytest.raises(RefundRequestConflictError):
            service.execute_refund_request(
                pending,
                **{**fields, "confirmation_id": other_confirmation_id},
            )

        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "UPDATE customer_service.cs_case SET status = 'closed' "
                    "WHERE case_id = %s",
                    (confirmation_id,),
                )

        # 待审核案件结案后，旧 confirmation success 不再把新申请永久锁死。
        run_sync(guard_existing_active_case())

        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM customer_service.cs_case "
                    "WHERE tenant_id = %s AND conversation_id = %s",
                    (tenant_id, conversation_id),
                )
                assert cursor.fetchone()[0] == 1
    finally:
        if engine is not None:
            async def dispose_async_database():
                await engine.dispose()

            run_sync(dispose_async_database())
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM customer_service.cs_case "
                    "WHERE tenant_id = %s AND conversation_id = %s",
                    (tenant_id, conversation_id),
                )
        admin.close()
