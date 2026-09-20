import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api import deps
from backend.app.api.identity import Identity
from backend.app.api.routes import budgets, model_prices
from backend.app.api.routes.budgets import BudgetPolicyUpdate
from backend.app.api.routes.model_prices import (
    PriceCanaryRequest,
    PriceImportRequest,
    PriceReviewRequest,
)


def _request(headers: dict[str, str]) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "headers": raw_headers})


def test_governance_write_requires_idempotency_key() -> None:
    require_key = getattr(deps, "require_idempotency_key", None)
    assert callable(require_key)
    with pytest.raises(HTTPException) as exc_info:
        require_key(_request({}))
    assert exc_info.value.status_code == 400


def test_governance_write_preserves_client_idempotency_key() -> None:
    require_key = getattr(deps, "require_idempotency_key", None)
    assert callable(require_key)
    assert require_key(_request({"Idempotency-Key": "  client-key-1  "})) == "client-key-1"


@pytest.mark.asyncio
async def test_governance_routes_reject_missing_key_before_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(user_id="u1", tenant_id="t1", source="header"),
    )
    operator = deps.OperatorIdentity(role="admin", actor="user:u1")
    request = _request({})

    calls = [
        budgets.update_budget_policy(
            "tenant",
            "t1",
            BudgetPolicyUpdate(
                daily_limit_usd="1.000000",
                monthly_limit_usd="10.000000",
                enforcement="hard",
                reason="test",
            ),
            request,
            operator,
        ),
        model_prices.import_model_price_version(
            PriceImportRequest(
                version="v1",
                source="test",
                rows=[{"model_name": "m", "component": "llm", "dimension": "input", "price_per_unit": "1"}],
            ),
            request,
            operator,
        ),
        model_prices.review_model_price_version(
            "v1",
            PriceReviewRequest(decision="approve"),
            request,
            operator,
        ),
        model_prices.canary_model_price_version(
            "v1",
            PriceCanaryRequest(action="start"),
            request,
            operator,
        ),
    ]

    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 400
