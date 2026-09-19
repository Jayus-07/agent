"""api/routes/sys_providers.py —— 探测端点契约（P1b）

锁定 B.6 的四道限制与裸 dict 响应形态（§1.1.1 的教训：前端曾因
「Result 壳 / 裸 dict」不一致而把成功当失败）。

鉴权用 `dependency_overrides` 注入假身份，**不 mock `atLeast` / 真实令牌**
（照 §16 的口径：权限矩阵要么真实切换角色，要么走依赖覆盖，不 mock 判定逻辑）。
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
from backend.services.provider_probe import ProbeResult, ProbeStep


class _FakeIdent:
    """路由只用到 `actor`；role/kind 由 `require_admin_user` 保证，故此处不重复断言。"""

    actor = "user:test-admin"
    role = "admin"
    kind = "user"


def _ok_result() -> ProbeResult:
    return ProbeResult(
        ok=True, blocked_at=None, summary="厂商连通性通过",
        steps=[ProbeStep("L0", provider_probe.STATUS_PASS, "URL 可达")],
    )


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(sys_providers.router)
    a.dependency_overrides[require_admin_user] = lambda: _FakeIdent()
    sys_providers.reset_rate_limit_for_tests()
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def anon_client() -> TestClient:
    """不覆盖鉴权依赖 → 走真实 `require_admin_user`（无 JWT 应被拒）。"""
    a = FastAPI()
    a.include_router(sys_providers.router)
    return TestClient(a)


DRAFT = {
    "driver": "openai",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-draft",
    "model_name": "glm-4.6",
    "network_scope": "public",
}


# ── 限制 1：admin only ──────────────────────────────────────────────────


@pytest.mark.parametrize("path,payload", [
    ("/sys/providers/verify-draft", DRAFT),
    ("/sys/providers/custom/verify", None),
])
def test_endpoints_reject_unauthenticated(anon_client, path, payload):
    resp = anon_client.post(path) if payload is None else anon_client.post(path, json=payload)
    assert resp.status_code in (401, 403)


# ── 草稿态：形态、scope 归一化、限流 ────────────────────────────────────


def test_draft_success_returns_bare_dict_carrying_probe_result(client, monkeypatch):
    monkeypatch.setattr(sys_providers.provider_probe, "probe_provider",
                        AsyncMock(return_value=_ok_result()))
    resp = client.post("/sys/providers/verify-draft", json=DRAFT)

    assert resp.status_code == 200
    body = resp.json()
    # 裸 dict：没有 Result 壳（无 code/data/message 包装）
    assert set(body) >= {
        "provider", "target", "network_scope", "draft",
        "ok", "blocked_at", "summary", "steps",
    }
    assert body["draft"] is True
    assert body["provider"] is None
    assert body["ok"] is True
    assert body["steps"][0]["level"] == "L0"


@pytest.mark.parametrize("given,expected", [
    ("PRIVATE", "private"), ("private", "private"),
    ("public", "public"), ("nonsense", "public"), ("", "public"),
])
def test_draft_scope_is_normalized(client, monkeypatch, given, expected):
    captured: dict = {}

    async def _capture(**kw):
        captured.update(kw)
        return _ok_result()

    monkeypatch.setattr(sys_providers.provider_probe, "probe_provider", _capture)
    resp = client.post("/sys/providers/verify-draft",
                       json={**DRAFT, "network_scope": given})

    assert resp.status_code == 200
    assert captured["network_scope"] == expected


def test_draft_rate_limit_is_stricter_than_verify(client, monkeypatch):
    monkeypatch.setattr(sys_providers.provider_probe, "probe_provider",
                        AsyncMock(return_value=_ok_result()))

    for _ in range(sys_providers._DRAFT_VERIFY_PER_MIN):
        assert client.post("/sys/providers/verify-draft", json=DRAFT).status_code == 200

    blocked = client.post("/sys/providers/verify-draft", json=DRAFT)
    assert blocked.status_code == 429
    assert "频繁" in blocked.json()["detail"]


def test_draft_does_not_echo_api_key_back(client, monkeypatch):
    """探测结果与回执都不得携带密钥（B.6 审计口径：只记指纹，不记 key）。"""
    monkeypatch.setattr(sys_providers.provider_probe, "probe_provider",
                        AsyncMock(return_value=_ok_result()))
    resp = client.post("/sys/providers/verify-draft",
                       json={**DRAFT, "api_key": "sk-super-secret"})
    assert "sk-super-secret" not in resp.text


# ── 已存实例：503 / 404 / 成功路径 ──────────────────────────────────────


def test_saved_provider_503_when_registry_unavailable(client, monkeypatch):
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=RegistrySnapshot(loaded=False)))
    resp = client.post("/sys/providers/custom/verify")
    assert resp.status_code == 503


def test_saved_provider_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=RegistrySnapshot(loaded=True)))
    resp = client.post("/sys/providers/custom/verify")
    assert resp.status_code == 404


def test_saved_provider_uses_db_scope_and_resolved_model(client, monkeypatch):
    """已存实例：scope 取自 DB，模型名取该实例名下第一个模型，key 从统一入口解析。"""
    snap = RegistrySnapshot(
        providers=[{
            "id": "custom",
            "base_url": "https://api.example.com/v1",
            "network_scope": "private",
            "driver": "openai",
        }],
        models=[{"name": "glm-4.6", "provider": "custom"}],
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

    async def _capture(**kw):
        captured.update(kw)
        return _ok_result()

    monkeypatch.setattr(sys_providers.provider_probe, "probe_provider", _capture)

    resp = client.post("/sys/providers/custom/verify")
    assert resp.status_code == 200
    assert captured["network_scope"] == "private"
    assert captured["api_key"] == "sk-from-env"
    assert captured["model_name"] == "glm-4.6"
    assert resp.json()["provider"] == "custom"


def test_saved_provider_422_when_base_url_missing(client, monkeypatch):
    snap = RegistrySnapshot(
        providers=[{"id": "custom", "base_url": "", "network_scope": "public"}],
        models=[], credentials={}, loaded=True,
    )
    monkeypatch.setattr(sys_providers.registry_store, "load_registry",
                        AsyncMock(return_value=snap))
    resp = client.post("/sys/providers/custom/verify")
    assert resp.status_code == 422
