"""B6 价保退差价：路由、mock 核价、确认执行与失败闭锁。"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backend.customer_service.action import build_pending_action
from backend.customer_service.errors import (
    OrderNotEligibleError,
    PriceMatchUnavailableError,
)
from backend.customer_service.service.price_match_service import PriceMatchService
from backend.mock.business_service import main as business_mock

_TOKEN = "mock-internal-test-token"
_HEADERS = {"X-Internal-Token": _TOKEN}


@pytest.fixture
def business_client(monkeypatch):
    business_mock.reset_mock_state()
    monkeypatch.setattr(business_mock, "_INTERNAL_TOKEN", _TOKEN)
    monkeypatch.setattr(business_mock, "_PRICE_MATCH_MODE", "mock")
    monkeypatch.setattr(business_mock, "_PRICE_MATCH_WINDOW_DAYS", 10)
    from fastapi.testclient import TestClient

    with TestClient(business_mock.app) as client:
        yield client
    business_mock.reset_mock_state()


@pytest.fixture
def http_wire(business_client, monkeypatch):
    """客服适配器走当前进程内的真实 business-mock HTTP 路由。"""
    from backend.config import customer_service as cfg
    from backend.config import messaging
    from backend.infra.http import business_client as http_client
    from backend.infra.http.business_client import BusinessServiceError
    from backend.observability import metrics
    from backend.shared import idempotency

    def request_json_sync(method, path, *, params=None, json_body=None):
        response = business_client.request(
            method, path, params=params, json=json_body, headers=_HEADERS,
        )
        if not 200 <= response.status_code < 300:
            raise BusinessServiceError(
                f"business-mock {method} {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(cfg, "CS_PRICE_MATCH_MODE", "mock")
    monkeypatch.setattr(cfg, "CS_PRICE_MATCH_WINDOW_DAYS", "10")
    monkeypatch.setattr(messaging, "BUSINESS_SERVICE_URL", "http://business-mock:8081")
    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(
        http_client, "get_json_sync",
        lambda path, params=None: request_json_sync("GET", path, params=params),
    )
    monkeypatch.setattr(
        http_client, "post_json_sync",
        lambda path, json_body: request_json_sync(
            "POST", path, json_body=json_body,
        ),
    )
    monkeypatch.setattr(
        idempotency, "run_idempotent_side_effect",
        lambda _operation, _payload, execute, **_kwargs: execute(),
    )
    monkeypatch.setattr(metrics, "record_cs_confirmation", lambda *_args: None)
    monkeypatch.setattr(metrics, "record_cs_action", lambda *_args: None)
    return business_client


class _FakeConfirmStore:
    def __init__(self):
        self.pending: dict[tuple[str, str], dict] = {}
        self.cleared: list[tuple[str, str, str]] = []

    def save(self, user_id, session_id, pending_action, tenant_id=""):
        self.pending[(user_id, session_id)] = dict(pending_action)

    def claim_for_execution(self, user_id, session_id, **_kwargs):
        action = self.pending.pop((user_id, session_id), None)
        return action.get("action_id") if action else None

    def clear(self, user_id, session_id, final_state="cancelled"):
        self.cleared.append((user_id, session_id, final_state))
        self.pending.pop((user_id, session_id), None)


@pytest.fixture
def fake_store(monkeypatch):
    from backend.customer_service import confirmation_store

    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    return store


def _orders(client, user_id: str) -> list[dict]:
    return client.get(
        "/business/orders",
        params={"user_id": user_id},
        headers=_HEADERS,
    ).json()["data"]["orders"]


def _wallet(client, user_id: str, tenant_id: str) -> dict:
    return client.get(
        "/business/wallet",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["wallet"]


def _execute(service, pending, user_id, tenant_id, session_id):
    return service.execute_confirmed_action(
        pending,
        confirmation_id=pending["action_id"],
        user_id=user_id,
        tenant_id=tenant_id,
        conversation_id=session_id,
    )


def test_price_match_intent_does_not_capture_policy_questions():
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.types import CSDomain

    coarse = CSCoarseRouter()
    fine = CSFineRouter()

    action_domain, _, _ = coarse.classify(
        "我买完耳机后降价了，帮我退差价",
    )
    assert action_domain == CSDomain.AFTER_SALES
    intent, confidence, _ = fine.classify(
        "我买完耳机后降价了，帮我退差价",
        CSDomain.AFTER_SALES,
    )
    assert intent == "t_price_match"
    assert confidence > 0

    policy_domain, _, _ = coarse.classify("价保政策是什么？")
    assert policy_domain == CSDomain.KNOWLEDGE


def test_price_match_confirmation_writes_one_mock_wallet_entry(
    http_wire, fake_store,
):
    from backend.customer_service.confirmation_flow import process_confirmation

    user_id, tenant_id = "customer-price-match", "tenant-a"
    paid = next(order for order in _orders(http_wire, user_id) if order["status"] == "paid")
    wallet_before = _wallet(http_wire, user_id, tenant_id)
    service = PriceMatchService()
    proposal = service.build_proposal(user_id, tenant_id, paid["order_no"])

    assert proposal.action_type == "price_match"
    assert proposal.target_id == paid["id"]
    assert proposal.risk_level.value == "high"
    assert proposal.before_state["amount"] == "100.00"
    assert "business-mock" in proposal.proposal_text
    pending = build_pending_action(proposal)
    session_id = "cs-price-match-session"
    fake_store.save(user_id, session_id, pending, tenant_id=tenant_id)

    outcome = process_confirmation(
        pending, "确认", user_id, session_id, tenant_id=tenant_id,
    )
    assert outcome.kind == "success"
    assert "未影响真实资金" in outcome.answer
    action_record = outcome.action_result["action_record"]
    refund = action_record["data"]["refund"]
    assert refund == {
        "amount": "100.00",
        "currency": "CNY",
        "kind": "price_match",
        "status": "simulated",
        "real_funds_affected": False,
    }
    assert action_record["status"] == "executed"
    assert action_record["data"]["execution_mode"] == "business_mock"

    wallet_after = _wallet(http_wire, user_id, tenant_id)
    assert Decimal(str(wallet_after["balance"])) == (
        Decimal(str(wallet_before["balance"])) + Decimal("100.00")
    )
    entries = [
        entry for entry in wallet_after["ledger"]
        if entry["kind"] == "price_match" and entry["ref_no"] == paid["order_no"]
    ]
    assert len(entries) == 1
    assert Decimal(str(entries[0]["amount"])) == Decimal("100.00")

    duplicate = process_confirmation(
        pending, "确认", user_id, session_id, tenant_id=tenant_id,
    )
    assert duplicate.kind == "duplicate"
    assert len([
        entry for entry in _wallet(http_wire, user_id, tenant_id)["ledger"]
        if entry["kind"] == "price_match"
    ]) == 1


def test_unverifiable_price_match_write_stays_in_verifying(http_wire, fake_store, monkeypatch):
    from backend.customer_service.confirmation_flow import process_confirmation
    from backend.infra.http import business_client as http_client

    user_id, tenant_id, session_id = (
        "customer-price-match-unknown", "tenant-a", "session-unknown",
    )
    paid = next(order for order in _orders(http_wire, user_id) if order["status"] == "paid")
    service = PriceMatchService()
    pending = build_pending_action(
        service.build_proposal(user_id, tenant_id, paid["order_no"]),
    )
    fake_store.save(user_id, session_id, pending, tenant_id=tenant_id)
    request_json_sync = http_client.request_json_sync

    def damaged_success_response(method, path, *, params=None, json_body=None):
        response = request_json_sync(
            method, path, params=params, json_body=json_body,
        )
        if method.upper() == "POST":
            return {"code": 0, "data": None}
        return response

    monkeypatch.setattr(
        http_client, "request_json_sync", damaged_success_response,
    )
    outcome = process_confirmation(
        pending, "确认", user_id, session_id, tenant_id=tenant_id,
    )

    assert outcome.kind == "failed"
    assert outcome.confirmation_state == "verifying"
    assert "暂停" in outcome.answer
    assert outcome.audit_entry["result"] == "in_doubt"
    entries = [
        entry for entry in _wallet(http_wire, user_id, tenant_id)["ledger"]
        if entry["kind"] == "price_match" and entry["ref_no"] == paid["order_no"]
    ]
    assert len(entries) == 1


def test_price_match_http_idempotency_and_order_level_duplicate_guard(http_wire):
    user_id, tenant_id, session_id = (
        "customer-price-match-replay", "tenant-a", "session-replay",
    )
    paid = next(order for order in _orders(http_wire, user_id) if order["status"] == "paid")
    service = PriceMatchService()
    pending = build_pending_action(
        service.build_proposal(user_id, tenant_id, paid["order_no"]),
    )

    first = _execute(service, pending, user_id, tenant_id, session_id)
    replay = _execute(service, pending, user_id, tenant_id, session_id)
    assert replay.to_dict()["data"]["refund"] == first.to_dict()["data"]["refund"]
    wallet = _wallet(http_wire, user_id, tenant_id)
    assert len([entry for entry in wallet["ledger"] if entry["kind"] == "price_match"]) == 1

    second_pending = build_pending_action(
        service.build_proposal(user_id, tenant_id, paid["order_no"]),
    )
    with pytest.raises(OrderNotEligibleError, match="未执行"):
        _execute(service, second_pending, user_id, tenant_id, session_id)
    assert len([
        entry for entry in _wallet(http_wire, user_id, tenant_id)["ledger"]
        if entry["kind"] == "price_match"
    ]) == 1


def test_price_match_rechecks_price_and_order_window(http_wire, monkeypatch):
    user_id, tenant_id, session_id = (
        "customer-price-match-change", "tenant-a", "session-change",
    )
    paid = next(order for order in _orders(http_wire, user_id) if order["status"] == "paid")
    service = PriceMatchService()
    pending = build_pending_action(
        service.build_proposal(user_id, tenant_id, paid["order_no"]),
    )
    monkeypatch.setitem(
        business_mock._PRODUCT_CURRENT_PRICES,
        "SKU-WIRELESS-HEADPHONES",
        Decimal("1100.00"),
    )
    with pytest.raises(OrderNotEligibleError, match="变化"):
        _execute(service, pending, user_id, tenant_id, session_id)
    assert not [
        entry for entry in _wallet(http_wire, user_id, tenant_id)["ledger"]
        if entry["kind"] == "price_match"
    ]

    completed = next(order for order in _orders(http_wire, user_id) if order["status"] == "completed")
    with pytest.raises(OrderNotEligibleError, match="窗口"):
        service.build_proposal(user_id, tenant_id, completed["order_no"])


def test_price_match_fails_closed_when_disabled_or_target_is_not_mock(
    http_wire, monkeypatch,
):
    from backend.config import customer_service as cfg
    from backend.config import messaging

    paid = next(
        order for order in _orders(http_wire, "customer-price-match-disabled")
        if order["status"] == "paid"
    )
    service = PriceMatchService()

    monkeypatch.setattr(business_mock, "_PRICE_MATCH_MODE", "disabled")
    blocked = http_wire.post(
        "/business/orders/MO-BYPASS/partial-refund",
        json={
            "operation_key": "price-match-bypass-001",
            "user_id": "customer-price-match-disabled",
            "tenant_id": "tenant-a",
            "order_no": "MO-BYPASS",
            "expected_status": "paid",
            "expected_payment_status": "paid",
            "expected_paid_at": "2026-11-01T00:00:00+00:00",
            "expected_currency": "CNY",
            "product_no": "SKU-WIRELESS-HEADPHONES",
            "expected_original_price": "1299.00",
            "expected_current_price": "1199.00",
            "expected_amount": "100.00",
            "window_days": 10,
        },
        headers=_HEADERS,
    )
    assert blocked.status_code == 409

    monkeypatch.setattr(cfg, "CS_PRICE_MATCH_MODE", "disabled")
    with pytest.raises(PriceMatchUnavailableError) as exc_info:
        service.build_proposal(
            "customer-price-match-disabled", "tenant-a", paid["order_no"],
        )
    assert "未执行价保" in exc_info.value.user_message
    assert "未创建提案" not in exc_info.value.user_message

    monkeypatch.setattr(cfg, "CS_PRICE_MATCH_MODE", "mock")
    monkeypatch.setattr(
        messaging, "BUSINESS_SERVICE_URL", "http://business-service:8081",
    )
    with pytest.raises(PriceMatchUnavailableError):
        service.build_proposal(
            "customer-price-match-disabled", "tenant-a", paid["order_no"],
        )


def test_price_match_action_type_migration_is_registered():
    from scripts.init_db import MIGRATION_TARGETS, discover_migrations

    root = Path(__file__).resolve().parents[3]
    migration = (
        root / "backend" / "sql" / "migrations"
        / "136_cs_price_match_action_type.sql"
    )
    assert migration.exists()
    contents = migration.read_text(encoding="utf-8")
    assert "'price_match'" in contents
    assert "'urge_request'" in contents
    ordered, unregistered = discover_migrations()
    assert unregistered == []
    assert MIGRATION_TARGETS["136_cs_price_match_action_type.sql"] == "memory"
    names = [name for name, _target in ordered]
    assert names.index("133_cs_coupon.sql") < names.index(
        "136_cs_price_match_action_type.sql",
    )


def test_business_mock_compose_forwards_price_match_gates():
    root = Path(__file__).resolve().parents[3]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert "CS_PRICE_MATCH_MODE=${CS_PRICE_MATCH_MODE:-disabled}" in compose
    assert "CS_PRICE_MATCH_WINDOW_DAYS=${CS_PRICE_MATCH_WINDOW_DAYS:-0}" in compose


def test_price_match_action_is_persisted_by_the_real_audit_repository():
    """T2：只允许显式指定的 loopback test/sandbox PostgreSQL。"""
    import asyncio
    import os
    import uuid
    from datetime import datetime, timezone

    import psycopg2
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dsn = os.getenv("CS_PRICE_MATCH_AUDIT_PG_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("设置 CS_PRICE_MATCH_AUDIT_PG_TEST_DSN 后运行隔离 PostgreSQL T2")
    url = make_url(dsn)
    database_name = str(url.database or "").lower()
    if (
        url.host not in {"127.0.0.1", "localhost"}
        or url.port == 5433
        or not (
            database_name.endswith("_test")
            or database_name.startswith("test_")
            or "sandbox" in database_name
        )
    ):
        pytest.fail("价保审计 T2 只允许明确标记 test/sandbox 的 loopback 隔离库")

    root = Path(__file__).resolve().parents[3]
    migration_path = (
        root / "backend" / "sql" / "migrations"
        / "136_cs_price_match_action_type.sql"
    )
    status_migration_path = (
        root / "backend" / "sql" / "migrations"
        / "050_agent_actions_status_enum.sql"
    )
    action_id = str(uuid.uuid4())
    conversation_id = f"price-match-test-{uuid.uuid4().hex}"
    user_id = f"price-match-user-{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(dsn)
    engine = None
    try:
        with admin:
            with admin.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS customer_service")
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS customer_service.conversations "
                    "(conversation_id VARCHAR(64) PRIMARY KEY)"
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS customer_service.agent_actions (
                        id BIGSERIAL PRIMARY KEY,
                        action_id VARCHAR(64) NOT NULL UNIQUE,
                        conversation_id VARCHAR(64) NOT NULL
                            REFERENCES customer_service.conversations(conversation_id),
                        user_id VARCHAR(64) NOT NULL,
                        action_type VARCHAR(30) NOT NULL
                            CONSTRAINT agent_actions_action_type_check
                            CHECK (action_type IN (
                                'refund_request', 'refund_execute',
                                'return_request', 'return_execute',
                                'address_update', 'order_cancel',
                                'complaint_create', 'handoff_request',
                                'handoff_accept', 'handoff_close'
                            )),
                        target_type VARCHAR(30),
                        target_id VARCHAR(64),
                        before_state JSONB,
                        after_state JSONB,
                        confirmation_state VARCHAR(20) NOT NULL,
                        status VARCHAR(20) NOT NULL
                            CHECK (status IN (
                                'pending', 'approved', 'executing', 'success',
                                'failed', 'cancelled', 'simulated', 'executed'
                            )),
                        executed_by VARCHAR(20) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        executed_at TIMESTAMPTZ
                    )
                    """
                )
                cursor.execute(
                    status_migration_path.read_text(encoding="utf-8"),
                )
                cursor.execute(
                    "INSERT INTO customer_service.conversations (conversation_id) "
                    "VALUES (%s) ON CONFLICT DO NOTHING",
                    (conversation_id,),
                )
                cursor.execute(migration_path.read_text(encoding="utf-8"))

        from backend.customer_service.action import AgentActionRecord
        from backend.customer_service.repository.audit_repo import AuditRepository

        record = AgentActionRecord(
            action_id=action_id,
            action_type="price_match",
            agent_type="ai",
            target_type="order",
            target_id="MO-PRICE-MATCH",
            data={
                "before_state": {"current_price": "1199.00"},
                "after_state": {
                    "amount": "100.00",
                    "real_funds_affected": False,
                },
            },
            status="executed",
            executed_at=datetime.now(timezone.utc),
        ).to_dict()
        record.update({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "confirmation_state": "success",
        })

        async def persist():
            nonlocal engine
            async_url = url.set(drivername="postgresql+asyncpg")
            engine = create_async_engine(async_url)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory.begin() as session:
                await AuditRepository(session).insert_agent_action(record)
            await engine.dispose()
            engine = None

        asyncio.run(persist())
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT action_type, status, before_state, after_state "
                "FROM customer_service.agent_actions WHERE action_id = %s",
                (action_id,),
            )
            row = cursor.fetchone()
        assert row is not None
        assert row[0:2] == ("price_match", "executed")
        assert row[2] == {"current_price": "1199.00"}
        assert row[3] == {"amount": "100.00", "real_funds_affected": False}
    finally:
        if engine is not None:
            asyncio.run(engine.dispose())
        with admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM customer_service.agent_actions WHERE action_id = %s",
                    (action_id,),
                )
                cursor.execute(
                    "DELETE FROM customer_service.conversations "
                    "WHERE conversation_id = %s",
                    (conversation_id,),
                )
        admin.close()
