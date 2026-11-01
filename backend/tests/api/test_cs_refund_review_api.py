"""cs-workstation-v2 P5：坐席退款审核路由回归（supervisor 闸/审计/分流）。

- GET/POST 权限：agent 403、supervisor 放行（http 模式服务层 monkeypatch）
- 意见必填 422；409/404/写结果未知(503) 映射
- 审计落库（真库 agent_memory@5433，p5- 前缀 audit_logs 用后即清）
- 用户端 GET /cs/refund-requests http 分流按 user 过滤（跨用户不可见）
"""
from __future__ import annotations

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_agent_offers, cs_refund_review
from backend.customer_service.errors import (
    RefundRequestUnavailableError,
    RefundReviewConflictError,
)
from backend.shared.idempotency import SideEffectOutcomeUnknown

TENANT = "tenant-p5t"
_PREFIX = "p5-"


def _pg():
    # CI 无 .env（批十/十二同款 .env 文件依赖）：统一 MEMORY_DB_CONFIG 口径
    from backend.config.database import MEMORY_DB_CONFIG

    return psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2)


def _cleanup():
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM customer_service.audit_logs WHERE user_id LIKE %s",
            (_PREFIX + "%",),
        )
        conn.commit()


@pytest.fixture
def db():
    _cleanup()
    yield
    _cleanup()


def _context(*, agent_id: str, cs_role: str = "agent") -> cs_agent_offers.AgentContext:
    return cs_agent_offers.AgentContext(
        agent_id=agent_id,
        tenant_id=TENANT,
        user_id=_PREFIX + "auth-user",
        cs_role=cs_role,
        platform_roles=("viewer",),
    )


@pytest.fixture
def supervisor_client(db, monkeypatch):
    """supervisor 身份 + http 模式 + 服务层 stub（队列 1 行 pending）。"""
    import backend.customer_service.service.refund_service as refund_mod

    ctx = _context(agent_id=_PREFIX + "sup", cs_role="supervisor")
    app = FastAPI()
    app.include_router(cs_refund_review.router)
    app.dependency_overrides[cs_refund_review.require_agent] = lambda: ctx

    async def fake_session():
        # 真库 session：审计必须真实落库（T2 断言 audit_logs 行）
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[cs_refund_review.get_session] = fake_session

    monkeypatch.setattr(
        refund_mod.RefundService, "_review_mode",
        lambda self: "http",
    )
    queue_row = {
        "request_id": _PREFIX + "req-1", "request_no": "RA-P5TEST0001",
        "status": "pending_review", "status_display": "等待人工审核",
        "order_no": "DEMO-1001", "amount": "398.00", "reason": "商品破损",
        "policy_version": "policy-v1", "user_id": _PREFIX + "user-a",
        "review": None, "created_at": "2026-10-31T10:00:00Z",
        "updated_at": "2026-10-31T10:00:00Z",
    }
    monkeypatch.setattr(
        refund_mod.RefundService, "list_review_queue",
        lambda self, *, tenant_id, status=None, limit=50: (
            [queue_row] if tenant_id == TENANT else []
        ),
    )
    reviewed = {
        "request_id": _PREFIX + "req-1", "status": "approved",
        "status_display": "审核通过，退款已退回钱包",
        "review": {"decision": "approved", "opinion": "凭证齐全", "reviewer_id": ctx.agent_id},
    }
    review_calls: list[dict] = []

    def fake_review(self, *, request_id, decision, opinion, reviewer_id):
        review_calls.append(
            {"request_id": request_id, "decision": decision,
             "opinion": opinion, "reviewer_id": reviewer_id},
        )
        return dict(reviewed)

    monkeypatch.setattr(
        refund_mod.RefundService, "review_refund_request", fake_review,
    )
    return TestClient(app, raise_server_exceptions=False), ctx, review_calls


def test_queue_requires_supervisor(supervisor_client):
    http, _ctx, _calls = supervisor_client
    response = http.get("/cs/refunds/review-queue")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["request_no"] == "RA-P5TEST0001"


