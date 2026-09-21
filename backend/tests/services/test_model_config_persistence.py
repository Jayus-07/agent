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


# ── delete_provider（2026-09-22 拍板：软删 + 关联一并停用/关闭；角色占用仍拒删）───


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _DeleteExec:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)


class _DeleteSession:
    """按语句关键字路由返回值的假会话，并记录全部 SQL。"""

    def __init__(self, provider_row, model_rows=(), role_rows=(),
                 specialized_rows=(), price_rows=()):
        self.provider_row = provider_row
        self.model_rows = list(model_rows)
        self.role_rows = list(role_rows)
        self.specialized_rows = list(specialized_rows)
        self.price_rows = list(price_rows)
        self.statements: list[str] = []
        self.commit_count = 0

    async def execute(self, statement, _params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "FROM llm_providers WHERE id" in sql:
            return _DeleteExec([self.provider_row] if self.provider_row else [])
        if "FROM llm_models WHERE provider_id" in sql:
            return _DeleteExec(self.model_rows)
        if "FROM llm_model_role_bindings" in sql:
            return _DeleteExec(self.role_rows)
        if "FROM llm_specialized_model_bindings" in sql:
            return _DeleteExec(self.specialized_rows)
        if "FROM model_price" in sql:
            return _DeleteExec(self.price_rows)
        return _DeleteExec([])

    async def commit(self):
        self.commit_count += 1


def _provider_row(**overrides):
    row = {
        "id": "custom-host",
        "display_name": "自建供应商",
        "driver": "openai",
        "base_url": "https://custom.example/v1",
        "billing": "metered",
        "is_builtin": False,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_delete_provider_soft_deletes_models_and_closes_associations(monkeypatch):
    """软删：供应商/模型 enabled=false，价格关闭、专项停用，凭据保留，无物理 DELETE。"""
    session = _DeleteSession(
        provider_row=_provider_row(),
        model_rows=[{"name": "custom-chat", "display_name": "自建模型", "model_kind": "chat"}],
    )
    refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(model_config, "get_session", lambda: _session_stream(session))
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)

    result = await model_config.ModelConfigService().delete_provider(
        "custom-host", "user:test-admin"
    )

    assert result["providerId"] == "custom-host"
    assert result["removedModels"] == ["custom-chat"]
    assert result["softDeleted"] is True
    joined = "\n".join(session.statements)
    assert "UPDATE llm_models SET enabled = false" in joined
    assert "UPDATE llm_providers SET enabled = false" in joined
    assert "UPDATE llm_specialized_model_bindings" in joined
    assert "UPDATE model_price SET effective_to = now()" in joined
    # 软删语义：不物理删任何行，凭据保留（恢复供应商时密钥仍在）
    assert not any("DELETE FROM llm_models" in sql for sql in session.statements)
    assert not any("DELETE FROM llm_providers" in sql for sql in session.statements)
    assert not any("DELETE FROM llm_provider_credentials" in sql for sql in session.statements)
    assert any("INSERT INTO llm_config_history" in sql for sql in session.statements)
    assert session.commit_count == 1
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_provider_rejects_when_role_binding_uses_model(monkeypatch):
    """名下模型被普通角色绑定时拒绝删除，提示先改绑。"""
    session = _DeleteSession(
        provider_row=_provider_row(),
        model_rows=[{"name": "custom-chat", "display_name": "自建模型", "model_kind": "chat"}],
        role_rows=[{"role": "main"}],
    )
    monkeypatch.setattr(
        model_config, "get_session", lambda: _session_stream(session)
    )
    monkeypatch.setattr(
        model_config.registry_store, "refresh_registry", AsyncMock(return_value=True)
    )

    with pytest.raises(model_config.ModelConfigConflict, match="main"):
        await model_config.ModelConfigService().delete_provider(
            "custom-host", "user:test-admin"
        )
    assert not any(
        "UPDATE llm_providers SET enabled = false" in sql
        for sql in session.statements
    )


@pytest.mark.asyncio
async def test_delete_provider_rejects_builtin_only(monkeypatch):
    """内置供应商一律拒删；专项通道占用不再拦截（随软删一并停用）。"""
    monkeypatch.setattr(
        model_config.registry_store, "refresh_registry", AsyncMock(return_value=True)
    )

    builtin_session = _DeleteSession(provider_row=_provider_row(is_builtin=True))
    monkeypatch.setattr(
        model_config, "get_session", lambda: _session_stream(builtin_session)
    )
    with pytest.raises(model_config.ModelConfigConflict, match="内置"):
        await model_config.ModelConfigService().delete_provider(
            "qwen", "user:test-admin"
        )
    assert not any(
        "UPDATE llm_providers SET enabled = false" in sql
        for sql in builtin_session.statements
    )


@pytest.mark.asyncio
async def test_delete_provider_soft_disables_specialized_bindings(monkeypatch):
    """旧专项卡占用不再拒删：绑定行 enabled=false 停用，供应商本体软删。"""
    session = _DeleteSession(
        provider_row=_provider_row(id="specialized-api"),
        model_rows=[{"name": "qwen3.7-text-embedding", "display_name": "向量", "model_kind": "embedding"}],
        specialized_rows=[{"role": "embedding"}],
    )
    refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(model_config, "get_session", lambda: _session_stream(session))
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)

    result = await model_config.ModelConfigService().delete_provider(
        "specialized-api", "user:test-admin"
    )

    assert result["softDeleted"] is True
    joined = "\n".join(session.statements)
    assert (
        "UPDATE llm_specialized_model_bindings" in joined
        and "enabled = false" in joined
    )
    assert "UPDATE llm_providers SET enabled = false" in joined
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_provider_rejects_unknown_provider(monkeypatch):
    session = _DeleteSession(provider_row=None)
    monkeypatch.setattr(
        model_config, "get_session", lambda: _session_stream(session)
    )
    monkeypatch.setattr(
        model_config.registry_store, "refresh_registry", AsyncMock(return_value=True)
    )

    with pytest.raises(model_config.ModelConfigNotFound):
        await model_config.ModelConfigService().delete_provider(
            "ghost", "user:test-admin"
        )
