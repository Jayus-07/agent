"""B5 催发货/催派送：路由、mock 登记、幂等、审计与异常转人工。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.customer_service.experts.action import action_expert_node
from backend.mock.business_service import main as business_mock

_TOKEN = "mock-internal-test-token"
_HEADERS = {"X-Internal-Token": _TOKEN}


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
    """HTTP 适配器实际调用 business-mock FastAPI 路由。"""
    from backend.config import customer_service as cs_config
    from backend.config import messaging
    from backend.infra.http import business_client as http_client
    from backend.infra.http.business_client import BusinessServiceError

    def request_json_sync(method, path, *, params=None, json_body=None):
        response = business_client.request(
            method, path, params=params, json=json_body, headers=_HEADERS,
        )
        if not 200 <= response.status_code < 300:
            raise BusinessServiceError(
                f"business-service {method} {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    monkeypatch.setattr(cs_config, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(
        messaging, "BUSINESS_SERVICE_URL", "http://business-mock:8081",
    )
    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(
        http_client, "get_json_sync",
        lambda path, params=None: request_json_sync("GET", path, params=params),
    )
    monkeypatch.setattr(
        http_client, "post_json_sync",
        lambda path, json_body: request_json_sync("POST", path, json_body=json_body),
    )
    return business_client


class _FakeConfirmStore:
    def __init__(self):
        self.saved = []

    def load(self, *_args, **_kwargs):
        return None

    def save(self, *_args, **_kwargs):
        self.saved.append(_args)


@pytest.fixture
def action_identity(monkeypatch):
    from backend.customer_service import confirmation_store
    from backend.customer_service.security.permission import PermissionChecker

    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    monkeypatch.setattr(
        PermissionChecker, "validate_user_identity", lambda _state: "customer-urge",
    )
    return store


def _first_order(client, user_id: str, status: str) -> dict:
    orders = business_mock._orders_for(user_id)
    return next(order for order in orders if order["status"] == status)


def _action_state(user_id: str, order_no: str, intent: str) -> dict:
    return {
        "user_id": user_id,
        "session_id": "cs-urge-session",
        "conversation_id": "cs-urge-conversation",
        "tenant_id": "tenant-urge",
        "cs_context": {"tenant_id": "tenant-urge"},
        "cs_route": {
            "intent": intent,
            "route_path": "business_action",
            "metadata": {"order_id": order_no},
        },
        "user_message": f"{order_no} 帮我催发货",
        "expert_history": [],
        "cs_audit_entries": [],
    }


def test_urge_intents_route_as_low_risk_actions_and_policy_stays_knowledge():
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.intents import INTENT_PROFILES
    from backend.customer_service.router.types import CSDomain, CSRoutePath

    coarse = CSCoarseRouter()
    fine = CSFineRouter()

    domain, _, _ = coarse.classify("帮我催发货")
    intent, _, _ = fine.classify("帮我催发货", domain)
    assert domain == CSDomain.TRANSACTION
    assert intent == "t_urge_shipping"

    delivery_domain, _, _ = coarse.classify("快递丢了，帮我催一下物流")
    delivery_intent, _, _ = fine.classify(
        "快递丢了，帮我催一下物流", delivery_domain,
    )
    assert delivery_domain == CSDomain.TRANSACTION
    assert delivery_intent == "t_urge_delivery"

    for intent_name in ("t_urge_shipping", "t_urge_delivery"):
        profile = INTENT_PROFILES[intent_name]
        assert profile.requires_auth is True
        assert profile.requires_action is True
        assert profile.risk_level == "low"
        assert profile.route_path == CSRoutePath.BUSINESS_ACTION

    policy_domain, _, _ = coarse.classify("配送一般几天能到？")
    assert policy_domain == CSDomain.KNOWLEDGE


def test_mock_urge_registers_once_per_tenant_order_and_type(http_wire):
    user_id = "customer-urge-mock"
    order = _first_order(http_wire, user_id, "paid")
    order["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    payload = {
        "user_id": user_id,
        "tenant_id": "tenant-a",
        "conversation_id": "conv-a",
        "order_no": order["order_no"],
        "request_type": "shipping",
    }

    first = http_wire.post(
        "/business/urge-requests", json=payload, headers=_HEADERS,
    )
    repeated = http_wire.post(
        "/business/urge-requests", json=payload, headers=_HEADERS,
    )
    other_tenant = http_wire.post(
        "/business/urge-requests",
        json={**payload, "tenant_id": "tenant-b"}, headers=_HEADERS,
    )

    first_data = first.json()["data"]
    repeated_data = repeated.json()["data"]
    other_tenant_data = other_tenant.json()["data"]
    assert first.status_code == 200
    assert first_data["already_registered"] is False
    assert repeated_data["already_registered"] is True
    assert repeated_data["urge_request"]["request_id"] == (
        first_data["urge_request"]["request_id"]
    )
    assert other_tenant_data["already_registered"] is False
    assert other_tenant_data["urge_request"]["request_id"] != (
        first_data["urge_request"]["request_id"]
    )
    assert first_data["logistics"]["status_display"] == "已支付，尚未发货"


def test_mock_rejects_foreign_order_and_ineligible_state_without_registration(
    http_wire,
):
    foreign_order = _first_order(http_wire, "urge-order-owner", "paid")
    foreign = http_wire.post(
        "/business/urge-requests",
        json={
            "user_id": "different-customer",
            "tenant_id": "tenant-a",
            "conversation_id": "conv-a",
            "order_no": foreign_order["order_no"],
            "request_type": "shipping",
        },
        headers=_HEADERS,
    )

    shipped_order = _first_order(http_wire, "urge-state-owner", "shipped")
    wrong_state = http_wire.post(
        "/business/urge-requests",
        json={
            "user_id": "urge-state-owner",
            "tenant_id": "tenant-a",
            "conversation_id": "conv-a",
            "order_no": shipped_order["order_no"],
            "request_type": "shipping",
        },
        headers=_HEADERS,
    )

    assert foreign.status_code == 404
    assert wrong_state.status_code == 409
    assert not business_mock._URGE_REQUESTS


def test_action_registers_mock_without_confirmation_and_records_audit(
    http_wire, action_identity,
):
    from backend.customer_service.action import ActionType

    order = _first_order(http_wire, "customer-urge", "paid")
    order["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    state = _action_state("customer-urge", order["order_no"], "t_urge_shipping")

    update = action_expert_node(state)

    result = update["cs_action_result"]
    record = result["action_record"]
    assert "pending_action" not in update
    assert "pending_action" not in update["cs_context"]
    assert result["action_type"] == ActionType.URGE_REQUEST
    assert result["status"] == "success"
    assert record["status"] == "simulated"
    assert record["confirmation_state"] == "not_required"
    assert record["data"]["after_state"]["request_id"].startswith("URGE-")
    assert update["cs_audit_entries"][0]["result"] == "success"
    assert record["data"]["after_state"]["request_id"] in (
        update["cs_audit_entries"][0]["detail"]
    )
    answer = update["last_expert_result"]["response_draft"]
    assert "已在模拟环境" in answer
    assert "没有向真实商家或物流系统发送请求" in answer
    assert "不承诺具体" in answer
    assert not action_identity.saved


def test_delivery_action_registers_mock_without_confirmation(
    http_wire, action_identity,
):
    order = _first_order(http_wire, "customer-urge", "shipped")
    state = _action_state("customer-urge", order["order_no"], "t_urge_delivery")
    state["user_message"] = f"{order['order_no']} 帮我催一下物流"

    update = action_expert_node(state)

    record = update["cs_action_result"]["action_record"]
    assert record["status"] == "simulated"
    assert record["confirmation_state"] == "not_required"
    assert record["data"]["after_state"]["request_type"] == "delivery"
    assert "催派送" in update["last_expert_result"]["response_draft"]
    assert not action_identity.saved


def test_repeated_action_reuses_request_id_and_keeps_attempt_auditable(
    http_wire, action_identity,
):
    order = _first_order(http_wire, "customer-urge", "paid")
    order["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    state = _action_state("customer-urge", order["order_no"], "t_urge_shipping")

    first = action_expert_node(state)
    repeated = action_expert_node(state)

    first_record = first["cs_action_result"]["action_record"]
    repeated_record = repeated["cs_action_result"]["action_record"]
    assert first_record["data"]["after_state"]["request_id"] == (
        repeated_record["data"]["after_state"]["request_id"]
    )
    assert repeated_record["data"]["after_state"]["already_registered"] is True
    assert "已在处理中" in repeated["last_expert_result"]["response_draft"]
    assert first_record["action_id"] != repeated_record["action_id"]


def test_stale_order_auto_routes_to_handoff_without_registering_urge(
    http_wire, action_identity,
):
    from backend.customer_service.graph_state import CS_HANDOFF_EXPERT
    from backend.customer_service.supervisor import (
        ExpertAction,
        make_supervisor_decision,
    )

    order = _first_order(http_wire, "customer-urge", "paid")
    state = _action_state("customer-urge", order["order_no"], "t_urge_shipping")
    action_update = action_expert_node(state)
    last_result = action_update["last_expert_result"]
    assert last_result["data"]["handoff_required"] is True
    assert not business_mock._URGE_REQUESTS

    decision = make_supervisor_decision({
        **state,
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 1,
        "expert_history": [{"expert": "action"}],
        "last_expert_result": last_result,
    })
    assert decision["next_action"] == ExpertAction.RUN_EXPERT.value
    assert decision["next_expert"] == "handoff"
    assert decision["requires_handoff"] is True
    assert CS_HANDOFF_EXPERT == "cs_handoff_expert"


def test_reported_lost_package_skips_registration_and_routes_human(
    http_wire, action_identity,
):
    order = _first_order(http_wire, "customer-urge", "shipped")
    state = _action_state("customer-urge", order["order_no"], "t_urge_delivery")
    state["user_message"] = f"{order['order_no']} 快递丢了，帮我催物流"

    update = action_expert_node(state)

    assert update["last_expert_result"]["data"]["handoff_required"] is True
    assert "丢失" in update["last_expert_result"]["data"]["handoff_reason"]
    assert not business_mock._URGE_REQUESTS


def test_handoff_node_returns_top_level_waiting_state(monkeypatch):
    from backend.customer_service.experts import handoff as handoff_module

    monkeypatch.setattr(
        handoff_module,
        "execute_handoff",
        lambda _message, _state: {
            "expert": "handoff",
            "status": "success",
            "response_draft": "已转人工",
            "data": {
                "handoff_state": "waiting_human",
                "handling_mode": "human",
            },
        },
    )
    from backend.customer_service.experts.handoff import handoff_expert_node

    output = handoff_expert_node({
        "user_message": "物流超过 5 天无更新",
        "expert_history": [],
        "cs_audit_entries": [],
        "cs_context": {},
        "handoff_state": "ai_active",
        "handling_mode": "ai",
    })

    assert output["handoff_state"] == "waiting_human"
    assert output["handling_mode"] == "human"
    assert output["cs_context"]["handoff_state"] == "waiting_human"


def test_business_escalation_is_counted_as_healthy_handoff():
    from backend.customer_service.handoff.handoff import (
        HANDOFF_REASON_BUCKETS,
        TriggerType,
    )

    assert HANDOFF_REASON_BUCKETS[TriggerType.BUSINESS_ESCALATION.value] == (
        "healthy_escalation"
    )


def test_urge_service_refuses_non_mock_gateway_before_post(monkeypatch):
    from backend.config import customer_service as cs_config
    from backend.config import messaging
    from backend.customer_service.service.urge_service import (
        UrgeService,
        UrgeServiceError,
    )
    from backend.infra.http import business_client as http_client

    monkeypatch.setattr(cs_config, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(
        messaging, "BUSINESS_SERVICE_URL", "https://business.example",
    )
    monkeypatch.setattr(
        http_client,
        "post_json_sync",
        lambda *_args, **_kwargs: pytest.fail("不应调用真实业务 URL"),
    )
    from backend.customer_service.security.permission import PermissionChecker

    monkeypatch.setattr(PermissionChecker, "validate_order_id", lambda _order: None)

    with pytest.raises(UrgeServiceError, match="仅在 business-mock"):
        UrgeService().register_urge(
            user_id="customer-urge",
            tenant_id="tenant-urge",
            conversation_id="cs-urge-conversation",
            order_no="MO-12345678",
            request_type="shipping",
        )


def test_unknown_urge_post_result_escalates_without_retry(monkeypatch):
    from backend.config import customer_service as cs_config
    from backend.config import messaging
    from backend.customer_service.security.permission import PermissionChecker
    from backend.customer_service.service.urge_service import (
        UrgeService,
        UrgeServiceError,
    )
    from backend.infra.http import business_client as http_client
    from backend.infra.http.business_client import BusinessServiceError

    monkeypatch.setattr(cs_config, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(
        messaging, "BUSINESS_SERVICE_URL", "http://business-mock:8081",
    )
    monkeypatch.setattr(PermissionChecker, "validate_order_id", lambda _order: None)
    calls = []

    def post_unknown(*_args, **_kwargs):
        calls.append(1)
        raise BusinessServiceError("timeout")

    monkeypatch.setattr(http_client, "post_json_sync", post_unknown)

    with pytest.raises(UrgeServiceError) as raised:
        UrgeService().register_urge(
            user_id="customer-urge",
            tenant_id="tenant-urge",
            conversation_id="cs-urge-conversation",
            order_no="MO-12345678",
            request_type="shipping",
        )

    assert raised.value.handoff_required is True
    assert "请勿重复提交" in raised.value.user_message
    assert len(calls) == 1


def test_invalid_order_reference_is_rejected_before_post(monkeypatch):
    from backend.config import customer_service as cs_config
    from backend.config import messaging
    from backend.customer_service.service.urge_service import (
        UrgeService,
        UrgeServiceError,
    )
    from backend.infra.http import business_client as http_client

    monkeypatch.setattr(cs_config, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(
        messaging, "BUSINESS_SERVICE_URL", "http://business-mock:8081",
    )
    monkeypatch.setattr(
        http_client,
        "post_json_sync",
        lambda *_args, **_kwargs: pytest.fail("无效订单不得请求 mock"),
    )

    with pytest.raises(UrgeServiceError) as raised:
        UrgeService().register_urge(
            user_id="customer-urge",
            tenant_id="tenant-urge",
            conversation_id="cs-urge-conversation",
            order_no="bad/order",
            request_type="shipping",
        )

    assert raised.value.handoff_required is False


def test_urge_action_audit_persists_idempotently_in_isolated_memory_pg(
    http_wire, action_identity,
):
    """用隔离 PostgreSQL 验证 B5 模拟动作审计与幂等记录真实落库。"""
    import os
    from uuid import uuid4

    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG
    from backend.orchestration.graph.cs_graph_node import _persist_audit_records

    if os.getenv("CS_AUDIT_PG_TEST") != "1":
        pytest.skip("设置 CS_AUDIT_PG_TEST=1 才执行隔离库审计验收")

    dbname = str(MEMORY_DB_CONFIG.get("dbname") or "").lower()
    host = str(MEMORY_DB_CONFIG.get("host") or "").strip().lower()
    port = int(MEMORY_DB_CONFIG.get("port") or 0)
    assert (
        dbname.endswith("_test") or dbname.startswith("test_")
        or "sandbox" in dbname
    ), "审计验收仅允许 test/sandbox 数据库"
    assert host in {"localhost", "127.0.0.1", "::1"}, "审计验收仅允许本机 PostgreSQL"
    assert port != 5433, "拒绝连接共享栈 PostgreSQL 端口 5433"

    nonce = uuid4().hex
    conversation_id = f"b5-audit-{nonce}"
    user_id = "customer-urge"
    order = _first_order(http_wire, user_id, "paid")
    order["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    state = _action_state(user_id, order["order_no"], "t_urge_shipping")
    state["conversation_id"] = conversation_id
    update = action_expert_node(state)
    audit_entries = update["cs_audit_entries"]
    action_record = update["cs_action_result"]["action_record"]

    assert len(audit_entries) == 1
    assert action_record["action_type"] == "urge_request"
    assert action_record["status"] == "simulated"
    assert action_record["confirmation_state"] == "not_required"

    action_id = action_record["action_id"]
    audit_entry = audit_entries[0]
    conn = psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=10)
    conversation_created = False
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_setting('transaction_read_only')",
            )
            target_db, read_only = cursor.fetchone()
            assert target_db == MEMORY_DB_CONFIG["dbname"]
            assert read_only == "off"
            cursor.execute(
                "SELECT to_regclass('customer_service.conversations'), "
                "to_regclass('customer_service.agent_actions'), "
                "to_regclass('customer_service.audit_logs'), "
                "to_regclass('ai.idempotency_records')",
            )
            assert all(cursor.fetchone()), "客服审计迁移未初始化到隔离测试库"
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'customer_service' "
                "AND table_name = 'conversations'",
            )
            conversation_columns = {row[0] for row in cursor.fetchall()}
            if "conversation_status" in conversation_columns:
                cursor.execute(
                    "INSERT INTO customer_service.conversations "
                    "(conversation_id, user_id, conversation_status) "
                    "VALUES (%s, %s, 'open')",
                    (conversation_id, user_id),
                )
            elif "status" in conversation_columns:
                cursor.execute(
                    "INSERT INTO customer_service.conversations "
                    "(conversation_id, user_id, status) "
                    "VALUES (%s, %s, 'active')",
                    (conversation_id, user_id),
                )
            else:
                pytest.fail("客服会话表缺少可识别的状态列")
        conn.commit()
        conversation_created = True

        persisted_state = {
            "cs_audit_entries": audit_entries,
            "cs_action_result": {"action_record": action_record},
        }
        _persist_audit_records(persisted_state)
        _persist_audit_records(persisted_state)

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT status, confirmation_state, action_type, target_type, "
                "target_id, after_state FROM customer_service.agent_actions "
                "WHERE action_id = %s",
                (action_id,),
            )
            action_row = cursor.fetchone()
            assert action_row is not None
            assert action_row[:5] == (
                "simulated", "not_required", "urge_request", "order",
                order["order_no"],
            )
            assert action_row[5]["request_id"].startswith("URGE-")
            assert action_row[5]["simulated"] is True

            cursor.execute(
                "SELECT action, result, resource_type, resource_id, error_detail "
                "FROM customer_service.audit_logs WHERE log_id = %s",
                (audit_entry["log_id"],),
            )
            audit_row = cursor.fetchone()
            assert audit_row is not None
            assert audit_row[:4] == (
                "urge_request", "success", "order", order["order_no"],
            )
            assert action_row[5]["request_id"] in audit_row[4]

            cursor.execute(
                "SELECT count(*) FROM customer_service.agent_actions "
                "WHERE action_id = %s",
                (action_id,),
            )
            assert cursor.fetchone()[0] == 1
            cursor.execute(
                "SELECT count(*) FROM customer_service.audit_logs WHERE log_id = %s",
                (audit_entry["log_id"],),
            )
            assert cursor.fetchone()[0] == 1
            cursor.execute(
                "SELECT count(*) FROM ai.idempotency_records "
                "WHERE operation = 'cs.action.persist' AND client_key = %s",
                (action_id,),
            )
            assert cursor.fetchone()[0] == 1
    finally:
        try:
            conn.rollback()
            if conversation_created:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM customer_service.agent_actions "
                        "WHERE action_id = %s",
                        (action_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_service.audit_logs WHERE log_id = %s",
                        (audit_entry["log_id"],),
                    )
                    cursor.execute(
                        "DELETE FROM ai.idempotency_records "
                        "WHERE operation = 'cs.action.persist' AND client_key = %s",
                        (action_id,),
                    )
                    cursor.execute(
                        "DELETE FROM customer_service.conversations "
                        "WHERE conversation_id = %s",
                        (conversation_id,),
                    )
                conn.commit()
        finally:
            conn.close()
