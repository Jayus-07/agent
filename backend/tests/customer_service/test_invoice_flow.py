"""B8 发票查询与模拟申请：只经 business-mock，不生成真实发票。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.customer_service.errors import DatabaseError, OrderNotEligibleError
from backend.customer_service.service.invoice_service import InvoiceService
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
    """HTTP 适配器连真实 business-mock 路由，副作用幂等账本使用测试替身。"""
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
                f"business-service {method} {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(cfg, "CS_INVOICE_MODE", "mock")
    monkeypatch.setattr(
        messaging, "BUSINESS_SERVICE_URL", "http://127.0.0.1:8081",
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
    monkeypatch.setattr(
        idempotency, "run_idempotent_side_effect",
        lambda _operation, _payload, execute, **_kwargs: execute(),
    )
    monkeypatch.setattr(metrics, "record_cs_confirmation", lambda *_args: None)
    monkeypatch.setattr(metrics, "record_cs_action", lambda *_args: None)
    return business_client


def _orders(client, user_id: str) -> list[dict]:
    return client.get(
        "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
    ).json()["data"]["orders"]


def _paid_order(client, user_id: str) -> dict:
    return next(item for item in _orders(client, user_id) if item["status"] == "paid")


def test_invoice_route_separates_transaction_queries_from_policy_questions():
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.types import CSDomain

    query = "查询我的发票抬头和开票记录"
    domain, _, _ = CSCoarseRouter().classify(query)
    assert domain == CSDomain.TRANSACTION
    intent, confidence, _ = CSFineRouter().classify(query, CSDomain.TRANSACTION)
    assert intent == "t_invoice_query"
    assert confidence > 0

    request = "我要给订单 MO-6DEC4021 开发票"
    domain, _, _ = CSCoarseRouter().classify(request)
    assert domain == CSDomain.TRANSACTION
    intent, _, _ = CSFineRouter().classify(request, CSDomain.TRANSACTION)
    assert intent == "t_invoice_request"

    for policy_question in ("发票怎么开？", "开票需要哪些资料？"):
        policy_domain, _, _ = CSCoarseRouter().classify(policy_question)
        assert policy_domain == CSDomain.KNOWLEDGE, policy_question


def test_mock_titles_and_invoice_records_are_scoped_by_tenant(http_wire):
    user_id = "customer-invoice-scope"
    tenant_a = http_wire.get(
        "/business/invoice-titles",
        params={"user_id": user_id, "tenant_id": "tenant-a"},
        headers=_HEADERS,
    ).json()["data"]["invoice_titles"]
    tenant_b = http_wire.get(
        "/business/invoice-titles",
        params={"user_id": user_id, "tenant_id": "tenant-b"},
        headers=_HEADERS,
    ).json()["data"]["invoice_titles"]

    assert len(tenant_a) == len(tenant_b) == 2
    assert tenant_a[0]["is_default"] is True
    assert tenant_a[0]["title_id"] != tenant_b[0]["title_id"]
    assert "tax_id" not in tenant_a[0]
    assert http_wire.get(
        "/business/invoices",
        params={"user_id": user_id, "tenant_id": "tenant-b"},
        headers=_HEADERS,
    ).json()["data"]["invoices"] == []


def test_mock_invoice_request_is_idempotent_and_same_title_conflicts(http_wire):
    user_id, tenant_id = "customer-invoice-once", "tenant-invoice"
    order = _paid_order(http_wire, user_id)
    title = http_wire.get(
        "/business/invoice-titles",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["invoice_titles"][0]
    payload = {
        "operation_key": "invoice-operation-0001",
        "user_id": user_id,
        "tenant_id": tenant_id,
        "conversation_id": "cs-invoice-conversation",
        "order_no": order["order_no"],
        "expected_order_status": "paid",
        "title_id": title["title_id"],
        "invoice_type": "electronic",
    }

    first = http_wire.post(
        "/business/invoices", json=payload, headers=_HEADERS,
    )
    replay = http_wire.post(
        "/business/invoices", json=payload, headers=_HEADERS,
    )
    assert first.status_code == 200 and replay.status_code == 200
    assert replay.json()["data"]["idempotent"] is True
    invoice = first.json()["data"]["invoice"]
    assert invoice["status"] == "simulated"
    assert invoice["real_invoice_issued"] is False
    assert invoice["tax_system_contacted"] is False
    assert invoice["invoice_no"].startswith("MOCK-INV-")

    conflict = http_wire.post(
        "/business/invoices",
        json={**payload, "operation_key": "invoice-operation-0002"},
        headers=_HEADERS,
    )
    assert conflict.status_code == 409
    records = http_wire.get(
        "/business/invoices",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["invoices"]
    assert len(records) == 1


def test_mock_invoice_request_rejects_unpaid_order_and_unknown_title(http_wire):
    user_id, tenant_id = "customer-invoice-ineligible", "tenant-invoice"
    pending_order = next(
        item for item in _orders(http_wire, user_id) if item["status"] == "pending"
    )
    title = http_wire.get(
        "/business/invoice-titles",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["invoice_titles"][0]
    payload = {
        "operation_key": "invoice-operation-0003",
        "user_id": user_id,
        "tenant_id": tenant_id,
        "conversation_id": "cs-invoice-conversation",
        "order_no": pending_order["order_no"],
        "expected_order_status": "pending",
        "title_id": title["title_id"],
        "invoice_type": "electronic",
    }
    assert http_wire.post(
        "/business/invoices", json=payload, headers=_HEADERS,
    ).status_code == 409
    paid_order = _paid_order(http_wire, user_id)
    assert http_wire.post(
        "/business/invoices",
        json={
            **payload,
            "operation_key": "invoice-operation-0004",
            "order_no": paid_order["order_no"],
            "expected_order_status": "paid",
            "title_id": "other-user-title",
        },
        headers=_HEADERS,
    ).status_code == 404


def test_invoice_service_builds_medium_confirmation_proposal(http_wire):
    from backend.customer_service.risk import RiskLevel

    user_id, tenant_id = "customer-invoice-proposal", "tenant-invoice"
    order = _paid_order(http_wire, user_id)
    proposal = InvoiceService().build_invoice_proposal(
        user_id, tenant_id, order["order_no"], "企业",
    )

    assert proposal.action_type == "invoice_request"
    assert proposal.risk_level is RiskLevel.MEDIUM
    assert proposal.after_state["title_type"] == "company"
    assert proposal.after_state["real_invoice_issued"] is False
    assert "不会实际开票" in proposal.proposal_text


def test_invoice_action_without_order_asks_instead_of_guessing(
    http_wire, monkeypatch,
):
    from types import SimpleNamespace

    from backend.customer_service import confirmation_store
    from backend.customer_service.context import semantic_slots
    from backend.customer_service.experts.action import execute_action
    from backend.customer_service.security.permission import PermissionChecker

    user_id, tenant_id = "customer-invoice-slot", "tenant-invoice"
    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    monkeypatch.setattr(
        PermissionChecker, "validate_user_identity", lambda _state: user_id,
    )
    monkeypatch.setattr(
        semantic_slots, "resolve_from_metadata",
        lambda *_args: SimpleNamespace(
            resolved=False, ambiguous=False, order_id="", candidates=[],
        ),
    )

    result = execute_action(
        "我想申请开发票",
        {"intent": "t_invoice_request", "metadata": {}},
        {"session_id": "cs-invoice-slot", "cs_context": {"tenant_id": tenant_id}},
    )

    pending = result["data"]["pending_action"]
    assert "订单号" in result["response_draft"]
    assert pending["status"] == "need_info"
    assert pending["missing_slots"] == ["order_id"]
    assert pending["action_type"] == "invoice_request"
    assert pending["risk_level"] == "medium"
    assert store.pending[(user_id, "cs-invoice-slot")] == pending


def test_disabled_or_unallowlisted_invoice_write_fails_closed(
    http_wire, monkeypatch,
):
    from backend.config import customer_service as cfg
    from backend.config import messaging

    user_id = "customer-invoice-disabled"
    order = _paid_order(http_wire, user_id)
    monkeypatch.setattr(cfg, "CS_INVOICE_MODE", "disabled")
    with pytest.raises(OrderNotEligibleError):
        InvoiceService().build_invoice_proposal(
            user_id, "tenant-invoice", order["order_no"],
        )

    monkeypatch.setattr(cfg, "CS_INVOICE_MODE", "mock")
    monkeypatch.setattr(messaging, "BUSINESS_SERVICE_URL", "https://real-tax.example")
    with pytest.raises(DatabaseError, match="business-mock"):
        InvoiceService().build_invoice_proposal(
            user_id, "tenant-invoice", order["order_no"],
        )


class _FakeConfirmStore:
    def __init__(self):
        self.pending: dict[tuple[str, str], dict] = {}
        self.cleared: list[tuple[str, str, str]] = []

    def save(self, user_id, session_id, pending_action, tenant_id=""):
        self.pending[(user_id, session_id)] = dict(pending_action)

    def load_authoritative(self, user_id, session_id, tenant_id):
        return self.pending.get((user_id, session_id))

    def load(self, user_id, session_id, tenant_id=""):
        return self.pending.get((user_id, session_id))

    def claim_for_execution(self, user_id, session_id, **_kwargs):
        return self.pending.pop((user_id, session_id), {}).get("action_id")

    def clear(self, user_id, session_id, final_state="cancelled"):
        self.cleared.append((user_id, session_id, final_state))
        self.pending.pop((user_id, session_id), None)


def _save_confirmation(fake_store, user_id, tenant_id, order_no):
    from backend.customer_service.action import build_pending_action

    proposal = InvoiceService().build_invoice_proposal(
        user_id, tenant_id, order_no,
    )
    pending = build_pending_action(proposal)
    session_id = "cs-invoice-confirmation"
    fake_store.save(user_id, session_id, pending, tenant_id=tenant_id)
    return pending, session_id


def test_confirmed_invoice_request_records_mock_state_only(
    http_wire, monkeypatch,
):
    from backend.customer_service import confirmation_store
    from backend.customer_service.confirmation_flow import process_confirmation

    user_id, tenant_id = "customer-invoice-confirmed", "tenant-invoice"
    order = _paid_order(http_wire, user_id)
    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    pending, session_id = _save_confirmation(
        store, user_id, tenant_id, order["order_no"],
    )

    outcome = process_confirmation(
        pending, "确认", user_id, session_id, tenant_id=tenant_id,
    )
    assert outcome.kind == "success"
    assert "模拟开票申请已登记" in outcome.answer
    assert "未实际开票" in outcome.answer
    record = outcome.action_result["action_record"]
    assert record["status"] == "simulated"
    assert record["data"]["real_invoice_issued"] is False
    assert record["data"]["tax_system_contacted"] is False
    assert store.cleared[-1][2] == "success"


def test_invoice_confirmation_rejects_changed_order_and_audits_context(
    http_wire, monkeypatch,
):
    from backend.customer_service import confirmation_store
    from backend.customer_service.confirmation_flow import process_confirmation

    user_id, tenant_id = "customer-invoice-order-change", "tenant-invoice"
    order = _paid_order(http_wire, user_id)
    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    pending, session_id = _save_confirmation(
        store, user_id, tenant_id, order["order_no"],
    )
    business_mock._ORDERS[(user_id, order["order_no"])]["status"] = "cancelled"

    outcome = process_confirmation(
        pending, "确认", user_id, session_id, tenant_id=tenant_id,
    )

    assert outcome.kind == "failed"
    assert outcome.confirmation_state == "failed"
    assert outcome.audit_entry["result"] == "failure"
    assert outcome.audit_entry["conversation_id"] == session_id
    assert outcome.audit_entry["target_id"] == order["order_no"]
    assert business_mock._INVOICES.get((tenant_id, user_id), []) == []
    assert store.cleared[-1][2] == "failed"


def test_invoice_write_with_unverifiable_response_enters_verifying(
    http_wire, monkeypatch,
):
    from backend.customer_service import confirmation_store
    from backend.customer_service.confirmation_flow import process_confirmation
    from backend.infra.http import business_client as http_client

    user_id, tenant_id = "customer-invoice-unknown", "tenant-invoice"
    order = _paid_order(http_wire, user_id)
    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    pending, session_id = _save_confirmation(
        store, user_id, tenant_id, order["order_no"],
    )
    real_post = http_client.post_json_sync

    def write_then_corrupt_response(path, json_body):
        real_post(path, json_body)
        return {"code": 0, "data": {}}

    monkeypatch.setattr(http_client, "post_json_sync", write_then_corrupt_response)
    outcome = process_confirmation(
        pending, "确认", user_id, session_id, tenant_id=tenant_id,
    )
    assert outcome.kind == "failed"
    assert outcome.confirmation_state == "verifying"
    assert "请勿重复提交" in outcome.answer
    assert store.cleared[-1][2] == "verifying"
    assert len(business_mock._INVOICES[(tenant_id, user_id)]) == 1


@patch(
    "backend.customer_service.security.permission.PermissionChecker.validate_user_identity",
    return_value="customer-invoice-query",
)
@patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
@patch("backend.customer_service.context_manager.resolve_order_slot", return_value="")
@patch("backend.customer_service.service.invoice_service.InvoiceService")
def test_query_expert_exports_safe_invoice_facts(
    invoice_service, _slot, _decompose, _identity,
):
    from backend.customer_service.experts.query import execute_query
    from backend.customer_service.response.facts import project_fact_set

    invoice_service.return_value.get_titles.return_value = [{
        "title_id": "MOCK-TITLE-1", "title_name": "个人抬头（模拟）",
        "title_type": "individual", "is_default": True, "status": "active",
        "tax_id": "must-not-leak",
    }]
    invoice_service.return_value.get_invoices.return_value = [{
        "invoice_no": "MOCK-INV-0001", "order_no": "MO-6DEC4021",
        "title_name": "个人抬头（模拟）", "title_type": "individual",
        "invoice_type": "electronic", "status": "simulated",
        "real_invoice_issued": False, "tax_id": "must-not-leak",
    }]
    result = execute_query(
        "查询我的发票抬头和开票记录",
        {"intent": "t_invoice_query", "route_path": "business_query"},
        {"user_id": "customer-invoice-query", "tenant_id": "tenant-invoice", "session_id": "s1"},
    )

    facts = result["data"]["service_facts"]
    assert facts["invoice_summary"] == {
        "title_count": 1, "invoice_count": 1, "has_invoice_records": True,
    }
    assert "模拟登记，未实际开票" in result["response_draft"]
    projected = project_fact_set(facts)
    assert projected["invoice_titles"][0]["title_name"] == "个人抬头（模拟）"
    assert "tax_id" not in str(projected)


def test_invoice_migration_and_rag_smoke_are_registered():
    from scripts.init_db import MIGRATION_TARGETS

    root = Path(__file__).resolve().parents[3]
    migration_dir = root / "backend" / "sql" / "migrations"
    business_migration = migration_dir / "138_cs_invoice_business.sql"
    audit_migration = migration_dir / "139_cs_invoice_action_type.sql"
    filter_file = root / ".github" / "scripts" / "rag_smoke_path_filter.py"
    workflow = root / ".github" / "workflows" / "rag_smoke.yml"

    assert business_migration.exists()
    assert audit_migration.exists()
    assert MIGRATION_TARGETS[business_migration.name] == "business"
    assert MIGRATION_TARGETS[audit_migration.name] == "memory"
    filter_content = filter_file.read_text(encoding="utf-8")
    assert '"backend/sql/migrations/138_cs_invoice_business.sql"' in filter_content
    assert '"backend/sql/migrations/139_cs_invoice_action_type.sql"' in filter_content
    assert "backend/tests/customer_service/test_invoice_flow.py" in workflow.read_text(
        encoding="utf-8",
    )
    business_sql = business_migration.read_text(encoding="utf-8")
    audit_sql = audit_migration.read_text(encoding="utf-8")
    assert "CHECK (real_invoice_issued IS FALSE)" in business_sql
    assert "'invoice_request'" in audit_sql


def test_invoice_action_and_audit_persist_once_in_isolated_memory_pg():
    """真实 PostgreSQL 验证模拟开票动作、审计与幂等账本同事务落库。"""
    from uuid import uuid4

    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG
    from backend.customer_service.action import AgentActionRecord
    from backend.customer_service.audit import build_audit_entry
    from backend.orchestration.graph.cs_graph_node import _persist_audit_records

    dbname = str(MEMORY_DB_CONFIG.get("dbname") or "").lower()
    host = str(MEMORY_DB_CONFIG.get("host") or "").strip().lower()
    port = int(MEMORY_DB_CONFIG.get("port") or 0)
    if not (
        dbname.endswith("_test")
        or dbname.startswith("test_")
        or "sandbox" in dbname
    ) or host not in {"localhost", "127.0.0.1", "::1"} or port == 5433:
        pytest.skip(
            "审计持久化验收只允许本机隔离 test/sandbox 数据库，"
            "并拒绝共享栈端口 5433",
        )

    conn = psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=10)
    nonce = uuid4().hex
    conversation_id = f"cs-invoice-audit-{nonce}"
    user_id = f"cs-invoice-user-{nonce}"
    action_id = str(uuid4())
    order_no = f"INV-{nonce[:16]}"
    invoice_id = f"mock-invoice-{nonce}"
    audit_entry = build_audit_entry(
        user_id=user_id,
        action_type="invoice_request",
        result="success",
        target_type="order",
        target_id=order_no,
        detail=(
            "execution_mode=simulation; real_invoice_issued=false; "
            "tax_system_contacted=false"
        ),
        conversation_id=conversation_id,
    )
    record = AgentActionRecord(
        action_id=action_id,
        action_type="invoice_request",
        agent_type="ai",
        target_type="order",
        target_id=order_no,
        data={
            "before_state": {
                "order_no": order_no,
                "order_status": "paid",
                "title_id": "mock-title",
            },
            "after_state": {
                "invoice_id": invoice_id,
                "order_no": order_no,
                "status": "simulated",
                "execution_mode": "simulation",
                "real_invoice_issued": False,
                "tax_system_contacted": False,
            },
        },
        status="simulated",
    ).to_dict()
    record["action_id"] = action_id
    state = {
        "cs_audit_entries": [audit_entry],
        "cs_action_result": {"action_record": record},
    }
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
                "INSERT INTO customer_service.conversations "
                "(conversation_id, user_id) VALUES (%s, %s)",
                (conversation_id, user_id),
            )
        conn.commit()
        conversation_created = True

        _persist_audit_records(state)
        _persist_audit_records(state)

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT action_type, status, confirmation_state, target_type, "
                "target_id, after_state FROM customer_service.agent_actions "
                "WHERE action_id = %s",
                (action_id,),
            )
            action_row = cursor.fetchone()
            assert action_row is not None
            assert action_row[:5] == (
                "invoice_request", "simulated", "success", "order", order_no,
            )
            assert action_row[5]["real_invoice_issued"] is False
            assert action_row[5]["tax_system_contacted"] is False

            cursor.execute(
                "SELECT action, result, resource_type, resource_id, error_detail "
                "FROM customer_service.audit_logs WHERE log_id = %s",
                (audit_entry["log_id"],),
            )
            audit_row = cursor.fetchone()
            assert audit_row == (
                "invoice_request", "success", "order", order_no,
                audit_entry["detail"],
            )

            cursor.execute(
                "SELECT count(*) FROM customer_service.agent_actions "
                "WHERE action_id = %s",
                (action_id,),
            )
            assert cursor.fetchone()[0] == 1
            cursor.execute(
                "SELECT count(*) FROM customer_service.audit_logs "
                "WHERE log_id = %s",
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
                        "DELETE FROM customer_service.audit_logs "
                        "WHERE log_id = %s",
                        (audit_entry["log_id"],),
                    )
                    cursor.execute(
                        "DELETE FROM ai.idempotency_records "
                        "WHERE operation = 'cs.action.persist' "
                        "AND client_key = %s",
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
