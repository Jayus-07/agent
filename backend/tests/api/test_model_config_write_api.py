"""模型配置治理写端点的契约测试。

这些测试先锁住管理端上线所需的边界：写入必须是管理员、必须带幂等键、
响应不得携带密钥、历史与漂移使用裸 dict 契约。
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.api.deps import require_admin_user, require_user_actor
from backend.app.api.router import api_router
from backend.app.api.routes import model_config
from backend.app.exceptions import http_exception_handler
from backend.services.model_config import ModelConfigConflict


@dataclass
class _FakeIdentity:
    actor: str = "user:test-admin"
    role: str = "admin"
    kind: str = "user"


class _FakeService:
    def __init__(self) -> None:
        self.update_role = AsyncMock(
            return_value={
                "role": "main",
                "oldValue": "MiniMax-M3",
                "newValue": "qwen3.7-plus",
                "changedBy": "user:test-admin",
            }
        )
        self.update_provider = AsyncMock(
            return_value={
                "id": "qwen",
                "displayName": "通义千问",
                "driver": "openai",
                "baseUrl": "https://example.invalid/v1",
                "networkScope": "public",
                "billing": "metered",
                "enabled": True,
                "credential": {
                    "configured": True,
                    "fingerprint": "abc123",
                    "last4": "a1b2",
                },
            }
        )
        self.create_provider = AsyncMock(
            return_value={
                "id": "custom-example-com",
                "displayName": "example.com",
                "driver": "openai",
                "baseUrl": "https://example.com/v1",
                "modelName": "custom-model",
                "networkScope": "public",
                "billing": "metered",
                "enabled": True,
                "credential": {
                    "configured": True,
                    "fingerprint": "abc123",
                    "last4": "a1b2",
                },
            }
        )
        self.add_provider_model = AsyncMock(
            return_value={
                "providerId": "custom-example-com",
                "name": "custom-embedding",
                "display": "custom-embedding",
                "modelKind": "embedding",
                "probe": {"ok": True},
            }
        )
        self.list_history = AsyncMock(
            return_value={
                "items": [
                    {
                        "id": "1",
                        "object": "provider_credential",
                        "key": "qwen",
                        "oldValue": None,
                        "newValue": None,
                        "secretFingerprint": "abc123",
                        "operator": "user:test-admin",
                        "at": "2026-09-19T10:00:00+08:00",
                        "rollbackable": False,
                    }
                ]
            }
        )
        self.rollback = AsyncMock(
            return_value={"id": "2", "rolledBack": True, "changedBy": "user:test-admin"}
        )
        self.drift = AsyncMock(
            return_value={"items": [], "checkedAt": "2026-09-19T10:00:00+08:00"}
        )
        self.list_specialized = AsyncMock(
            return_value={
                "items": [
                    {
                        "role": "embedding",
                        "modelName": "qwen3.7-text-embedding",
                        "providerId": "dashscope-rag",
                        "providerName": "阿里云百炼专项",
                        "adapter": "dashscope_embedding",
                        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "credential": {"configured": True, "last4": "1234"},
                        "lastProbe": {"ok": True, "elapsedMs": 87},
                    }
                ],
                "adapters": ["dashscope_embedding", "openai_embedding", "dashscope_rerank", "jina_rerank"],
            }
        )
        self.configure_specialized = AsyncMock(
            return_value={
                "ok": True,
                "saved": True,
                "tests": [
                    {
                        "role": "embedding",
                        "ok": True,
                        "elapsedMs": 87,
                        "detail": "已收到有效响应",
                    }
                ],
            }
        )


        self.remove_provider_model = AsyncMock(
            return_value={
                "providerId": "custom-example-com",
                "name": "custom-embedding",
                "display": "custom-embedding",
                "modelKind": "embedding",
            }
        )


def _client(service: _FakeService) -> TestClient:
    app = FastAPI()
    app.include_router(model_config.router)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.dependency_overrides[require_admin_user] = lambda: _FakeIdentity()
    app.dependency_overrides[require_user_actor] = lambda: _FakeIdentity()
    app.state.model_config_service = service
    return TestClient(app)


def test_role_write_requires_idempotency_key() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.put(
        "/sys/model-roles/main",
        json={"modelName": "qwen3.7-plus"},
    )

    assert response.status_code == 400
    assert service.update_role.await_count == 0


def test_role_write_uses_admin_and_returns_bare_dict() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.put(
        "/sys/model-roles/main",
        headers={"Idempotency-Key": "role-main-1"},
        json={"modelName": "qwen3.7-plus"},
    )

    assert response.status_code == 200
    assert response.json()["newValue"] == "qwen3.7-plus"
    assert "data" not in response.json()
    service.update_role.assert_awaited_once()


def test_provider_write_never_returns_secret() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.put(
        "/sys/providers/qwen",
        headers={"Idempotency-Key": "provider-qwen-1"},
        json={
            "displayName": "通义千问",
            "driver": "openai",
            "baseUrl": "https://example.invalid/v1",
            "networkScope": "public",
            "billing": "metered",
            "enabled": True,
            "apiKey": "sk-never-returned",
        },
    )

    assert response.status_code == 200
    assert "sk-never-returned" not in response.text
    assert "key_cipher" not in response.text


def test_provider_conflict_returns_actionable_model_reason() -> None:
    service = _FakeService()
    service.update_provider.side_effect = ModelConfigConflict(
        "模型 qwen3.7-text-embedding 已属于历史专项供应商 specialized-api；"
        "请编辑历史专项配置，或确认迁移到当前供应商"
    )
    client = _client(service)

    response = client.put(
        "/sys/providers/qwen",
        headers={"Idempotency-Key": "provider-conflict-1"},
        json={
            "displayName": "通义千问",
            "driver": "openai",
            "baseUrl": "https://example.invalid/v1",
            "modelName": "qwen3.7-text-embedding",
            "modelKind": "embedding",
        },
    )

    assert response.status_code == 409
    assert "历史专项供应商" in response.json()["detail"]


def test_provider_create_accepts_minimal_api_key_and_model() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.post(
        "/sys/providers",
        headers={"Idempotency-Key": "provider-create-1"},
        json={
            "baseUrl": "https://example.com/v1",
            "apiKey": "sk-never-returned",
            "modelName": "custom-model",
        },
    )

    assert response.status_code == 200
    assert response.json()["modelName"] == "custom-model"
    assert "sk-never-returned" not in response.text
    service.create_provider.assert_awaited_once()


def test_provider_create_accepts_model_kind_and_forwards_it() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.post(
        "/sys/providers",
        headers={"Idempotency-Key": "provider-create-embedding-1"},
        json={
            "baseUrl": "https://example.com/v1",
            "apiKey": "sk-never-returned",
            "modelName": "custom-embedding",
            "modelKind": "embedding",
        },
    )

    assert response.status_code == 200
    payload = service.create_provider.await_args.args[0]
    assert payload["modelKind"] == "embedding"


def test_provider_model_create_accepts_kind_and_forwards_it() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.post(
        "/sys/providers/custom-example-com/models",
        headers={"Idempotency-Key": "provider-model-create-1"},
        json={"modelName": "custom-embedding", "modelKind": "embedding"},
    )

    assert response.status_code == 200
    payload = service.add_provider_model.await_args.args[1]
    assert payload["modelKind"] == "embedding"


def test_provider_model_create_accepts_vision_and_speech_kinds() -> None:
    service = _FakeService()
    client = _client(service)

    for kind in ("vision", "speech"):
        response = client.post(
            "/sys/providers/custom-example-com/models",
            headers={"Idempotency-Key": f"provider-model-create-{kind}"},
            json={"modelName": f"custom-{kind}", "modelKind": kind},
        )

        assert response.status_code == 200
        assert service.add_provider_model.await_args.args[1]["modelKind"] == kind


def test_history_and_drift_are_bare_dicts() -> None:
    service = _FakeService()
    client = _client(service)

    history = client.get("/sys/config/history")
    drift = client.get("/sys/config/drift")

    assert history.status_code == 200
    assert history.json()["items"][0]["rollbackable"] is False
    assert drift.status_code == 200
    assert "result" not in drift.json()


def test_specialized_config_requires_idempotency_key() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.post(
        "/sys/specialized-models/test-and-save",
        json={
            "provider": {
                "displayName": "专项供应商",
                "baseUrl": "https://example.com",
                "apiKey": "sk-test-placeholder",
            },
            "bindings": {
                "embedding": {
                    "modelName": "qwen3.7-text-embedding",
                    "adapter": "dashscope_embedding",
                    "baseUrl": "https://example.com/v1",
                }
            },
        },
    )

    assert response.status_code == 400
    service.configure_specialized.assert_not_awaited()


def test_specialized_config_returns_probe_elapsed_and_never_secret() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.post(
        "/sys/specialized-models/test-and-save",
        headers={"Idempotency-Key": "specialized-1"},
        json={
            "provider": {
                "displayName": "专项供应商",
                "baseUrl": "https://example.com",
                "apiKey": "sk-never-returned",
            },
            "bindings": {
                "embedding": {
                    "modelName": "qwen3.7-text-embedding",
                    "adapter": "dashscope_embedding",
                    "baseUrl": "https://example.com/v1",
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["tests"][0]["elapsedMs"] == 87
    assert "sk-never-returned" not in response.text
    service.configure_specialized.assert_awaited_once()


def test_specialized_config_get_never_returns_secret() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.get("/sys/specialized-models")

    assert response.status_code == 200
    assert response.json()["items"][0]["credential"]["last4"] == "1234"
    assert "key_cipher" not in response.text


def test_credential_history_cannot_be_rolled_back() -> None:
    service = _FakeService()
    service.rollback.side_effect = ModelConfigConflict("密钥历史不可回滚")
    client = _client(service)

    response = client.post(
        "/sys/config/history/1/rollback",
        headers={"Idempotency-Key": "rollback-credential-1"},
    )

    assert response.status_code == 409
    service.rollback.assert_awaited_once()


def test_remove_model_requires_idempotency_key() -> None:
    service = _FakeService()
    client = _client(service)

    response = client.delete(
        "/sys/providers/custom-example-com/models",
        params={"modelName": "custom-embedding"},
    )

    assert response.status_code == 400
    assert service.remove_provider_model.await_count == 0


def test_remove_model_passes_model_name_from_query() -> None:
    service = _FakeService()
    client = _client(service)

    # 模型名自带斜杠（Qwen/Qwen3-32B）：必须走 query，放进路径段会被拆成多段而匹配不到
    response = client.delete(
        "/sys/providers/siliconflow/models",
        params={"modelName": "Qwen/Qwen3-32B"},
        headers={"Idempotency-Key": "remove-model-1"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "custom-embedding"
    service.remove_provider_model.assert_awaited_once_with(
        "siliconflow", "Qwen/Qwen3-32B", "user:test-admin"
    )


def test_remove_model_conflict_maps_to_409() -> None:
    service = _FakeService()
    service.remove_provider_model.side_effect = ModelConfigConflict(
        "模型 qwen3.7-plus 正被角色 main 使用，不能移除"
    )
    client = _client(service)

    response = client.delete(
        "/sys/providers/qwen/models",
        params={"modelName": "qwen3.7-plus"},
        headers={"Idempotency-Key": "remove-model-2"},
    )

    assert response.status_code == 409
    assert "角色" in response.text


def test_model_config_routes_are_registered_on_main_router() -> None:
    paths = {route.path for route in api_router.routes}
    assert {
        "/sys/providers",
        "/sys/providers/verify-draft",
        "/sys/providers/{provider_id}/verify",
        "/sys/model-roles",
        "/sys/model-roles/{role}",
        "/sys/providers/{provider_id}",
        "/sys/providers",
        "/sys/providers/{provider_id}/models",
        "/sys/config/history",
        "/sys/config/history/{history_id}/rollback",
        "/sys/config/drift",
        "/sys/specialized-models",
        "/sys/specialized-models/test-and-save",
    } <= paths
