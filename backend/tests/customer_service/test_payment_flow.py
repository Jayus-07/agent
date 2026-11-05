"""cs-capability-gap B4：支付流水查询与重复扣款退款提案。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from backend.customer_service.experts.query import execute_query
from backend.customer_service.service.payment_service import PaymentService
from backend.mock.business_service import main as business_mock

_TOKEN = "mock-internal-test-token"
_HEADERS = {"X-Internal-Token": _TOKEN}
_REFUND_REQUEST = (
    "订单 MO-12345678 是不是重复扣款？如果确认是，请帮我申请退款"
)


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
    """HTTP Service 请求走真实 business-mock 路由。"""
    from backend.config import customer_service as cfg
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

    monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(
        http_client, "get_json_sync",
        lambda path, params=None: request_json_sync("GET", path, params=params),
    )
    return business_client


def _payment(client, order_no: str, user_id: str = "customer-payment") -> dict:
    response = client.get(
        "/business/payments",
        params={
            "user_id": user_id,
            "tenant_id": "tenant-payment",
            "order_no": order_no,
        },
        headers=_HEADERS,
    )
    return response.json()["data"]["payment"]


def test_payment_intent_routing_keeps_policy_question_in_knowledge():
    """支付流水走交易域；纯「支付失败怎么办」沿用政策知识域口径。"""
    from backend.customer_service.router.coarse_router import CSCoarseRouter
    from backend.customer_service.router.fine_router import CSFineRouter
    from backend.customer_service.router.types import CSDomain

    question = "查一下订单 MO-12345678 的支付流水"
    domain, _, _ = CSCoarseRouter().classify(question)
    assert domain == CSDomain.TRANSACTION
    intent, confidence, _ = CSFineRouter().classify(question, domain)
    assert intent == "t_payment_issue"
    assert confidence > 0

    compound_domain, _, _ = CSCoarseRouter().classify(_REFUND_REQUEST)
    compound_intent, _, _ = CSFineRouter().classify(
        _REFUND_REQUEST, compound_domain,
    )
    assert compound_intent == "t_payment_issue"

    policy_domain, _, _ = CSCoarseRouter().classify("支付失败怎么办？")
    assert policy_domain == CSDomain.KNOWLEDGE


def test_duplicate_payment_request_dispatches_query_task_before_refund():
    from backend.customer_service.graph_state import CS_QUERY_EXPERT
    from backend.customer_service.router.cs_router import CSRouter
    from backend.customer_service.router.intents import resolve_route_path
    from backend.customer_service.understanding.task_plan import (
        build_rule_task_plan,
    )
    from backend.customer_service.supervisor import cs_supervisor_node

    route = CSRouter().route(_REFUND_REQUEST)
    plan = build_rule_task_plan(_REFUND_REQUEST)
    state = {
        "user_message": _REFUND_REQUEST,
        "user_id": "872", "tenant_id": "default",
        "session_id": "b4-plan-test", "conversation_id": "b4-plan-test",
        "cs_route": route.model_dump(),
        "handoff_state": "ai_active", "handling_mode": "ai",
        "confirmation_state": "not_required", "expert_loop_count": 0,
        "expert_history": [], "task_plan": plan,
        "task_plan_source": "rule_candidate", "task_cursor": 0,
        "task_results": [], "current_task": None,
    }

    assert route.intent == "t_payment_issue"
    assert resolve_route_path(route.intent).value == "business_query"
    command = cs_supervisor_node(state)
    assert command.goto == CS_QUERY_EXPERT
    assert command.update["current_task"]["capability"] == "query_payments"


def test_payment_signal_is_exempt_from_out_of_scope():
    from backend.customer_service.vocab import match_cs_signal_exempt

    assert match_cs_signal_exempt("订单支付失败，帮我查流水")


@pytest.mark.parametrize("decision_v2", [True, False])
def test_successful_payment_policy_answer_is_not_replaced_by_repeat(
    decision_v2, monkeypatch,
):
    from backend.config import customer_service as cs_config
    from backend.customer_service.graph_state import CS_REPORTER
    from backend.customer_service.router.cs_router import CSRouter
    from backend.customer_service.supervisor import cs_supervisor_node

    monkeypatch.setattr(cs_config, "CS_DECISION_V2", decision_v2)
    route = CSRouter().route("支付失败怎么办？")
    state = {
        "user_message": "支付失败怎么办？",
        "cs_route": route.model_dump(),
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 1,
        "expert_history": [{"expert": "knowledge", "status": "success"}],
        "last_expert_result": {
            "expert": "knowledge",
            "status": "success",
            "response_draft": "支付失败时可以稍后重试，或更换支付方式。",
            "evidence": [{"source": "payment-faq-policy.md"}],
        },
    }

    assert route.domain.value == "KNOWLEDGE"
    assert route.confidence >= 0.60
    command = cs_supervisor_node(state)

    assert command.goto == CS_REPORTER
    assert command.update["supervisor_decision"]["next_action"] == "finish"
    assert "已成功生成" in command.update["supervisor_decision"]["reason"]


@pytest.mark.parametrize("decision_v2", [True, False])
@pytest.mark.parametrize(
    ("history_status", "result_status", "response_draft"),
    [("failed", "failed", "查询失败"), ("success", "success", "  ")],
)
def test_failed_or_empty_knowledge_result_does_not_block_retry(
    decision_v2, history_status, result_status, response_draft, monkeypatch,
):
    from backend.config import customer_service as cs_config
    from backend.customer_service.graph_state import CS_KNOWLEDGE_EXPERT
    from backend.customer_service.router.cs_router import CSRouter
    from backend.customer_service.supervisor import cs_supervisor_node

    monkeypatch.setattr(cs_config, "CS_DECISION_V2", decision_v2)
    route = CSRouter().route("支付失败怎么办？")
    state = {
        "user_message": "支付失败怎么办？",
        "cs_route": route.model_dump(),
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 1,
        "expert_history": [{
            "expert": "knowledge", "status": history_status,
        }],
        "last_expert_result": {
            "expert": "knowledge",
            "status": result_status,
            "response_draft": response_draft,
        },
    }

    command = cs_supervisor_node(state)

    assert command.goto == CS_KNOWLEDGE_EXPERT
    assert command.update["supervisor_decision"]["next_action"] == "run_expert"


@pytest.mark.parametrize("decision_v2", [True, False])
def test_confirmation_pending_precedes_completed_knowledge_result(
    decision_v2, monkeypatch,
):
    from backend.config import customer_service as cs_config
    from backend.customer_service.graph_state import CS_REPORTER
    from backend.customer_service.router.cs_router import CSRouter
    from backend.customer_service.supervisor import cs_supervisor_node

    monkeypatch.setattr(cs_config, "CS_DECISION_V2", decision_v2)
    route = CSRouter().route("支付失败怎么办？")
    state = {
        "user_message": "支付失败怎么办？",
        "cs_route": route.model_dump(),
        "handoff_state": "ai_active",
        "confirmation_state": "pending",
        "expert_loop_count": 1,
        "expert_history": [{"expert": "knowledge", "status": "success"}],
        "last_expert_result": {
            "expert": "knowledge",
            "status": "success",
            "response_draft": "支付失败时可以稍后重试，或更换支付方式。",
        },
    }

    command = cs_supervisor_node(state)

    assert command.goto == CS_REPORTER
    assert command.update["supervisor_decision"]["next_action"] == "pending"


def test_mock_payment_lazy_seed_covers_three_states(http_wire):
    orders = http_wire.get(
        "/business/orders", params={"user_id": "customer-payment"},
        headers=_HEADERS,
    ).json()["data"]["orders"]
    by_status = {order["status"]: order for order in orders}

    single = _payment(http_wire, by_status["paid"]["order_no"])
    failed = _payment(http_wire, by_status["pending"]["order_no"])
    duplicate = _payment(http_wire, by_status["shipped"]["order_no"])

    assert single["success_count"] == 1
    assert [item["status"] for item in single["payments"]] == ["success"]
    assert failed["success_count"] == 0
    assert [item["status"] for item in failed["payments"]] == ["failed"]
    assert duplicate["success_count"] == 2
    assert duplicate["duplicate_payment"] is True
    assert duplicate["refund_candidate"] is True
    assert all(
        item["amount"] == duplicate["total_amount"]
        for item in duplicate["payments"]
    )
    assert duplicate["currency"] == "CNY"


def test_mock_payment_query_checks_order_ownership(http_wire):
    order = http_wire.get(
        "/business/orders", params={"user_id": "customer-payment"},
        headers=_HEADERS,
    ).json()["data"]["orders"][0]
    response = http_wire.get(
        "/business/payments",
        params={
            "user_id": "other-customer", "tenant_id": "tenant-payment",
            "order_no": order["order_no"],
        },
        headers=_HEADERS,
    )
    assert response.status_code == 404


def test_payment_service_http_mode_normalizes_payment_facts(http_wire):
    order = next(
        item for item in http_wire.get(
            "/business/orders", params={"user_id": "customer-payment"},
            headers=_HEADERS,
        ).json()["data"]["orders"] if item["status"] == "shipped"
    )

    payment = PaymentService().get_payments(
        "customer-payment", "tenant-payment", order["order_no"],
    )

    assert payment["order_no"] == order["order_no"]
    assert payment["currency"] == "CNY"
    assert payment["total_amount"] == 699.0
    assert payment["success_count"] == 2
    assert payment["refund_candidate"] is True
    assert all(item["paid_at"] for item in payment["payments"])


def test_payment_view_requires_complete_full_amount_duplicate_records():
    from backend.customer_service.service.payment_service import _payment_view

    base = {
        "order_no": "MO-12345678", "total_amount": "699.00",
        "payments": [
            {"status": "success", "amount": "699.00", "kind": "alipay"},
            {"status": "success", "amount": "699.00", "kind": "wechat_pay"},
        ],
    }
    assert _payment_view(base)["refund_candidate"] is True

    mismatched = {
        **base,
        "payments": [
            {"status": "success", "amount": "699.00", "kind": "alipay"},
            {"status": "success", "amount": "350.00", "kind": "wechat_pay"},
        ],
    }
    assert _payment_view(mismatched)["duplicate_payment"] is True
    assert _payment_view(mismatched)["refund_candidate"] is False

    truncated = {
        **base,
        "payments": base["payments"] + [
            {"status": "failed", "amount": "699.00", "kind": "bank_card"}
            for _ in range(19)
        ],
    }
    assert len(truncated["payments"]) == 21
    assert _payment_view(truncated)["success_count"] == 2
    assert _payment_view(truncated)["refund_candidate"] is False


@patch(
    "backend.customer_service.security.permission.PermissionChecker"
    ".validate_user_identity",
    return_value="872",
)
@patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
@patch("backend.customer_service.service.payment_service.PaymentService")
def test_payment_query_exports_facts_without_submitting_refund(
    payment_service, _decompose, _identity,
):
    payment_service.return_value.get_payments.return_value = {
        "order_no": "MO-12345678",
        "total_amount": 699.0,
        "currency": "CNY",
        "payments": [
            {
                "status": "success", "amount": 699.0,
                "paid_at": "2026-11-04T09:00:00+00:00", "kind": "alipay",
            },
            {
                "status": "success", "amount": 699.0,
                "paid_at": "2026-11-04T09:01:00+00:00", "kind": "wechat_pay",
            },
        ],
        "success_count": 2,
        "duplicate_payment": True,
        "refund_candidate": True,
    }

    result = execute_query(
        "订单 MO-12345678 是不是重复扣款？",
        {
            "intent": "t_payment_issue", "route_path": "business_query",
            "metadata": {"order_id": "MO-12345678"},
        },
        {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
    )

    assert "重复扣款" in result["response_draft"]
    assert "人工审核" in result["response_draft"]
    assert "确认" in result["response_draft"]
    assert not any(
        claim in result["response_draft"] for claim in ("已退款", "已到账")
    )
    assert "pending_action" not in result["data"]
    assert result["data"]["service_facts"]["payment"]["success_count"] == 2
    assert result["data"]["service_facts"]["payment"]["currency"] == "CNY"
    payment_service.return_value.get_payments.assert_called_once_with(
        "872", "default", "MO-12345678",
    )


@patch(
    "backend.customer_service.security.permission.PermissionChecker"
    ".validate_user_identity",
    return_value="872",
)
@patch("backend.customer_service.experts.query._llm_decompose_intents", return_value=None)
@patch("backend.customer_service.service.payment_service.PaymentService")
def test_payment_query_failure_degrades_politely(
    payment_service, _decompose, _identity,
):
    payment_service.return_value.get_payments.side_effect = RuntimeError("boom")

    result = execute_query(
        "查订单 MO-12345678 的支付流水",
        {"intent": "t_payment_issue", "metadata": {"order_id": "MO-12345678"}},
        {"user_id": "872", "tenant_id": "default", "session_id": "s1"},
    )

    assert "暂时不可用" in result["response_draft"]
    assert "没有发起退款申请" in result["response_draft"]


def test_payment_fact_projection_is_allowlisted_and_currency_safe():
    from backend.customer_service.response.facts import project_fact_set

    projected = project_fact_set({
        "payment": {
            "order_no": "MO-12345678", "total_amount": 699,
            "currency": "CNY", "success_count": 2,
            "duplicate_payment": True, "refund_candidate": True,
            "payments": [
                {
                    "status": "success", "amount": 699,
                    "paid_at": "2026-11-04T09:00:00+00:00", "kind": "alipay",
                },
                {
                    "status": "success", "amount": 699,
                    "paid_at": "2026-11-04T09:01:00+00:00", "kind": "wechat_pay",
                },
            ],
            "customer_id": "872", "tenant_id": "other", "internal_token": "secret",
        },
    })

    assert projected == {
        "payment": {
            "order_no": "MO-12345678", "total_amount": "699", "currency": "CNY",
            "success_count": 2, "duplicate_payment": True, "refund_candidate": True,
            "payments": [
                {
                    "status": "success", "amount": "699",
                    "paid_at": "2026-11-04T09:00:00+00:00", "kind": "alipay",
                },
                {
                    "status": "success", "amount": "699",
                    "paid_at": "2026-11-04T09:01:00+00:00", "kind": "wechat_pay",
                },
            ],
        },
    }

    invalid = project_fact_set({
        "payment": {
            "currency": "BTC", "success_count": -1,
            "payments": [{"status": "settled", "amount": "NaN"}],
        },
    })
    assert invalid == {}


def test_payment_amount_guard_uses_order_label_and_rejects_unsupported_amount():
    from backend.customer_service.response import composer

    facts = {"payment": {
        "order_no": "MO-12345678", "total_amount": "699", "currency": "CNY",
        "payments": [
            {"status": "success", "amount": "699", "kind": "alipay"},
            {"status": "success", "amount": "699", "kind": "wechat_pay"},
        ],
    }}
    assert composer._guard_fact_consistency(
        "订单支付流水核对到两笔成功支付，金额均为 ¥699。", facts,
    )
    assert not composer._guard_fact_consistency(
        "重复支付成功两笔，退款金额为 ¥1398。", facts,
    )


def test_payment_query_reply_uses_template_to_preserve_retry_guidance(monkeypatch):
    from backend.config import customer_service as cfg
    from backend.customer_service.experts.query import _payment_answer
    from backend.customer_service.response import composer

    payment = {
        "order_no": "MO-12345678", "total_amount": 699, "currency": "CNY",
        "payments": [{"status": "failed", "amount": 699}],
        "success_count": 0, "duplicate_payment": False,
        "refund_candidate": False,
    }
    draft = _payment_answer(payment)

    def forbidden_llm(*_args, **_kwargs):
        pytest.fail("支付流水回复必须保留模板中的重试指引")

    monkeypatch.setattr(cfg, "CS_RESPONSE_COMPOSER_ENABLED", True)
    monkeypatch.setattr(composer, "_compose_with_llm", forbidden_llm)
    answer, source = composer.compose_reply(
        "query", draft, {"data": {"payment": payment}},
        {"intent": "t_payment_issue"}, {},
    )

    assert source == "template"
    assert answer == draft
    assert "可稍后重试支付" in answer


def test_duplicate_payment_refund_plan_requires_explicit_request():
    from backend.customer_service.understanding.task_plan import (
        CSTaskPlan,
        build_rule_task_plan,
    )

    plan = build_rule_task_plan(_REFUND_REQUEST)
    assert plan is not None
    parsed = CSTaskPlan.model_validate(plan)
    assert parsed.primary_intent == "t_payment_issue"
    assert [task.capability for task in parsed.tasks] == [
        "query_payments", "propose_refund",
    ]
    assert parsed.tasks[1].condition.fact == "refund_candidate"
    assert parsed.tasks[1].condition.value is True

    assert build_rule_task_plan("订单 MO-12345678 是不是重复扣款？") is None
    assert build_rule_task_plan(
        "订单 MO-12345678 重复扣款，但我不申请退款",
    ) is None


def test_payment_task_query_returns_only_trusted_refund_facts(monkeypatch):
    from backend.customer_service.experts.query import _execute_task_query

    class Service:
        def get_payments(self, user_id, tenant_id, order_no):
            assert (user_id, tenant_id, order_no) == (
                "872", "default", "MO-12345678",
            )
            return {
                "order_no": order_no, "total_amount": 699.0, "currency": "CNY",
                "payments": [], "success_count": 2,
                "duplicate_payment": True, "refund_candidate": True,
            }

    monkeypatch.setattr(
        "backend.customer_service.service.payment_service.PaymentService", Service,
    )
    result = _execute_task_query(
        {"task_id": "q1", "capability": "query_payments"},
        "872", _REFUND_REQUEST,
        {"intent": "t_payment_issue", "metadata": {"order_id": "MO-12345678"}},
        {"tenant_id": "default"},
    )

    task_result = result["data"]["task_result"]
    assert task_result["status"] == "success"
    assert task_result["source"] == "business_payment_service"
    assert task_result["facts"] == {
        "order_count": 1, "order_id": "MO-12345678",
        "order_no": "MO-12345678", "success_count": 2,
        "duplicate_payment": True, "refund_candidate": True,
    }


@pytest.mark.parametrize("refund_candidate", [True, False])
def test_payment_task_proposal_reuses_existing_refund_confirmation(
    refund_candidate, monkeypatch,
):
    from backend.customer_service.experts import action as action_expert
    from backend.customer_service.experts.base import ExpertResult
    from backend.customer_service.understanding.task_plan import build_rule_task_plan

    message = _REFUND_REQUEST
    plan = build_rule_task_plan(message)
    assert plan is not None
    pending = {
        "task_id": "a1", "capability": "propose_refund",
        "depends_on": ["q1"],
        "condition": {"fact": "refund_candidate", "operator": "eq", "value": True},
    }
    calls = []

    def create_existing_proposal(user_id, intent, route, *args, **kwargs):
        calls.append((user_id, intent, route))
        return ExpertResult(
            expert="action", status="success",
            response_draft="确认后提交退款申请，等待人工审核。",
            data={
                "pending_action": {"action_type": "refund_request"},
                "confirmation_state": "pending_confirmation",
            },
        )

    monkeypatch.setattr(
        "backend.customer_service.security.permission.PermissionChecker"
        ".validate_user_identity",
        lambda _state: "872",
    )
    class Store:
        def load(self, user_id, session_id):
            return None

    monkeypatch.setattr(
        "backend.customer_service.confirmation_store.get_confirmation_store",
        Store,
    )
    monkeypatch.setattr(action_expert, "_build_new_proposal", create_existing_proposal)

    result = action_expert.execute_action(
        message,
        {"intent": "t_payment_issue", "metadata": {}},
        {
            "user_id": "872", "tenant_id": "default", "session_id": "s1",
            "task_plan": plan, "current_task": pending,
            "task_results": [{
                "task_id": "q1", "status": "success",
                "source": "business_payment_service",
                "facts": {
                    "order_count": 1, "order_id": "MO-12345678",
                    "order_no": "MO-12345678", "success_count": 2,
                    "duplicate_payment": True,
                    "refund_candidate": refund_candidate,
                },
            }],
        },
    )

    if refund_candidate:
        assert len(calls) == 1
        assert calls[0][1] == "as_refund"
        assert calls[0][2]["metadata"]["order_id"] == "MO-12345678"
        assert calls[0][2]["metadata"]["reason"] == "重复扣款"
        assert result["data"]["pending_action"]["action_type"] == "refund_request"
    else:
        assert calls == []
        assert "没有发起退款申请" in result["response_draft"]


def _sandbox_conn_kwargs() -> dict[str, object] | None:
    dsn = os.getenv("POINTS_SANDBOX_PG_TEST_DSN", "").strip()
    if dsn:
        url = make_url(dsn)
        return {
            "host": url.host,
            "port": url.port,
            "dbname": url.database,
            "user": url.username,
            "password": url.password,
        }
    dbname = os.getenv("POINTS_SANDBOX_PG_DBNAME", "").strip()
    if dbname:
        return {
            "host": os.getenv("PGHOST", "localhost"),
            "port": int(os.getenv("PGPORT", "5432")),
            "dbname": dbname,
            "user": os.getenv("PGUSER", "postgres"),
            "password": os.getenv("PGPASSWORD", ""),
        }
    return None


@pytest.fixture(scope="module")
def payment_sandbox_admin():
    conn_kwargs = _sandbox_conn_kwargs()
    if conn_kwargs is None:
        pytest.skip(
            "设置 POINTS_SANDBOX_PG_TEST_DSN（或 POINTS_SANDBOX_PG_DBNAME + PG*）"
            "后运行隔离 PG 验收",
        )
    admin = psycopg2.connect(**conn_kwargs, connect_timeout=10)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = str(cursor.fetchone()[0]).lower()
        if not (
            database_name.endswith("_test")
            or database_name.startswith("test_")
            or "sandbox" in database_name
        ):
            pytest.fail("支付 sandbox 验收只允许隔离 test/sandbox 数据库")

        migration_dir = Path(__file__).resolve().parents[2] / "sql" / "migrations"
        with admin.cursor() as cursor:
            cursor.execute("SELECT to_regclass('\"order\".orders') IS NOT NULL")
            orders_ready = bool(cursor.fetchone()[0])
        if not orders_ready:
            with admin.cursor() as cursor:
                cursor.execute(
                    (migration_dir / "003_agent_business_schema.sql").read_text(
                        encoding="utf-8",
                    ),
                )
        payment_migration = (migration_dir / "134_cs_payment.sql").read_text(
            encoding="utf-8",
        )
        with admin.cursor() as cursor:
            cursor.execute(payment_migration)
            cursor.execute(payment_migration)
        yield admin
    finally:
        admin.close()


def test_sandbox_payment_reads_idempotent_migration_seed(
    payment_sandbox_admin, monkeypatch,
):
    from backend.config import customer_service as cs_cfg
    from backend.infra import db as infra_db

    conn_kwargs = _sandbox_conn_kwargs()
    monkeypatch.setattr(cs_cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
    monkeypatch.setattr(infra_db, "BUSINESS_DB_CONFIG", dict(conn_kwargs))
    infra_db.reset_engines()
    try:
        payment = PaymentService().get_payments(
            "872", "default", "CSB4-PAY-DUP-872",
        )
    finally:
        infra_db.reset_engines()

    assert payment["success_count"] == 2
    assert payment["duplicate_payment"] is True
    assert payment["refund_candidate"] is True
    with payment_sandbox_admin.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM payment.payment_records"
            " WHERE customer_id = 872 AND transaction_no LIKE 'PAY-872-%'",
        )
        assert cursor.fetchone()[0] == 4
        cursor.execute(
            "SELECT id, pg_sequence_last_value("
            " pg_get_serial_sequence('customer.customers', 'id')::regclass)"
            " FROM customer.customers WHERE id = 872",
        )
        seeded_customer = cursor.fetchone()
        assert seeded_customer is not None
        assert int(seeded_customer[0]) == 872
        assert int(seeded_customer[1]) >= 872
