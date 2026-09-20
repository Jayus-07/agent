"""api/routes/sys_model_roles.py —— GET /sys/model-roles 契约

锁定三件事：

1. **source 归一化不得漏**：内部返回 `'code-default'` 与 `'inherit:<父role>'`，
   而 §5.4 契约只认 `db | env | inherit | default`。漏一个，前端 TypeScript 判别
   会静默落到 `else` 分支（本项目对「静默降级」有前科，见 UI 设计 §5.3）。
2. **`provider` 与 `registered` 必须同源**（都用当前可用清单）：若 `provider` 取自
   代码层常量而 `registered` 取自含动态层的清单，自建模型会显示「已注册但无所属
   供应商」—— 自相矛盾且无法排查。
3. **绝不下发密钥**（§7.3 硬约束 1）。

鉴权用 `dependency_overrides` 注入假身份（与相邻路由测试同口径）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_user_actor
from backend.app.api.routes import sys_model_roles
from backend.config import llm as config_llm
from backend.config import model_roles
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm.credentials import ProviderCredentials

CONTRACT_SOURCES = {"db", "env", "inherit", "default"}


class _FakeIdent:
    actor = "user:test-admin"
    role = "admin"
    kind = "user"


@pytest.fixture
def client() -> TestClient:
    a = FastAPI()
    a.include_router(sys_model_roles.router)
    a.dependency_overrides[require_user_actor] = lambda: _FakeIdent()
    return TestClient(a)


@pytest.fixture(autouse=True)
def _clean():
    yield
    model_roles.reset_overrides()
    models_mod.reset_dynamic_models_for_tests()
    credentials_mod.reset_credentials_for_tests()


def _a_registered_qwen_model() -> str:
    """取一个 provider='qwen' 的已注册模型名（不写字面量，避免与清单漂移）。"""
    entry = next(m for m in models_mod.AVAILABLE_MODELS if m["provider"] == "qwen")
    return str(entry["name"])


# ── 限制：admin only ────────────────────────────────────────────────────


def test_requires_admin():
    a = FastAPI()
    a.include_router(sys_model_roles.router)
    assert TestClient(a).get("/sys/model-roles").status_code in (401, 403)


# ── 形态与契约 ──────────────────────────────────────────────────────────


def test_bare_dict_and_all_sources_within_contract(client):
    body = client.get("/sys/model-roles").json()
    assert isinstance(body["items"], list) and body["items"]
    assert body["actor"] == "user:test-admin"
    assert "data" not in body and "result" not in body

    for row in body["items"]:
        # 契约字段齐全（label 按 §5.4 由前端持有，不在响应内）
        assert set(row) >= {
            "role", "effectiveModel", "literalValue", "source", "inheritedFrom",
            "provider", "registered", "missingKeyEnv", "requiresReindex",
            "updatedBy", "updatedAt",
        }
        assert "label" not in row
        # 归一化必须收敛到契约集合：'code-default' / 'inherit:main' 漏出去即失败
        assert row["source"] in CONTRACT_SOURCES, row


def test_schema_source_normalization():
    """纯函数三分支 + 继承前缀（继承态父 role 走 inheritedFrom，不进 source）。"""
    assert sys_model_roles._schema_source(model_roles.SOURCE_DEFAULT) == "default"
    assert sys_model_roles._schema_source("inherit:main") == "inherit"
    assert sys_model_roles._schema_source("db") == "db"
    assert sys_model_roles._schema_source("env") == "env"


# ── DB 覆盖层：接线后能如实透传 ──────────────────────────────────────────


def test_db_override_source_passthrough(client):
    """注入 DB 覆盖 → `source='db'`（当前注入通道未接线，此测锁的是端点的透传能力）。"""
    model = _a_registered_qwen_model()
    model_roles.inject_overrides({"main": model})

    row = {r["role"]: r for r in client.get("/sys/model-roles").json()["items"]}
    assert row["main"]["source"] == "db"
    assert row["main"]["effectiveModel"] == model


# ── provider / registered 同源（含动态层）───────────────────────────────


def test_provider_and_registered_share_available_catalog(client):
    """自建模型（只在动态层）→ registered=True **且** provider 正确。

    这正是 `model_roles.provider_of` 做不到的：它只看代码层 `AVAILABLE_MODELS`，
    会让自建模型 fall through 到 `provider=None`，与 `registered=True` 自相矛盾。
    """
    models_mod.set_dynamic_models(
        [{"name": "glm-4.6", "provider": "glm-coding", "source": "db"}]
    )
    model_roles.inject_overrides({"main": "glm-4.6"})

    row = {r["role"]: r for r in client.get("/sys/model-roles").json()["items"]}
    main = row["main"]
    assert main["registered"] is True
    assert main["provider"] == "glm-coding"


def test_provider_label_from_builtin_providers(client):
    """`providerLabel` 取 `PROVIDERS[].label`（厂商中文名，管理端展示用）。

    代码内置厂商必有 label；db 自建供应商不在 PROVIDERS 里 → 回落 provider 代码
    （其显示名在 provider_registry 的 display_name，roles API 不为此查 registry）。
    """
    model = _a_registered_qwen_model()
    model_roles.inject_overrides({"main": model})

    row = {r["role"]: r for r in client.get("/sys/model-roles").json()["items"]}
    assert row["main"]["providerLabel"] == "阿里云百炼"

    # 自建 provider 回落代码，且 11 个角色全部带 providerLabel 字段（契约字段不下线）
    models_mod.set_dynamic_models(
        [{"name": "glm-4.6", "provider": "glm-coding", "source": "db"}]
    )
    model_roles.inject_overrides({"main": "glm-4.6"})
    items = client.get("/sys/model-roles").json()["items"]
    main = next(r for r in items if r["role"] == "main")
    assert main["providerLabel"] == "glm-coding"
    assert all("providerLabel" in item for item in items)


def test_unregistered_model_reported_as_not_registered(client):
    model_roles.inject_overrides({"main": "no-such-model"})

    row = {r["role"]: r for r in client.get("/sys/model-roles").json()["items"]}
    main = row["main"]
    assert main["registered"] is False
    assert main["provider"] is None
    assert main["missingKeyEnv"] is None      # 无 provider → 不报缺 Key（不是同一种问题）


def test_disabled_ollama_role_reports_actionable_unavailable_reason(client, monkeypatch):
    monkeypatch.setattr(sys_model_roles.config_llm, "OLLAMA_ENABLED", False)
    model_roles.inject_overrides({"eval_gen": "qwen2.5:3b"})

    row = {
        r["role"]: r for r in client.get("/sys/model-roles").json()["items"]
    }["eval_gen"]

    assert row["available"] is False
    assert "Ollama 当前未启用" in row["availabilityReason"]


# ── missingKeyEnv ───────────────────────────────────────────────────────


def test_missing_key_env_reported_when_key_absent(client, monkeypatch):
    model = _a_registered_qwen_model()
    monkeypatch.setattr(config_llm, "QWEN_API_KEY", "")
    credentials_mod.reset_credentials_for_tests()      # 确保走 env 分支
    model_roles.inject_overrides({"main": model})

    row = {r["role"]: r for r in client.get("/sys/model-roles").json()["items"]}
    assert row["main"]["missingKeyEnv"] == models_mod.PROVIDER_API_KEY_ENV["qwen"]


def test_missing_key_env_cleared_when_credential_present(client, monkeypatch):
    """DB 凭据齐备 → 即便 env 为空也不算缺 Key（优先级 DB > env）。"""
    model = _a_registered_qwen_model()
    monkeypatch.setattr(config_llm, "QWEN_API_KEY", "")
    credentials_mod.set_db_credentials(
        {"qwen": ProviderCredentials(provider="qwen", api_key="sk-test", source="db", version=1)}
    )
    model_roles.inject_overrides({"main": model})

    row = {r["role"]: r for r in client.get("/sys/model-roles").json()["items"]}
    assert row["main"]["missingKeyEnv"] is None


# ── 脱敏 ────────────────────────────────────────────────────────────────


def test_never_leaks_secret(client):
    credentials_mod.set_db_credentials(
        {"qwen": ProviderCredentials(provider="qwen", api_key="sk-secret-value", source="db")}
    )
    raw = client.get("/sys/model-roles").text
    assert "sk-secret-value" not in raw
    assert "apiKey" not in raw and "api_key" not in raw
