from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.deps import require_admin_user
from backend.app.api.routes import sys_model_roles, sys_providers
from backend.infra.llm import health, models


class _Admin:
    role = "admin"
    kind = "user"

    def __init__(self, actor: str = "user:test-admin"):
        self.actor = actor


def _client(
    *, as_admin: bool = True, deny_admin: bool = False,
    actor: str = "user:test-admin",
) -> TestClient:
    app = FastAPI()
    app.include_router(sys_model_roles._health_router)
    if as_admin:
        app.dependency_overrides[require_admin_user] = lambda: _Admin(actor)
    elif deny_admin:
        def deny():
            raise HTTPException(status_code=403, detail="admin required")

        app.dependency_overrides[require_admin_user] = deny
    return TestClient(app)


def test_ocr_health_check_uses_manual_live_probe(monkeypatch):
    sys_providers.reset_rate_limit_for_tests()
    monkeypatch.setattr(
        sys_model_roles,
        "get_distributed_rate_limiter",
        lambda: Mock(acquire=Mock(return_value=True)),
    )
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
    sys_providers.reset_rate_limit_for_tests()
    monkeypatch.setattr(
        sys_model_roles,
        "get_distributed_rate_limiter",
        lambda: Mock(acquire=Mock(return_value=True)),
    )
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


def test_registered_model_upstream_not_found_is_a_probe_result_not_http_404(monkeypatch):
    sys_providers.reset_rate_limit_for_tests()
    monkeypatch.setattr(
        sys_model_roles,
        "get_distributed_rate_limiter",
        lambda: Mock(acquire=Mock(return_value=True)),
    )
    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {"name": "ocr-model", "model_kind": "ocr"},
    )
    live_probe = AsyncMock(return_value={
        "status": "model_not_found",
        "latencyMs": 19,
        "error": "上游未找到该模型，请检查上游模型名和模型权限",
        "probeSource": "manual",
    })
    monkeypatch.setattr(health, "probe_ocr_model", live_probe)

    response = _client().post(
        "/sys/model-health/check", json={"modelName": "ocr-model"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "model_not_found"
    assert "上游未找到" in response.json()["error"]
    live_probe.assert_awaited_once_with("ocr-model")


def test_missing_local_model_returns_404_without_running_probe(monkeypatch):
    sys_providers.reset_rate_limit_for_tests()
    monkeypatch.setattr(
        sys_model_roles,
        "get_distributed_rate_limiter",
        lambda: Mock(acquire=Mock(return_value=True)),
    )
    monkeypatch.setattr(models, "get_model_entry", lambda _name: None)
    live_probe = AsyncMock()
    monkeypatch.setattr(health, "probe_ocr_model", live_probe)

    response = _client().post(
        "/sys/model-health/check", json={"modelName": "missing-model"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "模型未在注册表登记"
    live_probe.assert_not_awaited()


def test_manual_health_probe_is_rate_limited_per_trusted_actor(monkeypatch):
    sys_providers.reset_rate_limit_for_tests()
    distributed = Mock(acquire=Mock(return_value=True))
    monkeypatch.setattr(
        sys_model_roles, "get_distributed_rate_limiter", lambda: distributed,
    )
    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {"name": "ocr-model", "model_kind": "ocr"},
    )
    live_probe = AsyncMock(return_value={
        "status": "healthy", "latencyMs": 19, "error": "", "probeSource": "manual",
    })
    monkeypatch.setattr(health, "probe_ocr_model", live_probe)

    client = _client(actor="user:rate-limited-admin")
    responses = [
        client.post("/sys/model-health/check", json={"modelName": "ocr-model"})
        for _ in range(11)
    ]
    other_actor_response = _client(actor="user:other-admin").post(
        "/sys/model-health/check", json={"modelName": "ocr-model"},
    )

    assert [response.status_code for response in responses] == [200] * 10 + [429]
    assert "每分钟上限 10 次" in responses[-1].json()["detail"]
    assert other_actor_response.status_code == 200
    assert live_probe.await_count == 11
    assert distributed.acquire.call_args_list[0].kwargs["identity"] == (
        "model-health-check:user:rate-limited-admin"
    )
    assert distributed.acquire.call_args_list[-1].kwargs["identity"] == (
        "model-health-check:user:other-admin"
    )
    sys_providers.reset_rate_limit_for_tests()


def test_distributed_health_probe_limit_returns_clear_429(monkeypatch):
    sys_providers.reset_rate_limit_for_tests()
    distributed = Mock(acquire=Mock(return_value=False))
    monkeypatch.setattr(
        sys_model_roles, "get_distributed_rate_limiter", lambda: distributed,
    )
    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {"name": "ocr-model", "model_kind": "ocr"},
    )
    live_probe = AsyncMock()
    monkeypatch.setattr(health, "probe_ocr_model", live_probe)

    response = _client(actor="user:shared-limit-admin").post(
        "/sys/model-health/check", json={"modelName": "ocr-model"},
    )

    assert response.status_code == 429
    assert "每分钟上限 10 次" in response.json()["detail"]
    assert response.headers["retry-after"] == "6"
    live_probe.assert_not_awaited()
    sys_providers.reset_rate_limit_for_tests()


def test_manual_health_check_still_requires_admin():
    response = _client(as_admin=False, deny_admin=True).post(
        "/sys/model-health/check", json={"modelName": "ocr-model"},
    )

    assert response.status_code == 403
