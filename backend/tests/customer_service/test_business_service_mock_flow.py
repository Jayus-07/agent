"""business-service Mock 与客服订单/售后适配的契约集成测试。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.customer_service.service.refund_service import RefundService
from backend.infra.http.business_client import BusinessServiceError
from backend.mock.business_service import main as business_mock


_TOKEN = "mock-internal-test-token"
_HEADERS = {"X-Internal-Token": _TOKEN}


def _create_payload(order: dict, *, user_id: str, tenant_id: str) -> dict:
    return {
        "request_id": str(uuid4()),
        "user_id": user_id,
        "tenant_id": tenant_id,
        "conversation_id": "cs-conversation-1",
        "order_no": order["order_no"],
        "expected_order_status": order["status"],
        "expected_amount": order["total_amount"],
        "policy_version": "policy-test-v1",
        "reason": "商品破损",
    }


@pytest.fixture
def business_client(monkeypatch):
    business_mock.reset_mock_state()
    monkeypatch.setattr(business_mock, "_INTERNAL_TOKEN", _TOKEN)
    with TestClient(business_mock.app) as client:
        yield client
    business_mock.reset_mock_state()


def _wire_business_client(monkeypatch, client):
    """让业务适配器经 HTTP 契约访问真实 FastAPI Mock 路由。"""
    from backend.infra.http import business_client as http_client

    def request_json_sync(method, path, *, params=None, json_body=None):
        response = client.request(
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

    def get_json_sync(path, *, params=None):
        return request_json_sync("GET", path, params=params)

    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(http_client, "get_json_sync", get_json_sync)


def test_mock_order_logistics_contract_and_resource_scope(business_client):
    user_id = "customer-55"
    response = business_client.get(
        "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
    )
    assert response.status_code == 200
    orders = response.json()["data"]["orders"]
    headphones = [order for order in orders if "耳机" in order["product_names"]]
    assert len(headphones) == 2

    recent = next(order for order in headphones if order["status"] == "paid")
    created_at = datetime.fromisoformat(recent["created_at"])
    assert timedelta(days=6) < datetime.now(timezone.utc) - created_at < timedelta(days=8)

    detail = business_client.get(
        f"/business/orders/{recent['order_no']}",
        params={"user_id": user_id}, headers=_HEADERS,
    )
    assert detail.json()["data"]["order"]["item_summary"] == "无线耳机"

    logistics = business_client.get(
        f"/business/orders/{recent['order_no']}/logistics",
        params={"user_id": user_id}, headers=_HEADERS,
    ).json()["data"]["logistics"]
    assert logistics["shipping_status"] == "not_shipped"
    assert logistics["events"] == []

    foreign = business_client.get(
        f"/business/orders/{recent['order_no']}",
        params={"user_id": "another-customer"}, headers=_HEADERS,
    )
    assert foreign.status_code == 404
    assert foreign.json() == {
        "code": 40401, "message": "order not found", "data": None,
    }


def test_mock_refund_create_update_review_and_tenant_scope(business_client):
    user_id = "customer-55"
    tenant_id = "tenant-a"
    order = next(
        item for item in business_client.get(
            "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
        ).json()["data"]["orders"]
        if item["status"] == "paid"
    )
    payload = _create_payload(order, user_id=user_id, tenant_id=tenant_id)

    first = business_client.post(
        "/business/refund-requests", json=payload, headers=_HEADERS,
    )
    replay = business_client.post(
        "/business/refund-requests", json=payload, headers=_HEADERS,
    )
    assert first.status_code == replay.status_code == 200
    request = first.json()["data"]["request"]
    assert request["request_no"].startswith("RA-")
    assert request["status"] == "pending_review"
    assert "user_id" not in request and "tenant_id" not in request
    assert replay.json()["data"]["request"] == request

    duplicate = business_client.post(
        "/business/refund-requests",
        json={**payload, "request_id": str(uuid4())},
        headers=_HEADERS,
    )
    assert duplicate.status_code == 409

    operation_id = str(uuid4())
    update_payload = {
        "operation_id": operation_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "reason": "商品破损，外壳有裂痕",
    }
    path = f"/business/refund-requests/{request['request_id']}/reason"
    updated = business_client.patch(path, json=update_payload, headers=_HEADERS)
    replayed_update = business_client.patch(
        path, json=update_payload, headers=_HEADERS,
    )
    assert updated.json()["data"]["request"]["reason"] == update_payload["reason"]
    assert replayed_update.json()["data"]["request"] == updated.json()["data"]["request"]

    other_tenant = business_client.get(
        f"/business/refund-requests/{request['request_id']}",
        params={"user_id": user_id, "tenant_id": "tenant-b"},
        headers=_HEADERS,
    )
    assert other_tenant.status_code == 404
    assert business_client.get(
        "/business/refund-requests",
        params={"user_id": user_id, "tenant_id": "tenant-b"},
        headers=_HEADERS,
    ).json()["data"]["requests"] == []

    business_mock.set_refund_status_for_test(request["request_id"], "reviewing")
    reviewed = business_client.get(
        f"/business/refund-requests/{request['request_id']}",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["request"]
    assert reviewed["status"] == "reviewing"
    assert reviewed["status_display"] == "审核中"

    concurrent_user = "customer-parallel"
    concurrent_order = business_client.get(
        "/business/orders", params={"user_id": concurrent_user},
        headers=_HEADERS,
    ).json()["data"]["orders"][0]
    concurrent_payload = _create_payload(
        concurrent_order, user_id=concurrent_user, tenant_id=tenant_id,
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(
            lambda _index: business_client.post(
                "/business/refund-requests",
                json=concurrent_payload,
                headers=_HEADERS,
            ),
            range(8),
        ))
    assert all(response.status_code == 200 for response in responses)
    concurrent_requests = [
        response.json()["data"]["request"] for response in responses
    ]
    assert all(item == concurrent_requests[0] for item in concurrent_requests)


def test_http_customer_services_complete_refund_flow_and_status_query(
    business_client, monkeypatch,
):
    from backend.config import customer_service as cfg
    from backend.customer_service import confirmation_store
    from backend.customer_service import context_resolver
    from backend.customer_service.context.semantic_slots import resolve_semantic_slots
    from backend.customer_service.experts.action import execute_action
    from backend.customer_service.experts.query import _dispatch_service
    from backend.customer_service.experts.query import execute_query
    from backend.observability import metrics
    from backend.shared import idempotency

    class ConfirmationStore:
        def __init__(self):
            self.cleared = []
            self.pending = {}

        def load(self, user_id, session_id, tenant_id=""):
            return self.pending.get((user_id, session_id))

        def save(self, user_id, session_id, pending_action, tenant_id=""):
            self.pending[(user_id, session_id)] = pending_action

        def claim_for_execution(self, *args, **kwargs):
            return "claimed"

        def clear(self, user_id, session_id, final_state="cancelled"):
            self.cleared.append((user_id, session_id, final_state))
            self.pending.pop((user_id, session_id), None)

    class ContextCache:
        def __init__(self):
            self.values = {}

        def get_json(self, key):
            return self.values.get(key)

        def set_json(self, key, value, ttl=None):
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value

    user_id = "customer-55"
    tenant_id = "tenant-a"
    monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(cfg, "CS_REFUND_REQUEST_MODE", "http")
    monkeypatch.setattr(cfg, "CS_REFUND_POLICY_VERSION", "policy-test-v1")
    monkeypatch.setattr(cfg, "CS_REFUND_WINDOW_DAYS", "30")
    monkeypatch.setattr(cfg, "CS_REFUNDABLE_STATUSES", "paid,shipped,completed")
    _wire_business_client(monkeypatch, business_client)
    store = ConfirmationStore()
    context_cache = ContextCache()
    monkeypatch.setattr(context_resolver, "_cache", lambda: context_cache)
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    monkeypatch.setattr(
        idempotency,
        "run_idempotent_side_effect",
        lambda operation, payload, execute, **kwargs: execute(),
    )
    monkeypatch.setattr(metrics, "record_cs_confirmation", lambda *_args: None)
    monkeypatch.setattr(metrics, "record_cs_action", lambda *_args: None)
    monkeypatch.setattr(metrics, "record_cs_intent", lambda *_args: None)

    resolution = resolve_semantic_slots(
        tenant_id,
        user_id,
        [
            {"name": "product_reference", "value": "耳机"},
            {"name": "time_reference", "value": "上周"},
        ],
    )
    assert resolution.resolved

    first_query = execute_query(
        "查一下我上周买的耳机到哪里了。",
        {
            "intent": "t_logistics",
            "metadata": {
                "semantic_slots": [
                    {"name": "product_reference", "value": "耳机"},
                    {"name": "time_reference", "value": "上周"},
                ],
            },
        },
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "session_id": "cs-conversation-1",
        },
    )
    assert "已支付，尚未发货" in first_query["response_draft"]
    conditional_reference = context_resolver.resolve_turn_reference(
        "如果还没发货，就帮我申请退款",
        tenant_id=tenant_id,
        user_id=user_id,
        session_id="cs-conversation-1",
    )
    assert conditional_reference is not None
    assert conditional_reference.order_id == resolution.order_id

    refund_service = RefundService()
    from backend.customer_service.understanding.task_plan import (
        build_rule_task_plan,
    )
    conditional_message = "如果还没发货，就帮我申请退款"
    plan = build_rule_task_plan(conditional_message)
    assert plan is not None
    route = {
        "intent": "t_logistics",
        "metadata": {"order_id": conditional_reference.order_id},
    }
    task_state = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "session_id": "cs-conversation-1",
        "current_task": plan["tasks"][0],
        "task_plan": plan,
        "task_results": [],
    }
    query_result = execute_query(conditional_message, route, task_state)
    task_result = query_result["data"]["task_result"]
    assert task_result["status"] == "success"
    assert task_result["facts"]["shipping_status"] == "not_shipped"

    action_state = {
        **task_state,
        "cs_context": {"tenant_id": tenant_id},
        "cs_route": route,
        "current_task": plan["tasks"][1],
        "task_results": [task_result],
    }

    def propose_conditional_refund():
        result = execute_action(conditional_message, route, action_state)
        return result["data"]["pending_action"]

    from backend.customer_service.confirmation_flow import process_confirmation

    pending = propose_conditional_refund()
    cancelled = process_confirmation(
        pending,
        "算了，先别申请了",
        user_id,
        "cs-conversation-1",
        tenant_id=tenant_id,
    )
    assert cancelled.kind == "cancelled"
    assert business_client.get(
        "/business/refund-requests",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["requests"] == []

    pending = propose_conditional_refund()
    created = process_confirmation(
        pending, "确认", user_id, "cs-conversation-1", tenant_id=tenant_id,
    )
    assert created.kind == "success"
    created_record = created.action_result["action_record"]["data"]
    assert created_record["execution_mode"] == "business_service"
    assert created_record["funds_refunded"] is False
    request = created_record["request"]
    assert request["status"] == "pending_review"
    assert request["request_no"] in created.answer

    from backend.customer_service.action import build_pending_action

    update_proposal = refund_service.build_refund_reason_update_proposal(
        user_id=user_id, tenant_id=tenant_id, reason="商品破损",
    )
    update_pending = build_pending_action(update_proposal)
    updated = process_confirmation(
        update_pending, "确认", user_id, "cs-conversation-1",
        tenant_id=tenant_id,
    )
    assert updated.kind == "success"
    updated_record = updated.action_result["action_record"]["data"]
    assert updated_record["operation"] == "update_reason"
    assert updated_record["request"]["reason"] == "商品破损"
    assert "原因已更新" in updated.answer

    business_mock.set_refund_status_for_test(request["request_id"], "reviewing")
    answer = _dispatch_service(
        user_id,
        "t_refund_status",
        "刚刚那笔退款申请审核到哪一步了？",
        {"intent": "t_refund_status"},
        tenant_id=tenant_id,
        session_id="cs-conversation-1",
    )
    assert request["request_no"] in answer
    assert "审核中" in answer
    assert "尚未退款" in answer


def test_http_write_response_loss_enters_verifying_without_false_success(
    business_client, monkeypatch,
):
    from backend.config import customer_service as cfg
    from backend.customer_service import confirmation_store
    from backend.customer_service.action import build_pending_action
    from backend.customer_service.confirmation_flow import process_confirmation
    from backend.infra.http import business_client as http_client
    from backend.observability import metrics
    from backend.shared import idempotency

    class ConfirmationStore:
        def __init__(self):
            self.cleared = []

        def claim_for_execution(self, *args, **kwargs):
            return "claimed"

        def clear(self, user_id, session_id, final_state="cancelled"):
            self.cleared.append((user_id, session_id, final_state))

    user_id = "customer-write-unknown"
    tenant_id = "tenant-a"
    monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "http")
    monkeypatch.setattr(cfg, "CS_REFUND_REQUEST_MODE", "http")
    monkeypatch.setattr(cfg, "CS_REFUND_POLICY_VERSION", "policy-test-v1")
    monkeypatch.setattr(cfg, "CS_REFUND_WINDOW_DAYS", "30")
    monkeypatch.setattr(cfg, "CS_REFUNDABLE_STATUSES", "paid,shipped,completed")
    _wire_business_client(monkeypatch, business_client)

    store = ConfirmationStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    monkeypatch.setattr(
        idempotency,
        "run_idempotent_side_effect",
        lambda operation, payload, execute, **kwargs: execute(),
    )
    monkeypatch.setattr(metrics, "record_cs_confirmation", lambda *_args: None)
    monkeypatch.setattr(metrics, "record_cs_action", lambda *_args: None)

    accepted_request = http_client.request_json_sync

    def lose_post_response(method, path, *, params=None, json_body=None):
        response = accepted_request(
            method, path, params=params, json_body=json_body,
        )
        if method.upper() == "POST" and path == "/business/refund-requests":
            raise BusinessServiceError("response lost after commit", status_code=503)
        return response

    monkeypatch.setattr(http_client, "request_json_sync", lose_post_response)

    order = business_client.get(
        "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
    ).json()["data"]["orders"][0]
    proposal = RefundService().build_refund_proposal(
        user_id, order["order_no"], "商品破损",
    )
    outcome = process_confirmation(
        build_pending_action(proposal), "确认", user_id,
        "cs-conversation-unknown", tenant_id=tenant_id,
    )

    assert outcome.kind == "failed"
    assert outcome.confirmation_state == "verifying"
    assert "无法确认最终结果" in outcome.answer
    assert "申请已登记" not in outcome.answer
    assert store.cleared[-1] == (
        user_id, "cs-conversation-unknown", "verifying",
    )
    requests = business_client.get(
        "/business/refund-requests",
        params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    ).json()["data"]["requests"]
    assert len(requests) == 1
    assert requests[0]["status"] == "pending_review"
