"""智能客服用户资料与订单接口回归测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.identity import Identity
from backend.app.api.routes import cs_customer
from backend.customer_service.errors import AccountNotFoundError, DatabaseError


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(cs_customer.router)
    app.dependency_overrides[cs_customer.require_identity] = lambda: Identity(
        user_id="user-1",
        user_name="新注册用户",
        auth_type="jwt",
        source="header",
    )
    return app


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _missing_account(*_args, **_kwargs):
    raise AccountNotFoundError("customer profile not found")


def test_new_user_without_profile_or_orders_gets_empty_order_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无业务档案和订单是正常空状态，不应返回读取失败。"""
    monkeypatch.setattr(
        cs_customer, "get_account_service",
        lambda: type("AccountServiceStub", (), {"query_account": staticmethod(_missing_account)})(),
    )
    monkeypatch.setattr(
        cs_customer, "get_order_service",
        lambda: type(
            "OrderServiceStub",
            (),
            {
                "list_recent_orders_with_products": staticmethod(lambda *_args, **_kwargs: []),
                "query_orders": staticmethod(
                    lambda **_kwargs: type("Result", (), {"orders": []})()
                ),
            },
        )(),
    )

    response = client.get("/cs/customer-context")

    assert response.status_code == 200
    assert response.json()["customer"]["display_name"] == "新注册用户"
    assert response.json()["orders"] == []
    assert response.json()["profile_unavailable"] is False


def test_order_database_failure_is_reported_as_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真正的业务数据库错误仍明确返回 503。"""
    monkeypatch.setattr(
        cs_customer, "get_account_service",
        lambda: type("AccountServiceStub", (), {"query_account": staticmethod(_missing_account)})(),
    )
    monkeypatch.setattr(
        cs_customer, "get_order_service",
        lambda: type(
            "OrderServiceStub",
            (),
            {
                "list_recent_orders_with_products": staticmethod(
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        DatabaseError("database unavailable")
                    )
                ),
            },
        )(),
    )

    response = client.get("/cs/customer-context")

    assert response.status_code == 503
    assert response.json()["detail"] == "客户资料和订单暂时无法读取"


def test_customer_context_requires_authenticated_identity() -> None:
    """未认证请求不能读取任何人的客户信息。"""
    app = FastAPI()
    app.include_router(cs_customer.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/cs/customer-context")

    assert response.status_code == 401
