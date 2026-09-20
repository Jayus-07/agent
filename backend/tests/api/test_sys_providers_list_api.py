"""api/routes/sys_providers.py —— GET /sys/providers 清单契约

锁定两条语义（都是「错得很安静、代价很大」的那类）：

1. **fail-open 兜底不是空列表** —— DB 未就绪时返回代码层内置厂商。若返回空列表，
   管理端会显示「一个供应商都没有」，把运维引向「谁把配置删了」的错误方向；
   而 `loaded=True` 且表为空则**必须**如返回空（真·没配，不是故障）。
2. **绝不下发密钥** —— 响应体里不得出现明文/密文，连字段名都不该有（§7.3 硬约束 1）。

鉴权用 `dependency_overrides` 注入假身份（与 test_sys_providers_probe_api.py 同口径）。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_user_actor
from backend.app.api.routes import sys_providers
from backend.infra.llm import models as models_mod
from backend.infra.llm import registry_store
from backend.infra.llm.credentials import ProviderCredentials


class _FakeIdent:
    actor = "user:test-admin"
    role = "admin"
    kind = "user"


class _FakeEditorIdent:
    actor = "user:test-editor"
    role = "editor"
    kind = "user"


@pytest.fixture
def client() -> TestClient:
    a = FastAPI()
    a.include_router(sys_providers.router)
    a.dependency_overrides[require_user_actor] = lambda: _FakeIdent()
    return TestClient(a)


@pytest.fixture(autouse=True)
def _clean():
    registry_store.reset_for_tests()
    yield
    registry_store.reset_for_tests()


def _db_snapshot() -> registry_store.RegistrySnapshot:
    """一内置 + 一自建（自建带凭据元数据，便于验证脱敏与映射）。"""
    return registry_store.RegistrySnapshot(
        providers=[
            {
                "id": "qwen", "display_name": "通义千问", "driver": "openai",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "network_scope": "public", "billing": "metered",
                "is_builtin": True, "enabled": True,
            },
            {
                "id": "glm-coding", "display_name": "GLM 编码包", "driver": "openai",
                "base_url": "http://10.0.0.7:8000/v1", "network_scope": "private",
                "billing": "subscription", "is_builtin": False, "enabled": True,
            },
        ],
        models=[
            {"name": "qwen3.7-plus", "provider": "qwen"},
            {"name": "qwen3.7-flash", "provider": "qwen"},
            {"name": "glm-4.6", "provider": "glm-coding"},
        ],
        credentials={
            "glm-coding": ProviderCredentials(
                provider="glm-coding", api_key="sk-secret-value",
                source="db", version=2,
            )
        },
        credential_meta={
            "glm-coding": {
                "fingerprint": "3f9c1d", "last4": "a1b2",
                "rotatedAt": "2026-09-16T10:00:00+08:00", "rotatedBy": "admin",
            }
        },
        loaded=True,
    )


# ── 限制 1：JWT 用户身份（B3 起读端点放宽，service 身份仍拒）────────────


def test_requires_jwt_user():
    """不覆盖鉴权依赖 → 走真实 `require_user_actor`（无 JWT 应被拒）。"""
    a = FastAPI()
    a.include_router(sys_providers.router)
    assert TestClient(a).get("/sys/providers").status_code in (401, 403)


def test_editor_role_can_read(monkeypatch):
    """B3：editor 只读可见 —— role=editor 的 JWT 用户可拉清单。"""
    monkeypatch.setattr(registry_store, "load_registry", AsyncMock(return_value=_db_snapshot()))
    a = FastAPI()
    a.include_router(sys_providers.router)
    a.dependency_overrides[require_user_actor] = lambda: _FakeEditorIdent()
    body = TestClient(a).get("/sys/providers").json()
    assert body["actor"] == "user:test-editor"
    assert len(body["items"]) == 2


# ── 响应形态：裸 dict（§1.1.1）────────────────────────────────────────


def test_bare_dict_without_result_envelope(client, monkeypatch):
    monkeypatch.setattr(
        registry_store, "load_registry", AsyncMock(return_value=_db_snapshot())
    )
    body = client.get("/sys/providers").json()
    assert isinstance(body["items"], list)
    assert body["source"] == "db"
    assert body["actor"] == "user:test-admin"
    assert "data" not in body and "result" not in body


# ── fail-open 兜底：DB 不可用 → 代码层厂商，而非空列表 ──────────────────


def test_db_unavailable_falls_back_to_builtin(client, monkeypatch):
    monkeypatch.setattr(
        registry_store, "load_registry",
        AsyncMock(return_value=registry_store.RegistrySnapshot(loaded=False)),
    )
    body = client.get("/sys/providers").json()
    assert body["source"] == "builtin"
    ids = [r["id"] for r in body["items"]]
    assert ids == list(models_mod.PROVIDERS)
    assert all(r["isBuiltin"] for r in body["items"])
    assert all(r["networkScope"] == "public" for r in body["items"])


def test_db_loaded_but_empty_is_not_fallback(client, monkeypatch):
    """表存在但为空 → `source='db'` 且空清单。

    必须与「DB 不可用」可区分：前者是真·没配，后者是故障兜底。
    分支依据是 `loaded`，不是 `items` 是否为空。
    """
    monkeypatch.setattr(
        registry_store, "load_registry",
        AsyncMock(return_value=registry_store.RegistrySnapshot(loaded=True)),
    )
    body = client.get("/sys/providers").json()
    assert body["source"] == "db"
    assert body["items"] == []


# ── DB 分支字段映射（对齐 UI 设计 §6 ProviderRow）───────────────────────


def test_db_rows_field_mapping(client, monkeypatch):
    monkeypatch.setattr(
        registry_store, "load_registry", AsyncMock(return_value=_db_snapshot())
    )
    items = {r["id"]: r for r in client.get("/sys/providers").json()["items"]}

    glm = items["glm-coding"]
    assert glm["displayName"] == "GLM 编码包"
    assert glm["networkScope"] == "private"      # 「内网」徽章的依据
    assert glm["billing"] == "subscription"      # 订阅制 → 前端置灰单价区
    assert glm["isBuiltin"] is False
    assert glm["modelCount"] == 1
    assert glm["credential"] == {
        "configured": True, "fingerprint": "3f9c1d", "last4": "a1b2",
        "rotatedAt": "2026-09-16T10:00:00+08:00", "rotatedBy": "admin",
    }
    assert glm["lastProbe"] is None              # 持久化表未建 → 恒「未验证」

    qwen = items["qwen"]
    assert qwen["modelCount"] == 2
    assert qwen["isBuiltin"] is True
    assert qwen["credential"]["configured"] is False
    assert qwen["credential"]["fingerprint"] is None


def test_provider_rows_keep_all_models_when_names_overlap(client, monkeypatch):
    """不同供应商可以登记同名模型，管理端不能因全局去重而漏显示。"""
    snapshot = _db_snapshot()
    snapshot.models.append({"name": "qwen3.7-plus", "provider": "glm-coding"})
    monkeypatch.setattr(
        registry_store, "load_registry", AsyncMock(return_value=snapshot)
    )

    items = {r["id"]: r for r in client.get("/sys/providers").json()["items"]}

    assert {model["name"] for model in items["qwen"]["models"]} == {
        "qwen3.7-plus",
        "qwen3.7-flash",
    }
    assert {model["name"] for model in items["glm-coding"]["models"]} == {
        "glm-4.6",
        "qwen3.7-plus",
    }
    assert items["qwen"]["modelCount"] == 2
    assert items["glm-coding"]["modelCount"] == 2


def test_provider_rows_include_specialized_provider_and_models(client, monkeypatch):
    """专项配置也属于供应商目录，向量/重排模型必须在供应商页可见。"""
    snapshot = _db_snapshot()
    snapshot.providers.append({
        "id": "specialized-rag",
        "display_name": "阿里云百炼专项",
        "driver": "specialized",
        "base_url": "https://dashscope.example",
        "network_scope": "public",
        "billing": "metered",
        "is_builtin": False,
        "enabled": True,
    })
    snapshot.models.extend([
        {
            "name": "qwen3.7-text-embedding",
            "provider": "specialized-rag",
            "model_kind": "embedding",
        },
        {
            "name": "qwen3.7-text-rerank",
            "provider": "specialized-rag",
            "model_kind": "rerank",
        },
    ])
    monkeypatch.setattr(
        registry_store, "load_registry", AsyncMock(return_value=snapshot)
    )

    items = {r["id"]: r for r in client.get("/sys/providers").json()["items"]}

    assert "specialized-rag" in items
    assert items["specialized-rag"]["models"] == [
        {
            "name": "qwen3.7-text-embedding",
            "display": "qwen3.7-text-embedding",
            "modelKind": "embedding",
            "source": "user",
            "usedByRoles": [],
        },
        {
            "name": "qwen3.7-text-rerank",
            "display": "qwen3.7-text-rerank",
            "modelKind": "rerank",
            "source": "user",
            "usedByRoles": [],
        },
    ]


def test_model_entries_carry_source_and_role_usage(client, monkeypatch):
    """列表要就地说明「为什么不能移除」：被角色占用的模型标出角色。

    §B.15 起清单 DB-only：不再有「仅代码层 → builtin 不可移除」——
    不能移除的唯一原因是角色占用。
    """
    snapshot = registry_store.RegistrySnapshot(
        providers=[
            {
                "id": "siliconflow",
                "display_name": "硅基流动",
                "driver": "openai",
                "base_url": "https://api.siliconflow.cn/v1",
                "network_scope": "public",
                "billing": "metered",
                "is_builtin": True,
                "enabled": True,
            },
            {
                "id": "specialized-rag",
                "display_name": "阿里云百炼专项",
                "driver": "specialized",
                "base_url": "https://dashscope.example",
                "network_scope": "public",
                "billing": "metered",
                "is_builtin": False,
                "enabled": True,
            },
        ],
        models=[
            {
                "name": "qwen3.7-text-embedding",
                "provider": "specialized-rag",
                "model_kind": "embedding",
            }
        ],
        roles={"embedding": "qwen3.7-text-embedding"},
        loaded=True,
    )
    monkeypatch.setattr(registry_store, "load_registry", AsyncMock(return_value=snapshot))

    items = {r["id"]: r for r in client.get("/sys/providers").json()["items"]}

    db_model = next(
        m for m in items["specialized-rag"]["models"] if m["name"] == "qwen3.7-text-embedding"
    )
    assert db_model["source"] == "user"
    assert db_model["usedByRoles"] == ["embedding"]

    # siliconflow 无 DB 模型行 → 不再凭代码种子凭空出现模型
    assert items["siliconflow"]["models"] == []


def test_base_url_only_override_is_not_reported_as_managed_key(client, monkeypatch):
    """provider 表的地址覆盖不应伪装成已托管 API Key。"""
    snapshot = registry_store.RegistrySnapshot(
        providers=[{
            "id": "qwen_tp", "display_name": "Qwen Token Plan", "driver": "openai",
            "base_url": "https://token-plan.example/v1", "network_scope": "public",
            "billing": "subscription", "is_builtin": True, "enabled": True,
        }],
        credentials={
            "qwen_tp": ProviderCredentials(
                provider="qwen_tp", base_url="https://token-plan.example/v1", source="db"
            )
        },
        credential_meta={},
        loaded=True,
    )
    monkeypatch.setattr(registry_store, "load_registry", AsyncMock(return_value=snapshot))

    item = client.get("/sys/providers").json()["items"][0]

    assert item["credential"]["configured"] is False


# ── 脱敏：任何密钥载体都不得出现在响应里 ────────────────────────────────


def test_never_leaks_secret(client, monkeypatch):
    monkeypatch.setattr(
        registry_store, "load_registry", AsyncMock(return_value=_db_snapshot())
    )
    raw = client.get("/sys/providers").text
    assert "sk-secret-value" not in raw      # 明文
    assert "key_cipher" not in raw           # 密文列名
    assert "apiKey" not in raw and "api_key" not in raw
