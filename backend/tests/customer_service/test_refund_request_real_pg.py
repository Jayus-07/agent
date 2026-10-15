"""退款申请案件事实源的隔离 PostgreSQL 集成验收。"""
from __future__ import annotations

import asyncio
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
    other_user_id = f"refund-request-other-user-{suffix}"
    tenant_id = f"refund-request-tenant-{suffix}"
    other_tenant_id = f"refund-request-other-tenant-{suffix}"
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
        order_migration = (
            Path(__file__).resolve().parents[2]
            / "sql" / "migrations" / "098_cs_refund_order_unique.sql"
        ).read_text(encoding="utf-8")
        with admin:
            with admin.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS customer_service")
                cursor.execute(migration)
                cursor.execute(
                    "DROP INDEX IF EXISTS "
                    "customer_service.uq_cs_case_active_refund_order"
                )

        # Existing duplicate groups must stop the migration without changing rows.
        preflight_entries = [
            (f"refund-request-preflight-a-{suffix}", "duplicate-preflight-order"),
            (f"refund-request-preflight-b-{suffix}", "duplicate-preflight-order"),
            (f"refund-request-preflight-empty-a-{suffix}", ""),
            (f"refund-request-preflight-empty-b-{suffix}", None),
        ]
        with admin:
            with admin.cursor() as cursor:
                for conversation, order_key in preflight_entries:
                    context = (
                        "{}" if order_key is None else
                        '{"refund_request":{"order_id":"'
                        + order_key + '"}}'
                    )
                    cursor.execute(
                        """
                        INSERT INTO customer_service.cs_case (
                            case_id, tenant_id, user_id, conversation_id,
                            case_type, status, context
                        ) VALUES (
                            %s, %s, %s, %s, 'refund', 'open', %s::jsonb
                        )
                        """,
                        (
                            str(uuid.uuid4()), tenant_id, user_id,
                            conversation, context,
                        ),
                    )
        with pytest.raises(psycopg2.Error, match="found 2 duplicate active refund groups"):
            with admin:
                with admin.cursor() as cursor:
                    cursor.execute(order_migration)
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM customer_service.cs_case "
                    "WHERE tenant_id = %s AND user_id = %s "
                    "AND conversation_id = ANY(%s)",
                    (
                        tenant_id, user_id,
                        [conversation for conversation, _ in preflight_entries],
                    ),
                )
                assert cursor.fetchone()[0] == 4
                cursor.execute(
                    "DELETE FROM customer_service.cs_case "
                    "WHERE tenant_id = %s AND user_id = %s "
                    "AND conversation_id = ANY(%s)",
                    (
                        tenant_id, user_id,
                        [conversation for conversation, _ in preflight_entries],
                    ),
                )
                cursor.execute(order_migration)

        # 缺失、NULL 或空白订单键归一到同一空键，仍由唯一索引挡住。
        blank_order_conversations = [
            f"refund-request-blank-a-{suffix}",
            f"refund-request-blank-b-{suffix}",
        ]
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO customer_service.cs_case "
                    "(case_id, tenant_id, user_id, conversation_id, case_type, context) "
                    "VALUES (%s, %s, %s, %s, 'refund', '{}'::jsonb)",
                    (
                        str(uuid.uuid4()), tenant_id, user_id,
                        blank_order_conversations[0],
                    ),
                )
        with pytest.raises(psycopg2.errors.UniqueViolation):
            with admin:
                with admin.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO customer_service.cs_case "
                        "(case_id, tenant_id, user_id, conversation_id, case_type, context) "
                        "VALUES (%s, %s, %s, %s, 'refund', "
                        "jsonb_build_object('refund_request', "
                        "jsonb_build_object('order_id', ' ')))",
                        (
                            str(uuid.uuid4()), tenant_id, user_id,
                            blank_order_conversations[1],
                        ),
                    )
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM customer_service.cs_case "
                    "WHERE tenant_id = %s AND conversation_id = ANY(%s)",
                    (tenant_id, blank_order_conversations),
                )

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
        from backend.customer_service.errors import (
            BusinessRuleError,
            OrderNotEligibleError,
            RefundRequestConflictError,
        )
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
        with pytest.raises(RefundRequestConflictError):
            service.execute_refund_request(
                {**pending, "after_state": {"reason": "不同原因"}}, **fields,
            )
        eligibility_check = service.check_refund_eligibility
        monkeypatch.setattr(
            service,
            "check_refund_eligibility",
            lambda *_: RefundEligibility(
                True, "order-42", "ORD-0042", 50.00, "paid",
                policy_version="refund-pg-test-v1",
            ),
        )
        with pytest.raises(OrderNotEligibleError):
            service.execute_refund_request(
                pending,
                **{**fields, "confirmation_id": str(uuid.uuid4())},
            )
        monkeypatch.setattr(
            service, "check_refund_eligibility", eligibility_check,
        )

        case_service = get_case_service()
        with pytest.raises(BusinessRuleError, match="缺少有效订单标识"):
            run_sync(case_service.async_create_refund_request(
                request_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                order_id="  ",
                order_no="ORD-0042",
                amount="49.95",
                reason="缺少订单标识",
                policy_version="refund-pg-test-v1",
            ))
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

        reapply_fields = {**fields, "confirmation_id": str(uuid.uuid4())}
        reapplied = service.execute_refund_request(pending, **reapply_fields)
        assert reapplied.data["request"]["status"] == "pending_review"
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "UPDATE customer_service.cs_case SET status = 'closed' "
                    "WHERE case_id = %s",
                    (reapply_fields["confirmation_id"],),
                )

        # 两个并发调用先同时完成 exact-ID 查询，再竞争真实 PostgreSQL 唯一索引。
        case_service = get_case_service()
        original_load = case_service._load_refund_case
        initial_loads = 0
        both_initial_reads_done = asyncio.Event()

        async def synchronized_initial_load(db, request_id, **scope):
            nonlocal initial_loads
            result = await original_load(db, request_id, **scope)
            initial_loads += 1
            if initial_loads <= 2:
                if initial_loads == 2:
                    both_initial_reads_done.set()
                await both_initial_reads_done.wait()
            return result

        monkeypatch.setattr(
            case_service, "_load_refund_case", synchronized_initial_load,
        )
        concurrent_conversations = [
            f"refund-request-concurrent-a-{suffix}",
            f"refund-request-concurrent-b-{suffix}",
        ]
        concurrent_fields = [
            {
                "request_id": str(uuid.uuid4()),
                "conversation_id": conversation,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "order_id": "order-42",
                "order_no": "ORD-0042",
                "amount": "49.95",
                "reason": "商品破损",
                "policy_version": "refund-pg-test-v1",
            }
            for conversation in concurrent_conversations
        ]

        async def create_concurrently():
            return await asyncio.gather(
                *(
                    case_service.async_create_refund_request(**request_fields)
                    for request_fields in concurrent_fields
                ),
                return_exceptions=True,
            )

        concurrent_results = run_sync(create_concurrently())
        monkeypatch.setattr(case_service, "_load_refund_case", original_load)
        assert sum(isinstance(result, dict) for result in concurrent_results) == 1
        assert sum(
            isinstance(result, RefundRequestConflictError)
            for result in concurrent_results
        ) == 1
        conflict = next(
            result for result in concurrent_results
            if isinstance(result, RefundRequestConflictError)
        )
        assert "该订单已有待处理的退款申请" in conflict.user_message
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM customer_service.cs_case "
                    "WHERE tenant_id = %s AND user_id = %s "
                    "AND case_type = 'refund' "
                    "AND status IN ('open', 'waiting_user', 'processing') "
                    "AND context #>> '{refund_request,order_id}' = 'order-42'",
                    (tenant_id, user_id),
                )
                assert cursor.fetchone()[0] == 1

        # 同订单可以由其他用户、其他租户各自申请，不发生错误互阻。
        async def create_isolated_requests():
            return await asyncio.gather(
                case_service.async_create_refund_request(
                    **{
                        **concurrent_fields[0],
                        "request_id": str(uuid.uuid4()),
                        "conversation_id": f"refund-request-other-user-{suffix}",
                        "user_id": other_user_id,
                    },
                ),
                case_service.async_create_refund_request(
                    **{
                        **concurrent_fields[0],
                        "request_id": str(uuid.uuid4()),
                        "conversation_id": f"refund-request-other-tenant-{suffix}",
                        "tenant_id": other_tenant_id,
                    },
                ),
            )

        isolated_results = run_sync(create_isolated_requests())
        assert len(isolated_results) == 2

        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM customer_service.cs_case "
                    "WHERE tenant_id IN (%s, %s) "
                    "AND user_id IN (%s, %s)",
                    (tenant_id, other_tenant_id, user_id, other_user_id),
                )
                assert cursor.fetchone()[0] == 5
    finally:
        if engine is not None:
            async def dispose_async_database():
                await engine.dispose()

            run_sync(dispose_async_database())
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM customer_service.cs_case "
                    "WHERE tenant_id IN (%s, %s) "
                    "AND user_id IN (%s, %s)",
                    (tenant_id, other_tenant_id, user_id, other_user_id),
                )
        admin.close()