def test_queue_rejects_agent_role(db, supervisor_client):
    http, ctx, _calls = supervisor_client
    # 换成 agent 身份直接构造客户端
    app = FastAPI()
    app.include_router(cs_refund_review.router)
    app.dependency_overrides[cs_refund_review.require_agent] = lambda: _context(
        agent_id=_PREFIX + "agent-a", cs_role="agent",
    )
    agent_http = TestClient(app, raise_server_exceptions=False)
    assert agent_http.get("/cs/refunds/review-queue").status_code == 403
    assert agent_http.post(
        "/cs/refunds/p5-req-1/review",
        json={"decision": "approved", "opinion": "x"},
    ).status_code == 403


def test_review_persists_and_writes_audit(supervisor_client):
    http, ctx, review_calls = supervisor_client
    response = http.post(
        "/cs/refunds/p5-req-1/review",
        json={"decision": "approved", "opinion": "凭证齐全，同意退款"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert review_calls == [{
        "request_id": "p5-req-1", "decision": "approved",
        "opinion": "凭证齐全，同意退款", "reviewer_id": ctx.agent_id,
    }]
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT action, actor_type, actor_id, resource_type, resource_id,"
            " result, after_state"
            " FROM customer_service.audit_logs WHERE user_id = %s",
            (_PREFIX + "auth-user",),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    action, actor_type, actor_id, resource_type, resource_id, result, after = rows[0]
    assert action == "refund_review"
    assert actor_type == "human" and actor_id == ctx.agent_id
    assert resource_type == "refund_request" and resource_id == "p5-req-1"
    assert result == "success"
    assert after["status"] == "approved"


def test_review_rejects_blank_opinion(supervisor_client):
    http, _ctx, _calls = supervisor_client
    for opinion in ("", "   "):
        response = http.post(
            "/cs/refunds/p5-req-1/review",
            json={"decision": "approved", "opinion": opinion},
        )
        assert response.status_code == 422


def test_review_maps_conflict_not_found_and_unknown(db, supervisor_client, monkeypatch):
    import backend.customer_service.service.refund_service as refund_mod

    http, _ctx, _calls = supervisor_client

    def conflict(self, **kw):
        raise RefundReviewConflictError()

    monkeypatch.setattr(refund_mod.RefundService, "review_refund_request", conflict)
    assert http.post(
        "/cs/refunds/p5-req-1/review",
        json={"decision": "approved", "opinion": "x"},
    ).status_code == 409

    def not_found(self, **kw):
        raise RefundRequestUnavailableError()

    monkeypatch.setattr(refund_mod.RefundService, "review_refund_request", not_found)
    assert http.post(
        "/cs/refunds/p5-req-1/review",
        json={"decision": "approved", "opinion": "x"},
    ).status_code == 404

    def unknown(self, **kw):
        raise SideEffectOutcomeUnknown("结果未知")

    monkeypatch.setattr(refund_mod.RefundService, "review_refund_request", unknown)
    response = http.post(
        "/cs/refunds/p5-req-1/review",
        json={"decision": "approved", "opinion": "x"},
    )
    assert response.status_code == 503
    assert "刷新" in response.json()["detail"]


def test_my_refund_requests_http_mode_filters_by_user(db, monkeypatch):
    """用户端 GET http 分流：只返回本人申请（跨用户不可见）。"""
    from backend.app.api.routes import cs_admin

    import backend.customer_service.service.refund_service as refund_mod

    monkeypatch.setattr(
        refund_mod.RefundService, "_review_mode", lambda self: "http",
    )
    rows = [
        {"request_id": "r1", "status": "approved", "user_id": _PREFIX + "user-a",
         "request_no": "RA-1", "amount": "10.00", "status_display": "审核通过"},
        {"request_id": "r2", "status": "pending_review", "user_id": "someone-else",
         "request_no": "RA-2", "amount": "20.00", "status_display": "等待人工审核"},
    ]
    monkeypatch.setattr(
        refund_mod.RefundService, "list_review_queue",
        lambda self, *, tenant_id, status=None, limit=50: rows,
    )

    import backend.app.api.identity as identity_mod
    from types import SimpleNamespace

    monkeypatch.setattr(
        identity_mod, "resolve_identity",
        lambda request: SimpleNamespace(
            user_id=_PREFIX + "user-a", tenant_id=TENANT, authenticated=True,
        ),
    )
    app = FastAPI()
    app.include_router(cs_admin.confirm_router)
    http = TestClient(app, raise_server_exceptions=False)
    response = http.get("/cs/refund-requests")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["request_id"] == "r1"
