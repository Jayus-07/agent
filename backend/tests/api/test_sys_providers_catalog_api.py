"""api/routes/sys_providers.py —— 模型目录端点契约（§B.14）

模型目录与「探测」是两件事，这组用例把这条边界锁住：

- `verify*` 回答「**能不能用**」，由 L0 + L2 判定。
- `model-catalog` 回答「**有哪些模型名可以填**」，**不参与任何判定**。

所以这里第一条断言就是**响应里不许出现 `blocked_at`** —— 一旦目录开始携带判定语义，
前端迟早会把「拿不到清单」渲染成「供应商不可用」，而这正是把 L1 移出探测链要避免的事。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_admin_user
from backend.app.api.routes import sys_providers
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm.credentials import ProviderCredentials
from backend.infra.llm.registry_store import RegistrySnapshot
from backend.services import provider_probe
from backend.services.provider_probe import ModelCatalog, ModelCatalogItem

DRAFT = {
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-draft-secret",
    "network_scope": "public",
}

_CATALOG_KEYS = {
    "ok", "status", "summary", "reason", "items", "count", "total",
    "truncated", "shape_ok", "cached",
}


class _FakeIdent:
    actor = "user:test-admin"
    role = "admin"
    kind = "user"


def _ok_catalog() -> ModelCatalog:
    return ModelCatalog(
        ok=True, status=provider_probe.STATUS_PASS, summary="共 2 个模型可选",
        items=[ModelCatalogItem(id="glm-4.6"), ModelCatalogItem(id="bge-m3", kind="embedding")],
        shape_ok=True,
    )


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(sys_providers.router)
    a.dependency_overrides[require_admin_user] = lambda: _FakeIdent()
    sys_providers.reset_rate_limit_for_tests()
    provider_probe.reset_catalog_cache_for_tests()
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def anon_client() -> TestClient:
    a = FastAPI()
    a.include_router(sys_providers.router)
    return TestClient(a)


# ── 限制 1：admin only ──────────────────────────────────────────────────


@pytest.mark.parametrize("path,payload", [
    ("/sys/providers/model-catalog", DRAFT),
    ("/sys/providers/custom/model-catalog", None),
])
def test_catalog_rejects_unauthenticated(anon_client, path, payload):
    resp = (anon_client.post(path) if payload is None
            else anon_client.post(path, json=payload))
    assert resp.status_code in (401, 403)


# ── 草稿态 ──────────────────────────────────────────────────────────────


def test_draft_catalog_returns_bare_dict_without_probe_verdict(client, monkeypatch):
    monkeypatch.setattr(sys_providers.provider_probe, "fetch_model_catalog",
                        AsyncMock(return_value=_ok_catalog()))
    resp = client.post("/sys/providers/model-catalog", json=DRAFT)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= _CATALOG_KEYS | {"provider", "target", "network_scope", "draft"}
    assert body["draft"] is True
    assert body["provider"] is None
    assert [i["id"] for i in body["items"]] == ["glm-4.6", "bge-m3"]
    assert body["items"][1]["kind"] == "embedding"
    # 目录**不是探测**：不得携带判定语义，否则前端会把「拿不到清单」当成「不可用」
    assert "blocked_at" not in body
    assert "steps" not in body


def test_draft_catalog_accepts_frontend_camel_case_payload(client, monkeypatch):
    captured: dict = {}

    async def _capture(base_url, api_key, **kw):
        captured.update(kw, base_url=base_url, api_key=api_key)
        return _ok_catalog()

    monkeypatch.setattr(sys_providers.provider_probe, "fetch_model_catalog", _capture)
    resp = client.post("/sys/providers/model-catalog", json={
        "baseUrl": "https://api.example.com/v1",
        "apiKey": "sk-draft",
        "networkScope": "private",
    })

    assert resp.status_code == 200
    assert captured["base_url"] == "https://api.example.com/v1"
    assert captured["api_key"] == "sk-draft"
    assert captured["allow_private"] is True


def test_draft_catalog_does_not_echo_api_key(client, monkeypatch):
    monkeypatch.setattr(sys_providers.provider_probe, "fetch_model_catalog",
                        AsyncMock(return_value=_ok_catalog()))
    resp = client.post("/sys/providers/model-catalog",
                       json={**DRAFT, "api_key": "sk-super-secret"})
    assert "sk-super-secret" not in resp.text


def test_draft_catalog_rate_limit(client, monkeypatch):
    monkeypatch.setattr(sys_providers.provider_probe, "fetch_model_catalog",
                        AsyncMock(return_value=_ok_catalog()))

    for _ in range(sys_providers._CATALOG_PER_MIN):
        assert client.post("/sys/providers/model-catalog",
                           json=DRAFT).status_code == 200

    blocked = client.post("/sys/providers/model-catalog", json=DRAFT)
    assert blocked.status_code == 429
    assert "频繁" in blocked.json()["detail"]


# ── 已在库实例 ──────────────────────────────────────────────────────────


def test_saved_catalog_503_when_registry_unavailable(client, monkeypatch):
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=RegistrySnapshot(loaded=False)))
    resp = client.post("/sys/providers/custom/model-catalog")
    assert resp.status_code == 503


def test_saved_catalog_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=RegistrySnapshot(loaded=True)))
    resp = client.post("/sys/providers/custom/model-catalog")
    assert resp.status_code == 404


def test_saved_catalog_uses_db_address_and_resolved_key(client, monkeypatch):
    """库里的地址 + 统一入口解析出的 Key —— 密钥不回显、不进响应。"""
    snap = RegistrySnapshot(
        providers=[{
            "id": "custom",
            "base_url": "https://api.example.com/v1",
            "network_scope": "public",
            "driver": "openai",
        }],
        models=[],
        credentials={},
        loaded=True,
    )
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=snap))
    monkeypatch.setattr(
        credentials_mod, "resolve_credentials",
        lambda provider, **kw: ProviderCredentials(
            provider=provider, api_key="sk-from-env", source="env", version=0
        ),
    )
    captured: dict = {}

    async def _capture(base_url, api_key, **kw):
        captured.update(kw, base_url=base_url, api_key=api_key)
        return _ok_catalog()

    monkeypatch.setattr(sys_providers.provider_probe, "fetch_model_catalog", _capture)

    resp = client.post("/sys/providers/custom/model-catalog")

    assert resp.status_code == 200
    assert captured["base_url"] == "https://api.example.com/v1"
    assert captured["api_key"] == "sk-from-env"
    assert captured["allow_private"] is False
    assert "sk-from-env" not in resp.text
    assert resp.json()["draft"] is False


def test_saved_catalog_422_when_provider_has_no_base_url(client, monkeypatch):
    snap = RegistrySnapshot(
        providers=[{"id": "custom", "base_url": "", "network_scope": "public"}],
        models=[], credentials={}, loaded=True,
    )
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=snap))
    resp = client.post("/sys/providers/custom/model-catalog")
    assert resp.status_code == 422
