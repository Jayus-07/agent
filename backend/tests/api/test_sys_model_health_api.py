from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.deps import require_admin_user
from backend.app.api.routes import sys_model_roles
from backend.infra.llm import health, models


class _Admin:
    actor = "user:test-admin"
    role = "admin"
    kind = "user"


def _client(*, as_admin: bool = True, deny_admin: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(sys_model_roles._health_router)
    if as_admin:
        app.dependency_overrides[require_admin_user] = lambda: _Admin()
    elif deny_admin:
        def deny():
            raise HTTPException(status_code=403, detail="admin required")

        app.dependency_overrides[require_admin_user] = deny
    return TestClient(app)


def test_ocr_health_check_uses_manual_live_probe(monkeypatch):
    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {"name": "ocr-model", "model_kind": "ocr"},
    )
    live_probe = AsyncMock(return_value={
        "status": "auth_failed",
        "latencyMs": 19,
        "error": "上游拒绝了凭据",
        "probeSource": "manual",
    })
    monkeypatch.setattr(health, "probe_ocr_model", live_probe)
    fallback_probe = Mock()
    monkeypatch.setattr(health, "probe_model", fallback_probe)

    response = _client().post(
        "/sys/model-health/check", json={"modelName": "ocr-model"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "auth_failed"
    assert response.json()["probeSource"] == "manual"
    live_probe.assert_awaited_once_with("ocr-model")
    fallback_probe.assert_not_called()


def test_non_ocr_health_check_keeps_existing_probe(monkeypatch):
    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {"name": "chat-model", "model_kind": "chat"},
    )
    live_probe = AsyncMock()
    monkeypatch.setattr(health, "probe_ocr_model", live_probe)
    regular_probe = Mock(return_value={
        "status": "healthy", "latencyMs": 12, "error": "",
        "probeSource": "manual",
    })
    monkeypatch.setattr(health, "probe_model", regular_probe)

    response = _client().post(
        "/sys/model-health/check", json={"modelName": "chat-model"},
    )

    assert response.status_code == 200
    regular_probe.assert_called_once_with("chat-model", probe_source="manual")
    live_probe.assert_not_awaited()


def test_manual_health_check_still_requires_admin():
    response = _client(as_admin=False, deny_admin=True).post(
        "/sys/model-health/check", json={"modelName": "ocr-model"},
    )

    assert response.status_code == 403
