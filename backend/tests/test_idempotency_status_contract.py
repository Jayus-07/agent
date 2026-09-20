"""幂等状态查询接口契约：只允许原操作者读取安全摘要。"""

import asyncio

import pytest
from fastapi import HTTPException

from backend.app.api.identity import Identity


def test_idempotency_status_scopes_identity_and_hides_result_body(monkeypatch):
    from backend.app.api.routes import idempotency

    observed = {}

    class Store:
        def get_status(self, **kwargs):
            observed.update(kwargs)
            return {
                "client_key": "req-1",
                "operation": "model_price.import",
                "status": "succeeded",
                "attempt": 1,
                "error_code": None,
                "has_result": True,
                "updated_at": "2026-09-18T12:00:00+00:00",
                "result": {"secret": "must-not-leak"},
            }

    monkeypatch.setattr(idempotency, "_store", lambda: Store())
    monkeypatch.setattr(
        idempotency,
        "require_identity",
        lambda _request: Identity(
            user_id="user-1",
            tenant_id="tenant-1",
            auth_type="jwt",
            source="header",
        ),
    )

    result = asyncio.run(idempotency.get_operation_status("req-1", object()))

    assert observed == {
        "tenant_id": "tenant-1",
        "actor_id": "user-1",
        "client_key": "req-1",
    }
    assert result["status"] == "succeeded"
    assert result["has_result"] is True
    assert "result" not in result
    assert "secret" not in str(result)


def test_idempotency_status_returns_not_found_without_record(monkeypatch):
    from backend.app.api.routes import idempotency

    class Store:
        def get_status(self, **_kwargs):
            return None

    monkeypatch.setattr(idempotency, "_store", lambda: Store())
    monkeypatch.setattr(
        idempotency,
        "require_identity",
        lambda _request: Identity(
            user_id="user-1",
            tenant_id="tenant-1",
            auth_type="jwt",
            source="header",
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(idempotency.get_operation_status("missing", object()))

    assert exc_info.value.status_code == 404
