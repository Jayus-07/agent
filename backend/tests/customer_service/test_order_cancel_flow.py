"""cs-workstation-v2 P2：取消订单全链路 + 余额查询回归（http 模式，走真实 Mock 契约）。

覆盖：取消提案构建（paid 可提/其他状态拒绝）、确认执行（退款回钱包 + 流水）、
重复确认幂等（不二次退款）、确认时事实变化拒绝执行、余额查询应答、
细分类关键词命中。
"""
from __future__ import annotations

import pytest

from backend.customer_service.action import build_pending_action
from backend.customer_service.errors import OrderNotEligibleError
from backend.customer_service.service.cancel_order_service import CancelOrderService
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
    """http 网关模式 + 业务适配器走真实 Mock 路由 + 幂等/指标旁路。"""
    from backend.config import customer_service as cfg
    from backend.infra.http import business_client as http_client
    from backend.infra.http.business_client import BusinessServiceError
    from backend.observability import metrics

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
    monkeypatch.setattr(http_client, "get_json_sync", lambda path, params=None: request_json_sync("GET", path, params=params))
    monkeypatch.setattr(http_client, "post_json_sync", lambda path, json_body: request_json_sync("POST", path, json_body=json_body))
    from backend.shared import idempotency

    monkeypatch.setattr(
        idempotency,
        "run_idempotent_side_effect",
        lambda operation, payload, execute, **kwargs: execute(),
    )
    monkeypatch.setattr(metrics, "record_cs_confirmation", lambda *_a: None)
    monkeypatch.setattr(metrics, "record_cs_action", lambda *_a: None)
    return business_client


class _FakeConfirmStore:
    """L1 语义等价的确认存储替身：认领闸门 + 终态记录，与 DB 解耦。"""

    def __init__(self):
        self.pending: dict[tuple[str, str], dict] = {}
        self.cleared: list[tuple[str, str, str]] = []

    def save(self, user_id, session_id, pending_action, tenant_id=""):
        self.pending[(user_id, session_id)] = dict(pending_action)

    def load_authoritative(self, user_id, session_id, tenant_id):
        return self.pending.get((user_id, session_id))

    def claim_for_execution(self, user_id, session_id, **kwargs):
        return self.pending.pop((user_id, session_id), {}).get("action_id")

    def clear(self, user_id, session_id, final_state="cancelled"):
        self.cleared.append((user_id, session_id, final_state))
        self.pending.pop((user_id, session_id), None)


@pytest.fixture
def fake_store(monkeypatch):
    from backend.customer_service import confirmation_store

    store = _FakeConfirmStore()
    monkeypatch.setattr(confirmation_store, "get_confirmation_store", lambda: store)
    return store


def _paid_order(client, user_id: str) -> dict:
    return next(
        item for item in client.get(
            "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
        ).json()["data"]["orders"]
        if item["status"] == "paid"
    )


def test_fine_router_hits_cancel_and_balance_intents():
    from backend.customer_service.router.coarse_router import CSDomain
    from backend.customer_service.router.fine_router import CSFineRouter

    router = CSFineRouter()
    intent_cancel, conf_cancel, _ = router.classify("我要取消订单 DEMO-1001", CSDomain.AFTER_SALES)
    assert intent_cancel == "t_order_cancel" and conf_cancel > 0
    intent_balance, _, _ = router.classify("我的账户余额还有多少", CSDomain.TRANSACTION)
    assert intent_balance == "t_balance_query"


def test_cancel_proposal_requires_paid_order(http_wire):
    user_id = "customer-cancel"
    paid = _paid_order(http_wire, user_id)
    proposal = CancelOrderService().build_cancel_order_proposal(
        user_id, paid["order_no"], "不想要了",
    )
    assert proposal.action_type == "order_cancel"
    assert proposal.target_id == paid["id"]
    assert proposal.before_state["amount"] == paid["total_amount"]
    assert "取消订单" in proposal.proposal_text

    shipped = next(
        item for item in http_wire.get(
            "/business/orders", params={"user_id": user_id}, headers=_HEADERS,
        ).json()["data"]["orders"]
        if item["status"] == "shipped"
    )
    with pytest.raises(OrderNotEligibleError):
        CancelOrderService().build_cancel_order_proposal(
            user_id, shipped["order_no"], "不想要了",
        )


