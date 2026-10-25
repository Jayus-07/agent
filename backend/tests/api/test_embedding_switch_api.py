"""全局嵌入配置入口的权限、参数透传和失败契约。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api import identity
from backend.app.api.deps import require_admin_user, require_super_admin_user
from backend.app.api.routes import model_config
from backend.rag.indexing import embedding_migration


@pytest.fixture
def api(monkeypatch):
    app = FastAPI()
    app.include_router(model_config.router)
    actor = SimpleNamespace(actor="user:trusted", role="super_admin", kind="user")
    app.dependency_overrides[require_admin_user] = lambda: actor
    app.dependency_overrides[require_super_admin_user] = lambda: actor
    monkeypatch.setattr(model_config, "require_super_admin_user", AsyncMock(return_value=actor))
    monkeypatch.setattr(identity, "require_principal", lambda request: SimpleNamespace(
        user_id="trusted", tenant_id="trusted-tenant",
    ))
    service = SimpleNamespace(
        preview_embedding_change=AsyncMock(return_value={"dimension": 1024}),
        update_role=AsyncMock(return_value={"newValue": "new"}),
    )
    app.state.model_config_service = service
    yield TestClient(app), service, app


def test_preview_invalid_dimension_is_actionable_422(api):
    client, service, _ = api
    service.preview_embedding_change.side_effect = ValueError("模型输出1536维，索引要求1024维")
    result = client.post("/sys/model-roles/embedding/preview", json={"modelName": "new"})
    assert result.status_code == 422
    assert "1024" in result.text
    service.update_role.assert_not_awaited()


def test_confirmation_and_expected_snapshot_are_passed_to_authoritative_service(api):
    client, service, _ = api
    result = client.put("/sys/model-roles/embedding", headers={"Idempotency-Key": "switch-1"}, json={
        "modelName": "new", "confirmRebuild": True,
        "expectedModel": "old", "expectedFingerprint": "fp",
    })
    assert result.status_code == 200
    service.update_role.assert_awaited_once_with(
        "embedding", "new", "user:trusted", confirm_rebuild=True,
        expected_model="old", expected_fingerprint="fp",
    )


def test_rebuild_uses_trusted_user_and_tenant_not_body_claims(api, monkeypatch):
    client, _, _ = api
    submit = AsyncMock(return_value={"taskId": "job", "status": "PENDING"})
    monkeypatch.setattr(embedding_migration, "submit_rebuild", submit)
    result = client.post("/sys/model-roles/embedding/rebuild", headers={"Idempotency-Key": "rebuild-1"}, json={
        "modelName": "new", "expectedFingerprint": "fp", "confirmRebuild": True,
        "userId": "attacker", "tenantId": "other",
    })
    assert result.status_code == 200
    submit.assert_awaited_once_with("new", "fp", True, "trusted", "trusted-tenant", "rebuild-1")


@pytest.mark.parametrize("path", ["preview", "rebuild"])
def test_non_super_admin_cannot_preview_or_start_global_rebuild(api, path):
    client, service, app = api

    def denied():
        raise HTTPException(403, "需要超级管理员")

    app.dependency_overrides[require_super_admin_user] = denied
    result = client.post(f"/sys/model-roles/embedding/{path}",
                         headers={"Idempotency-Key": "forbidden"},
                         json={"modelName": "new", "confirmRebuild": True})
    assert result.status_code == 403
    service.preview_embedding_change.assert_not_awaited()
    service.update_role.assert_not_awaited()


def test_non_super_admin_cannot_switch_via_generic_role_route(api, monkeypatch):
    client, service, _ = api
    monkeypatch.setattr(model_config, "require_super_admin_user", AsyncMock(side_effect=HTTPException(403)))
    result = client.put("/sys/model-roles/embedding", headers={"Idempotency-Key": "forbidden"},
                        json={"modelName": "new", "confirmRebuild": True})
    assert result.status_code == 403
    service.update_role.assert_not_awaited()


@pytest.mark.parametrize("role,kind", [("admin", "user"), ("super_admin", "service")])
def test_local_billing_declaration_cannot_bypass_price_permission(role, kind):
    actor = SimpleNamespace(role=role, kind=kind)
    with pytest.raises(HTTPException) as error:
        model_config._require_local_billing_permission(actor, "local")
    assert error.value.status_code == 403
    model_config._require_local_billing_permission(actor, "metered")


def test_super_admin_can_declare_local_billing():
    model_config._require_local_billing_permission(
        SimpleNamespace(role="super_admin", kind="user"), "local",
    )
