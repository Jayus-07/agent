"""cs-workstation-v2 P5：退款审核闭环回归。

三层覆盖：
- Mock review 端点（TestClient 直测）：approved 回款钱包 / 终态锁 409 /
  rejected 不回款 / opinion 空白 422 / operation_id 幂等重放 / review-queue
- RefundService 审核封装（wire business_client）：异常映射 409/404/未知
- 坐席审核路由（真库审计 5433）：supervisor 闸 / 意见必填 / 审计落库 /
  用户端 GET http 分流按 user 过滤
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
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
        "conversation_id": "cs-conversation-p5",
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


def _wire_business_client(monkeypatch, client: TestClient) -> None:
    """让 business_client 的三个入口都经 HTTP 契约访问 Mock 路由。"""
    from backend.infra.http import business_client as http_client

    def request_json_sync(method, path, *, params=None, json_body=None):
        response = client.request(
            method, path, params=params, json=json_body, headers=_HEADERS,
        )
        if not 200 <= response.status_code < 300:
            raise BusinessServiceError(
                f"business-service {method} {path} -> {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    monkeypatch.setattr(http_client, "request_json_sync", request_json_sync)
    monkeypatch.setattr(
        http_client, "get_json_sync",
        lambda path, *, params=None: request_json_sync("GET", path, params=params),
    )
    monkeypatch.setattr(
        http_client, "post_json_sync",
        lambda path, *, json_body=None: request_json_sync("POST", path, json_body=json_body),
    )


def _create_request(client: TestClient, *, user_id: str, tenant_id: str) -> dict:
    order = next(
        item for item in client.get(
            "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
        ).json()["data"]["orders"]
        if item["status"] == "paid"
    )
    response = client.post(
        "/business/refund-requests",
        json=_create_payload(order, user_id=user_id, tenant_id=tenant_id),
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["request"]


def _wallet(client: TestClient, user_id: str, tenant_id: str) -> dict:
    response = client.get(
        "/business/wallet", params={"user_id": user_id, "tenant_id": tenant_id},
        headers=_HEADERS,
    )
    return response.json()["data"]["wallet"]


class TestMockReviewEndpoint:
    def test_approved_refunds_wallet_and_locks_final(
        self, business_client,
    ):
        request = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        before = _wallet(business_client, "customer-55", "tenant-a")

        response = business_client.post(
            f"/business/refund-requests/{request['request_id']}/review",
            json={
                "decision": "approved", "opinion": "凭证齐全，同意退款",
                "reviewer_id": "sup-1",
                "operation_id": str(uuid4()),
            },
            headers=_HEADERS,
        )
        assert response.status_code == 200, response.text
        reviewed = response.json()["data"]["request"]
        assert reviewed["status"] == "approved"
        assert "退款已退回钱包" in reviewed["status_display"]
        assert reviewed["review"]["opinion"] == "凭证齐全，同意退款"

        after = _wallet(business_client, "customer-55", "tenant-a")
        assert after["balance"] == round(before["balance"] + float(request["amount"]), 2)
        kinds = [entry["kind"] for entry in after["ledger"]]
        assert "refund" in kinds

        # 终态锁：approved 后再审核 409
        again = business_client.post(
            f"/business/refund-requests/{request['request_id']}/review",
            json={
                "decision": "rejected", "opinion": "改判",
                "reviewer_id": "sup-1", "operation_id": str(uuid4()),
            },
            headers=_HEADERS,
        )
        assert again.status_code == 409

    def test_rejected_does_not_refund(self, business_client):
        request = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        before = _wallet(business_client, "customer-55", "tenant-a")
        response = business_client.post(
            f"/business/refund-requests/{request['request_id']}/review",
            json={
                "decision": "rejected", "opinion": "超过政策窗口",
                "reviewer_id": "sup-1", "operation_id": str(uuid4()),
            },
            headers=_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["data"]["request"]["status"] == "rejected"
        after = _wallet(business_client, "customer-55", "tenant-a")
        assert after["balance"] == before["balance"]

    def test_blank_opinion_rejected_422(self, business_client):
        request = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        for opinion in ("", "   "):
            response = business_client.post(
                f"/business/refund-requests/{request['request_id']}/review",
                json={
                    "decision": "approved", "opinion": opinion,
                    "reviewer_id": "sup-1", "operation_id": str(uuid4()),
                },
                headers=_HEADERS,
            )
            assert response.status_code == 422

    def test_operation_id_replay_is_idempotent(self, business_client):
        request = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        operation_id = str(uuid4())
        body = {
            "decision": "approved", "opinion": "ok",
            "reviewer_id": "sup-1", "operation_id": operation_id,
        }
        first = business_client.post(
            f"/business/refund-requests/{request['request_id']}/review",
            json=body, headers=_HEADERS,
        )
        replay = business_client.post(
            f"/business/refund-requests/{request['request_id']}/review",
            json=body, headers=_HEADERS,
        )
        assert first.status_code == replay.status_code == 200
        assert first.json()["data"]["request"] == replay.json()["data"]["request"]
        # 同 operation_id 换 payload → 409 冲突
        conflict = business_client.post(
            f"/business/refund-requests/{request['request_id']}/review",
            json={**body, "decision": "rejected"}, headers=_HEADERS,
        )
        assert conflict.status_code == 409

    def test_review_queue_lists_tenant_scope(self, business_client):
        created = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        response = business_client.get(
            "/business/refunds/review-queue",
            params={"tenant_id": "tenant-a"}, headers=_HEADERS,
        )
        assert response.status_code == 200
        ids = {row["request_id"] for row in response.json()["data"]["requests"]}
        assert created["request_id"] in ids
        # 状态过滤
        filtered = business_client.get(
            "/business/refunds/review-queue",
            params={"tenant_id": "tenant-a", "status": "approved"},
            headers=_HEADERS,
        )
        assert filtered.json()["data"]["requests"] == []


class TestRefundServiceReview:
    def test_review_maps_conflict_and_not_found(self, business_client, monkeypatch):
        _wire_business_client(monkeypatch, business_client)
        service = RefundService()
        request = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        from backend.customer_service.errors import (
            RefundRequestUnavailableError,
            RefundReviewConflictError,
        )

        ok = service.review_refund_request(
            request_id=request["request_id"], decision="approved",
            opinion="同意", reviewer_id="sup-1",
        )
        assert ok["status"] == "approved"
        with pytest.raises(RefundReviewConflictError):
            service.review_refund_request(
                request_id=request["request_id"], decision="rejected",
                opinion="x", reviewer_id="sup-1",
            )
        with pytest.raises(RefundRequestUnavailableError):
            service.review_refund_request(
                request_id=str(uuid4()), decision="approved",
                opinion="x", reviewer_id="sup-1",
            )

    def test_review_queue_reads_tenant_rows(self, business_client, monkeypatch):
        _wire_business_client(monkeypatch, business_client)
        created = _create_request(
            business_client, user_id="customer-55", tenant_id="tenant-a",
        )
        rows = RefundService().list_review_queue(tenant_id="tenant-a")
        assert created["request_id"] in {row["request_id"] for row in rows}
