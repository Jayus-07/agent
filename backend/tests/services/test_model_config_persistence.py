"""模型配置治理写入必须在提前退出会话迭代器前显式提交。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services import model_config
from backend.services.provider_probe import ProbeResult, ProbeStep


class _Result:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _Session:
    def __init__(self):
        self.commit_count = 0

    async def execute(self, statement, _params=None):
        if "SELECT model_name" in str(statement):
            return _Result(None)
        return _Result(None)

    async def commit(self):
        self.commit_count += 1


async def _session_stream(session):
    yield session


@pytest.mark.asyncio
async def test_update_role_commits_before_breaking_session_stream(monkeypatch):
    session = _Session()
    refresh = AsyncMock(return_value=True)

    monkeypatch.setattr(
        model_config,
        "get_session",
        lambda: _session_stream(session),
    )
    monkeypatch.setattr(model_config, "_model_validation_issue", lambda *a, **k: None)
    monkeypatch.setattr(model_config.model_roles, "set_override", lambda *a, **k: None)
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)

    result = await model_config.ModelConfigService().update_role(
        "fallback",
        "qwen3.7-plus",
        "user:test-admin",
    )

    assert result["newValue"] == "qwen3.7-plus"
    assert session.commit_count == 1
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_role_rejects_unregistered_specialized_model(monkeypatch):
    """专项角色也只能绑定模型目录中的名称，不能绕过管理端写入自由文本。"""
    monkeypatch.setattr(model_config.models_mod, "get_model_entry", lambda _name: None)
    monkeypatch.setattr(
        model_config,
        "get_session",
        lambda: pytest.fail("未登记模型不应打开写事务"),
    )

    with pytest.raises(ValueError, match="未知模型"):
        await model_config.ModelConfigService().update_role(
            "rerank",
            "qwen3.7-text-reran",
            "user:test-admin",
        )


class _CreateResult:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _CreateSession:
    def __init__(self):
        self.commit_count = 0
        self.statements: list[str] = []
        self.parameters: list[dict] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.parameters.append(dict(params or {}))
        return _CreateResult(None)

    async def commit(self):
        self.commit_count += 1


async def test_create_provider_registers_model_and_encrypts_key(monkeypatch):
    session = _CreateSession()
    refresh = AsyncMock(return_value=True)

    monkeypatch.setattr(
        model_config,
        "get_session",
        lambda: _session_stream(session),
    )
    monkeypatch.setattr(model_config, "encrypt_secret", lambda value: "enc:cipher")
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)

    async def _probe(**_kwargs):
        return ProbeResult(
            ok=True,
            blocked_at=None,
            summary="模型连通性通过（快速测试）",
            steps=[ProbeStep("L2", "pass", "模型名可用")],
        )

    monkeypatch.setattr(model_config.provider_probe, "probe_provider", _probe)

    result = await model_config.ModelConfigService().create_provider(
        {
            "baseUrl": "https://provider.example/v1",
            "apiKey": "sk-secret-value",
            "modelName": "custom-model",
        },
        "user:test-admin",
    )

    provider_insert = next(i for i, sql in enumerate(session.statements) if "INSERT INTO llm_providers" in sql)
    model_insert = next(i for i, sql in enumerate(session.statements) if "INSERT INTO llm_models" in sql)
    credential_params = next(
        params for sql, params in zip(session.statements, session.parameters)
        if "INSERT INTO llm_provider_credentials" in sql
    )

    assert provider_insert < model_insert
    assert credential_params["key_cipher"] == "enc:cipher"
    assert "sk-secret-value" not in str(session.parameters)
    assert result["id"].startswith("custom-provider-example")
    assert result["modelName"] == "custom-model"
    assert result["credential"]["last4"] == "alue"
    assert session.commit_count == 1
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_provider_does_not_persist_when_probe_fails(monkeypatch):
    async def _probe(**_kwargs):
        return ProbeResult(
            ok=False,
            blocked_at="L2",
            summary="未通过（卡在 L2）：模型名错误",
            steps=[ProbeStep("L2", "fail", "模型名错误")],
        )

    monkeypatch.setattr(model_config.provider_probe, "probe_provider", _probe)
    monkeypatch.setattr(
        model_config,
        "get_session",
        lambda: pytest.fail("探测失败时不得打开写事务"),
    )

    with pytest.raises(ValueError, match="模型名错误"):
        await model_config.ModelConfigService().create_provider(
            {
                "baseUrl": "https://provider.example/v1",
                "apiKey": "sk-secret-value",
                "modelName": "bad-model",
            },
            "user:test-admin",
        )


class _LegacyMigrationResult:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _LegacyMigrationSession:
    def __init__(self):
        self.statements: list[str] = []
        self.parameters: list[dict] = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.parameters.append(dict(params or {}))
        if "SELECT 1 FROM llm_providers" in sql:
            return _LegacyMigrationResult(None)
        if "SELECT provider_id, model_kind FROM llm_models" in sql:
            return _LegacyMigrationResult({
                "provider_id": "specialized-api",
                "model_kind": "embedding",
            })
        if "SELECT driver FROM llm_providers" in sql:
            return _LegacyMigrationResult({"driver": "specialized"})
        return _LegacyMigrationResult(None)

    async def commit(self):
        self.commit_count += 1


@pytest.mark.asyncio
async def test_create_provider_claims_same_model_from_legacy_specialized_provider(monkeypatch):
    """新 Key 注册历史专项模型时，只迁移专项旧目录，不允许覆盖普通供应商。"""
    session = _LegacyMigrationSession()
    refresh = AsyncMock(return_value=True)

    monkeypatch.setattr(model_config, "get_session", lambda: _session_stream(session))
    monkeypatch.setattr(model_config, "encrypt_secret", lambda value: "enc:cipher")
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)
    monkeypatch.setattr(model_config.ModelConfigService, "record_probe", AsyncMock())
    monkeypatch.setattr(
        model_config.models_mod,
        "get_model_entry",
        lambda _name: {"provider": "specialized-api", "model_kind": "embedding"},
    )

    async def _probe(**_kwargs):
        return ProbeResult(
            ok=True,
            blocked_at=None,
            summary="模型连通性通过（快速测试）",
            steps=[ProbeStep("L2", "pass", "模型名可用")],
        )

    monkeypatch.setattr(model_config.provider_probe, "probe_provider", _probe)

    result = await model_config.ModelConfigService().create_provider(
        {
            "displayName": "百炼向量新 Key",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "apiKey": "sk-new-placeholder",
            "modelName": "qwen3.7-text-embedding",
            "modelKind": "embedding",
        },
        "user:test-admin",
    )

    assert result["modelName"] == "qwen3.7-text-embedding"
    assert any("UPDATE llm_models SET provider_id" in sql for sql in session.statements)
    assert any("UPDATE llm_specialized_model_bindings" in sql for sql in session.statements)
    assert session.commit_count == 1
    refresh.assert_awaited_once()