def test_confirmed_cancel_refunds_wallet_once(http_wire, fake_store, monkeypatch):
    """确认 → 取消执行 → 钱包回款入账；重复确认不二次退款（幂等）。"""
    from backend.customer_service.confirmation_flow import process_confirmation

    user_id, tenant_id = "customer-wallet2", "tenant-a"
    paid = _paid_order(http_wire, user_id)
    amount = float(paid["total_amount"])
    balance_before = http_wire.get(
        "/business/wallet",
        params={"user_id": user_id, "tenant_id": tenant_id}, headers=_HEADERS,
    ).json()["data"]["wallet"]["balance"]

    pending = build_pending_action(
        CancelOrderService().build_cancel_order_proposal(
            user_id, paid["order_no"], "不想要了",
        ),
    )
    fake_store.save(user_id, "cs-conversation-cancel", pending, tenant_id=tenant_id)
    outcome = process_confirmation(
        pending, "确认", user_id,
        "cs-conversation-cancel", tenant_id=tenant_id,
    )
    assert outcome.kind == "success"
    assert "订单已取消" in outcome.answer
    assert "余额" in outcome.answer
    record = outcome.action_result["action_record"]
    assert record["data"]["funds_refunded"] is True
    assert record["data"]["execution_mode"] == "mall_service"

    wallet = http_wire.get(
        "/business/wallet",
        params={"user_id": user_id, "tenant_id": tenant_id}, headers=_HEADERS,
    ).json()["data"]["wallet"]
    assert wallet["balance"] == balance_before + amount
    assert wallet["ledger"][0]["kind"] == "cancel_refund"

    cancelled_order = http_wire.get(
        f"/business/orders/{paid['order_no']}",
        params={"user_id": user_id}, headers=_HEADERS,
    ).json()["data"]["order"]
    assert cancelled_order["status"] == "cancelled"
    assert cancelled_order["payment_status"] == "refunded"

    # 重复确认：同一 pending 已被认领清空 → duplicate，不二次退款
    replay = process_confirmation(
        pending, "确认", user_id, "cs-conversation-cancel", tenant_id=tenant_id,
    )
    assert replay.kind == "duplicate"
    wallet_after_replay = http_wire.get(
        "/business/wallet",
        params={"user_id": user_id, "tenant_id": tenant_id}, headers=_HEADERS,
    ).json()["data"]["wallet"]
    assert wallet_after_replay["balance"] == balance_before + amount


def test_confirmed_cancel_rejected_when_facts_changed(http_wire, fake_store):
    """确认时订单金额/状态已变化 → 拒绝执行，钱包不动。"""
    from backend.customer_service.confirmation_flow import process_confirmation

    user_id, tenant_id = "customer-cancel3", "tenant-a"
    paid = _paid_order(http_wire, user_id)
    balance_before = http_wire.get(
        "/business/wallet",
        params={"user_id": user_id, "tenant_id": tenant_id}, headers=_HEADERS,
    ).json()["data"]["wallet"]["balance"]

    pending = build_pending_action(
        CancelOrderService().build_cancel_order_proposal(
            user_id, paid["order_no"], "不想要了",
        ),
    )
    fake_store.save(user_id, "cs-conversation-cancel3", pending, tenant_id=tenant_id)
    # 提案后订单被并发改为 shipped（事实变化）
    with business_mock._STATE_LOCK:
        business_mock._find_order(user_id, paid["order_no"])["status"] = "shipped"

    outcome = process_confirmation(
        pending, "确认", user_id,
        "cs-conversation-cancel3", tenant_id=tenant_id,
    )
    assert outcome.kind in {"failed", "error"}
    wallet_after = http_wire.get(
        "/business/wallet",
        params={"user_id": user_id, "tenant_id": tenant_id}, headers=_HEADERS,
    ).json()["data"]["wallet"]
    assert wallet_after["balance"] == balance_before


def test_balance_query_answer_contains_wallet_and_ledger(http_wire):
    from backend.customer_service.experts.query import execute_query

    user_id, tenant_id = "customer-wallet2", "tenant-a"
    result = execute_query(
        "我的账户余额还有多少钱",
        {"intent": "t_balance_query", "metadata": {}},
        {"user_id": user_id, "tenant_id": tenant_id, "session_id": "cs-1"},
    )
    assert result["status"] == "success"
    assert "余额" in result["response_draft"]
    assert "¥" in result["response_draft"]
    facts = result["data"]["service_facts"]["balance"]
    assert facts["currency"] == "CNY"
    assert facts["balance"] >= 5000.0
