"""B7 在途订单改址：business-mock 模拟、确认、幂等与拒绝边界。"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from backend.customer_service.action import build_pending_action
from backend.customer_service.errors import (
    AddressChangeUnavailableError,
    OrderNotEligibleError,
)
from backend.customer_service.service.address_change_service import (
    AddressChangeService,
    extract_new_address,
    normalize_new_address,
)
from backend.mock.business_service import main as business_mock

_TOKEN = "mock-address-change-test-token"
_HEADERS = {"X-Internal-Token": _TOKEN}
_ADDRESS = "浙江省杭州市滨江区长河街道江虹路88号1幢501室"


@pytest.fixture
def business_client(monkeypatch):
    business_mock.reset_mock_state()
    monkeypatch.setattr(business_mock, "_INTERNAL_TOKEN", _TOKEN)
    from fastapi.testclient import TestClient

    with TestClient(business_mock.app) as client:
        yield client
    business_mock.reset_mock_state()


@pytest.fixture
def http_wire(business_client, monkeypatch):
    """HTTP adapter 走真实 mock 路由；测试不依赖共享服务栈。"""
    from backend.config import customer_service as cs_config
    from backend.config import messaging
    from backend.infra.http import business_client as http_client
    from backend.infra.http.business_client import BusinessServiceError

    monkeypatch.setattr(cs_config, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(cs_config, "CS_ADDRESS_CHANGE_MODE", "mock")
    monkeypatch.setattr(messaging, "BUSINESS_SERVICE_URL", "http://business-mock:8081/")

    def request_json_sync(method, path, *, params=None, json_body=None):
        response = business_client.request(
            method,
            path,
            params=params,
            json=json_body,
            headers=_HEADERS,
        )
        if not 200 <= response.status_code < 300:
            raise BusinessServiceError(
                f"business-service {method} {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(
        http_client,
        "get_json_sync",
        lambda path, params=None: request_json_sync("GET", path, params=params),
    )
    monkeypatch.setattr(
        http_client,
        "post_json_sync",
        lambda path, json_body: request_json_sync(
            "POST", path, json_body=json_body,
        ),
    )
    return business_client


class _FakeConfirmStore:
    def __init__(self):
        self.pending: dict[tuple[str, str], dict] = {}
        self.cleared: list[tuple[str, str, str]] = []

    def save(self, user_id, session_id, pending_action, tenant_id=""):
        self.pending[(user_id, session_id)] = dict(pending_action)

    def load(self, user_id, session_id):
        return self.pending.get((user_id, session_id))

    def claim_for_execution(self, user_id, session_id, **_kwargs):
        pending = self.pending.pop((user_id, session_id), None)
        return pending.get("action_id") if pending else None

    def clear(self, user_id, session_id, final_state="cancelled"):
        self.cleared.append((user_id, session_id, final_state))
        self.pending.pop((user_id, session_id), None)


@pytest.fixture
def fake_confirm_store(monkeypatch):
    from backend.customer_service import confirmation_store

    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    return store


def _in_transit_order(client, user_id: str) -> dict:
    response = client.get(
        "/business/orders",
        params={"user_id": user_id},
        headers=_HEADERS,
    )
    assert response.status_code == 200
    return next(
        order for order in response.json()["data"]["orders"]
        if order["status"] == "shipped"
    )


def test_policy_question_stays_knowledge_and_explicit_action_routes_to_b7():
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.types import CSDomain

    coarse = CSCoarseRouter()
    domain, _, _ = coarse.classify("在途包裹能不能改地址？")
    assert domain == CSDomain.KNOWLEDGE

    action_query = "请帮我把在途订单的收货地址改到浙江省杭州市滨江区长河街道88号"
    action_domain, _, _ = coarse.classify(action_query)
    assert action_domain == CSDomain.TRANSACTION
    intent, confidence, _ = CSFineRouter().classify(action_query, action_domain)
    assert intent == "t_change_address_in_transit"
    assert confidence > 0


def test_address_extraction_requires_a_complete_non_question_address():
    assert normalize_new_address(_ADDRESS) == _ADDRESS
    assert extract_new_address(
        {"metadata": {}}, f"请把收货地址改到{_ADDRESS}"
    ) == _ADDRESS
    assert extract_new_address({"metadata": {}}, "在途包裹能不能改地址？") == ""
    with pytest.raises(OrderNotEligibleError):
        normalize_new_address("滨江")


def test_proposal_confirmation_is_mock_only_audited_and_order_is_unchanged(
    http_wire, fake_confirm_store, monkeypatch,
):
    from backend.customer_service import confirmation_flow
    from backend.observability import metrics
    from backend.shared import idempotency

    monkeypatch.setattr(
        idempotency,
        "run_idempotent_side_effect",
        lambda _operation, _payload, execute, **_kwargs: execute(),
    )
    monkeypatch.setattr(metrics, "record_cs_confirmation", lambda *_a: None)
    monkeypatch.setattr(metrics, "record_cs_action", lambda *_a: None)

    user_id, tenant_id, conversation_id = (
        "customer-address-change", "tenant-a", "conversation-address-change",
    )
    order = _in_transit_order(http_wire, user_id)
    before_order = dict(business_mock._find_order(user_id, order["order_no"]))
    proposal = AddressChangeService().build_proposal(
        user_id, order["order_no"], _ADDRESS,
    )
    pending = build_pending_action(proposal)
    fake_confirm_store.save(user_id, conversation_id, pending, tenant_id=tenant_id)

    outcome = confirmation_flow.process_confirmation(
        pending,
        "确认",
        user_id,
        conversation_id,
        tenant_id=tenant_id,
    )

    assert outcome.kind == "success"
    assert "改址模拟请求已登记" in outcome.answer
    assert "真实订单地址未修改" in outcome.answer
    assert "未通知真实承运方" in outcome.answer
    record = outcome.action_result["action_record"]
    assert record["action_type"] == "address_change"
    assert record["status"] == "simulated"
    assert record["data"]["real_order_address_changed"] is False
    assert record["data"]["real_carrier_notified"] is False
    assert record["data"]["after_state"]["request_status"] == "simulated"
    assert outcome.audit_entry["action_type"] == "address_change"
    assert outcome.audit_entry["result"] == "success"
    assert "real order address unchanged" in outcome.audit_entry["detail"]
    assert business_mock._find_order(user_id, order["order_no"]) == before_order
    assert len(business_mock._ADDRESS_CHANGE_REQUESTS) == 1


def test_mock_endpoint_is_owner_scoped_idempotent_and_one_request_per_order(
    business_client,
):
    user_id = "customer-address-endpoint"
    order = _in_transit_order(business_client, user_id)
    payload = {
        "operation_key": str(uuid.uuid4()),
        "user_id": user_id,
        "tenant_id": "tenant-a",
        "order_no": order["order_no"],
        "expected_shipping_status": "shipped",
        "new_address": _ADDRESS,
    }
    path = f"/business/logistics/{order['order_no']}/change-address"
    first = business_client.post(path, json=payload, headers=_HEADERS)
    replay = business_client.post(path, json=payload, headers=_HEADERS)
    assert first.status_code == replay.status_code == 200
    first_request = first.json()["data"]["address_change_request"]
    assert replay.json()["data"]["address_change_request"] == first_request
    assert first_request["real_carrier_notified"] is False
    assert first_request["real_order_address_changed"] is False

    other_operation = {**payload, "operation_key": str(uuid.uuid4())}
    assert business_client.post(path, json=other_operation, headers=_HEADERS).status_code == 409

    other_user_payload = {**payload, "user_id": "different-customer"}
    other_user_payload["operation_key"] = str(uuid.uuid4())
    response = business_client.post(
        path, json=other_user_payload, headers=_HEADERS,
    )
    assert response.status_code == 404


def test_confirmation_rechecks_shipping_status_before_mock_write(http_wire):
    user_id = "customer-address-stale"
    order = _in_transit_order(http_wire, user_id)
    proposal = AddressChangeService().build_proposal(
        user_id, order["order_no"], _ADDRESS,
    )
    pending = build_pending_action(proposal)
    business_mock._find_order(user_id, order["order_no"])["status"] = "completed"

    with pytest.raises(OrderNotEligibleError):
        AddressChangeService().execute_confirmed_action(
            pending,
            confirmation_id=pending["action_id"],
            user_id=user_id,
            tenant_id="tenant-a",
            conversation_id="conversation-stale",
        )
    assert not business_mock._ADDRESS_CHANGE_REQUESTS


def test_service_replays_same_confirmation_but_refuses_real_target_and_disabled_mode(
    http_wire, monkeypatch,
):
    from backend.config import customer_service as cs_config
    from backend.config import messaging

    user_id = "customer-address-idempotency"
    order = _in_transit_order(http_wire, user_id)
    pending = build_pending_action(AddressChangeService().build_proposal(
        user_id, order["order_no"], _ADDRESS,
    ))
    service = AddressChangeService()
    first = service.execute_confirmed_action(
        pending,
        confirmation_id=pending["action_id"],
        user_id=user_id,
        tenant_id="tenant-a",
        conversation_id="conversation-idempotency",
    )
    replay = service.execute_confirmed_action(
        pending,
        confirmation_id=pending["action_id"],
        user_id=user_id,
        tenant_id="tenant-a",
        conversation_id="conversation-idempotency",
    )
    assert first.to_dict()["data"]["address_change_request"] == replay.to_dict()[
        "data"
    ]["address_change_request"]

    monkeypatch.setattr(cs_config, "CS_ADDRESS_CHANGE_MODE", "disabled")
    with pytest.raises(AddressChangeUnavailableError):
        service.build_proposal(user_id, order["order_no"], _ADDRESS)

    monkeypatch.setattr(cs_config, "CS_ADDRESS_CHANGE_MODE", "mock")
    monkeypatch.setattr(messaging, "BUSINESS_SERVICE_URL", "https://real-carrier.example")
    with pytest.raises(AddressChangeUnavailableError):
        service.build_proposal(user_id, order["order_no"], _ADDRESS)


def test_unverifiable_mock_write_stays_in_verifying(
    http_wire, fake_confirm_store, monkeypatch,
):
    from backend.customer_service.confirmation_flow import process_confirmation
    from backend.infra.http import business_client as http_client

    user_id, tenant_id, session_id = (
        "customer-address-unknown", "tenant-a", "conversation-address-unknown",
    )
    order = _in_transit_order(http_wire, user_id)
    pending = build_pending_action(AddressChangeService().build_proposal(
        user_id, order["order_no"], _ADDRESS,
    ))
    fake_confirm_store.save(user_id, session_id, pending, tenant_id=tenant_id)
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
    assert "请勿重复提交" in outcome.answer
    assert outcome.audit_entry["result"] == "in_doubt"
    assert len(business_mock._ADDRESS_CHANGE_REQUESTS) == 1


def test_missing_address_slot_recognizes_only_valid_address_and_builds_proposal(
    http_wire, fake_confirm_store, monkeypatch,
):
    from backend.customer_service.experts.action import execute_action
    from backend.customer_service.pending_handler import (
        _looks_like_need_info_slot_answer,
    )

    user_id, tenant_id, session_id = (
        "customer-address-slot", "tenant-a", "conversation-address-slot",
    )
    order = _in_transit_order(http_wire, user_id)
    initial = execute_action(
        f"请修改订单 {order['order_no']} 的收货地址",
        {
            "intent": "t_change_address_in_transit",
            "metadata": {"order_id": order["order_no"]},
        },
        {"user_id": user_id, "tenant_id": tenant_id, "session_id": session_id},
    )
    pending = initial["data"]["pending_action"]
    assert pending["status"] == "need_info"
    assert pending["missing_slots"] == ["new_address"]
    assert _looks_like_need_info_slot_answer(
        {"user_message": _ADDRESS}, pending,
    )
    assert not _looks_like_need_info_slot_answer(
        {"user_message": "这个政策怎么规定？"}, pending,
    )

    from backend.customer_service import pending_handler

    monkeypatch.setattr(
        pending_handler, "_read_authoritative_handoff", lambda *_args: None,
    )
    pending_turn = pending_handler.cs_pending_handler_node({
        "pending_action": pending,
        "confirmation_state": "pending_confirmation",
        "user_message": _ADDRESS,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "conversation_id": session_id,
        "cs_route": {"route_path": "business_query"},
    })
    assert pending_turn.goto == "cs_action_expert"

    filled = execute_action(
        _ADDRESS,
        {"intent": "t_change_address_in_transit", "metadata": {}},
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "pending_action": pending,
        },
    )
    proposal = filled["data"]["pending_action"]
    assert proposal["action_type"] == "address_change"
    assert proposal["after_state"]["new_address"] == _ADDRESS
    assert "每单限 1 次" in proposal["proposal_text"]

    from types import SimpleNamespace

    from backend.customer_service.context import semantic_slots

    monkeypatch.setattr(
        semantic_slots,
        "resolve_from_metadata",
        lambda *_args: SimpleNamespace(resolved=False, ambiguous=False),
    )
    no_order = execute_action(
        f"请把地址改到{_ADDRESS}",
        {"intent": "t_change_address_in_transit", "metadata": {}},
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "session_id": "conversation-address-no-order",
        },
    )
    order_pending = no_order["data"]["pending_action"]
    assert order_pending["missing_slots"] == ["order_id"]
    assert order_pending["collected_slots"]["new_address"] == _ADDRESS

    order_filled = execute_action(
        order["order_no"],
        {"intent": "t_change_address_in_transit", "metadata": {}},
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "session_id": "conversation-address-no-order",
            "pending_action": order_pending,
        },
    )
    order_proposal = order_filled["data"]["pending_action"]
    assert order_proposal["action_type"] == "address_change"
    assert order_proposal["after_state"]["new_address"] == _ADDRESS


def test_address_change_action_is_persisted_by_real_audit_repository():
    """T2：只允许显式指定的 loopback test/sandbox PostgreSQL。"""
    import asyncio
    import os
    import uuid
    from datetime import datetime, timezone

    import psycopg2
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dsn = os.getenv("CS_ADDRESS_CHANGE_AUDIT_PG_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("设置 CS_ADDRESS_CHANGE_AUDIT_PG_TEST_DSN 后运行隔离 PostgreSQL T2")
    url = make_url(dsn)
    database_name = str(url.database or "").lower()
    if (
        url.host not in {"127.0.0.1", "localhost"}
        or url.port in {None, 5433}
        or not (
            database_name.endswith("_test")
            or database_name.startswith("test_")
            or "sandbox" in database_name
        )
    ):
        pytest.fail("B7 审计 T2 只允许明确标记 test/sandbox 的 loopback 隔离库")

    root = Path(__file__).resolve().parents[3]
    migration_dir = root / "backend" / "sql" / "migrations"
    action_migration = migration_dir / "137_cs_address_change_action_type.sql"
    status_migration = migration_dir / "050_agent_actions_status_enum.sql"
    action_id = str(uuid.uuid4())
    conversation_id = f"address-change-test-{uuid.uuid4().hex}"
    user_id = f"address-change-user-{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(dsn)
    engine = None
    try:
        with admin:
            with admin.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS customer_service")
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS customer_service.conversations "
                    "(conversation_id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL)"
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
                                'failed', 'cancelled'
                            )),
                        executed_by VARCHAR(20) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        executed_at TIMESTAMPTZ
                    )
                    """
                )
                cursor.execute(status_migration.read_text(encoding="utf-8"))
                cursor.execute(action_migration.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO customer_service.conversations "
                    "(conversation_id, user_id) VALUES (%s, %s) "
                    "ON CONFLICT (conversation_id) DO NOTHING",
                    (conversation_id, user_id),
                )

        from backend.customer_service.action import AgentActionRecord
        from backend.customer_service.repository.audit_repo import AuditRepository

        record = AgentActionRecord(
            action_id=action_id,
            action_type="address_change",
            agent_type="ai",
            target_type="order",
            target_id="MO-ADDRESS-CHANGE",
            data={
                "before_state": {
                    "order_no": "MO-ADDRESS-CHANGE",
                    "shipping_status": "shipped",
                },
                "after_state": {
                    "new_address": "浙江省杭州市滨江区长河街道88号",
                    "status": "simulated",
                    "request_id": action_id,
                    "real_carrier_notified": False,
                    "real_order_address_changed": False,
                },
            },
            status="simulated",
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
        assert row[0:2] == ("address_change", "simulated")
        assert row[2]["shipping_status"] == "shipped"
        assert row[3]["request_id"] == action_id
        assert row[3]["real_carrier_notified"] is False
        assert row[3]["real_order_address_changed"] is False
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


def test_b7_migration_and_rag_smoke_registration():
    repo_root = Path(__file__).resolve().parents[3]
    migration_name = "137_cs_address_change_action_type.sql"
    from scripts.init_db import MIGRATION_TARGETS

    assert MIGRATION_TARGETS[migration_name] == "memory"
    migration = (
        repo_root / "backend" / "sql" / "migrations" / migration_name
    ).read_text(encoding="utf-8")
    for action_type in ("urge_request", "price_match", "address_change"):
        assert f"'{action_type}'" in migration

    spec = importlib.util.spec_from_file_location(
        "rag_smoke_path_filter",
        repo_root / ".github" / "scripts" / "rag_smoke_path_filter.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.classify_migration(
        f"backend/sql/migrations/{migration_name}"
    ) == "non_ai"

    workflow = (repo_root / ".github" / "workflows" / "rag_smoke.yml").read_text(
        encoding="utf-8",
    )
    assert "backend/tests/customer_service/test_address_change_flow.py" in workflow
