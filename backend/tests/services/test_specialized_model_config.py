"""专项模型 test-and-save 的事务与密钥边界。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services import model_config
from backend.services.specialized_model_probe import SpecializedProbeResult


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Session:
    def __init__(self):
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(dict(params or {}))
        return _Result(None)

    async def commit(self):
        self.commit_count += 1


async def _session_stream(session):
    yield session


def _payload(api_key: str = "sk-test-placeholder") -> dict:
    return {
        "provider": {
            "displayName": "阿里云百炼专项",
            "baseUrl": "https://dashscope.aliyuncs.com",
            "apiKey": api_key,
        },
        "bindings": {
            "embedding": {
                "modelName": "qwen3.7-text-embedding",
                "adapter": "dashscope_embedding",
                "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "options": {"dimensions": 1024},
            },
            "rerank": {
                "modelName": "qwen3.7-text-rerank",
                "adapter": "dashscope_rerank",
                "baseUrl": "https://dashscope.aliyuncs.com/api/v1",
                "options": {},
            },
        },
    }


@pytest.mark.asyncio
async def test_failed_specialized_probe_does_not_persist(monkeypatch):
    session = _Session()
    refresh = AsyncMock(return_value=True)

    async def failed_probe(**kwargs):
        del kwargs
        return SpecializedProbeResult(
            role="rerank",
            adapter="dashscope_rerank",
            ok=False,
            status_code=401,
            summary="Key 无效",
            detail="InvalidApiKey",
            elapsed_ms=123,
        )

    monkeypatch.setattr(model_config, "get_session", lambda: _session_stream(session))
    monkeypatch.setattr(model_config.specialized_model_probe, "probe_specialized", failed_probe)
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)

    result = await model_config.ModelConfigService().configure_specialized(
        _payload(), "user:test-admin"
    )

    assert result["ok"] is False
    assert result["saved"] is False
    assert result["tests"][0]["elapsedMs"] == 123
    assert session.commit_count == 0
    assert not any("INSERT" in statement for statement in session.statements)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_specialized_config_encrypts_key_and_returns_no_secret(monkeypatch):
    session = _Session()
    refresh = AsyncMock(return_value=True)

    async def successful_probe(**kwargs):
        return SpecializedProbeResult(
            role=kwargs["role"],
            adapter=kwargs["adapter"],
            ok=True,
            status_code=200,
            summary="专项模型调用通过",
            detail="已收到有效响应",
            elapsed_ms=87,
        )

    monkeypatch.setattr(model_config, "get_session", lambda: _session_stream(session))
    monkeypatch.setattr(model_config.specialized_model_probe, "probe_specialized", successful_probe)
    monkeypatch.setattr(model_config, "encrypt_secret", lambda value: "enc:cipher")
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)

    result = await model_config.ModelConfigService().configure_specialized(
        _payload(), "user:test-admin"
    )

    assert result["ok"] is True
    assert result["saved"] is True
    assert result["provider"]["credential"]["last4"] == "lder"
    assert all(item["ok"] for item in result["tests"])
    assert "sk-test-placeholder" not in str(result)
    model_inserts = [
        params for statement, params in zip(session.statements, session.params)
        if "INSERT INTO llm_models" in statement
    ]
    assert {params["model_kind"] for params in model_inserts} == {"embedding", "rerank"}
    assert any("llm_specialized_model_bindings" in statement for statement in session.statements)
    assert any(
        params.get("key_cipher") == "enc:cipher"
        for params in session.params
    )
    assert session.commit_count == 1
    refresh.assert_awaited_once()
