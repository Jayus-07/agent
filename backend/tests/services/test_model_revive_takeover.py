"""软删模型行的「复活过户」（2026-09-22 拍板）。

场景：删除供应商后其模型名成为幽灵占用（llm_models 主键=模型名，软删行
仍在表中），用户在新供应商下重加同名模型被 409 卡死。修复语义：占用行
enabled=false 时允许复活并过户到新供应商；enabled=true 的占用仍拒绝。
"""
import pytest

import backend.services.model_config as model_config
from backend.services.model_config import (
    ModelConfigConflict,
    ModelConfigService,
)


class _UpsertSession:
    """按 SQL 关键字路由的假会话，记录全部语句供断言。"""

    def __init__(self, existing_row, provider_row=None):
        self.existing_row = existing_row
        self.provider_row = provider_row or _provider_row()
        self.statements: list[str] = []

    async def execute(self, statement, _params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT * FROM llm_providers WHERE id" in sql:
            provider_row = self.provider_row

            class _PR:
                def mappings(self):
                    provider = provider_row

                    class _M:
                        def first(self):
                            return provider

                    return _M()

            return _PR()
        if "SELECT provider_id, enabled FROM llm_models" in sql:
            row = self.existing_row

            class _Dup:
                def mappings(self):
                    existing = row

                    class _M:
                        def first(self):
                            return existing

                    return _M()

            return _Dup()
        if "SELECT provider_id, model_kind, enabled FROM llm_models" in sql:
            rows = [self.existing_row] if self.existing_row else []

            class _R:
                def __init__(self, rows):
                    self._rows = rows

                def mappings(self):
                    rows = self._rows

                    class _M:
                        def __init__(self, rows):
                            self._rows = rows

                        def first(self):
                            return self._rows[0] if self._rows else None

                    return _M(rows)

            return _R(rows)
        if "FROM llm_providers WHERE id" in sql:
            class _P:
                def mappings(self):
                    class _PM:
                        def first(self):
                            return {"driver": "openai", "id": "legacy"}
                    return _PM()
            return _P()
        return None

    async def commit(self):
        pass


async def _stream(session):
    yield session


def _provider_row(provider_id="new-relay", driver="openai"):
    return {
        "id": provider_id,
        "driver": driver,
        "base_url": "https://relay.example/v1",
        "network_scope": "public",
        "billing": "metered",
        "is_builtin": False,
    }


@pytest.mark.asyncio
async def test_add_model_revives_soft_deleted_row_into_new_provider(monkeypatch):
    """占用行 enabled=false → 复活并过户：UPDATE provider_id + enabled=true。"""
    session = _UpsertSession({
        "provider_id": "legacy-qwen",
        "model_kind": "chat",
        "enabled": False,
    })
    monkeypatch.setattr(model_config, "get_session", lambda: _stream(session))
    monkeypatch.setattr(
        model_config.registry_store, "refresh_registry", pytest.AsyncMock(return_value=True)
    ) if hasattr(pytest, "AsyncMock") else None
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        model_config.registry_store, "refresh_registry", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        model_config.ModelConfigService, "record_probe", AsyncMock()
    )
    monkeypatch.setattr(
        model_config.provider_probe, "probe_provider",
        AsyncMock(return_value=_ok_probe()),
    )
    monkeypatch.setattr(model_config, "_apply_model_pricing", AsyncMock())
    monkeypatch.setattr(
        model_config.credentials_mod, "resolve_credentials",
        lambda pid: _cred(),
    )

    svc = ModelConfigService()

    async def _read_secret(_pid):
        return "sk-test"

    monkeypatch.setattr(svc, "_read_provider_secret", _read_secret)

    await svc.add_provider_model(
        "new-relay",
        {"modelName": "qwen3.7-plus", "modelKind": "chat"},
        "user:1",
    )
    takeover_sql = [
        s for s in session.statements
        if "UPDATE llm_models SET provider_id" in s and "enabled = true" in s
    ]
    assert takeover_sql, "应执行复活过户 UPDATE（provider_id + enabled=true）"


@pytest.mark.asyncio
async def test_add_model_still_rejects_live_duplicate_on_other_provider(monkeypatch):
    """占用行 enabled=true → 仍然拒绝（模型名全局唯一语义不变）。"""
    session = _UpsertSession({
        "provider_id": "other-provider",
        "model_kind": "chat",
        "enabled": True,
    })
    monkeypatch.setattr(model_config, "get_session", lambda: _stream(session))

    with pytest.raises(ModelConfigConflict, match="已登记到供应商 other-provider"):
        await ModelConfigService().add_provider_model(
            "new-relay",
            {"modelName": "qwen3.7-plus", "modelKind": "chat"},
            "user:1",
        )


class _Cred:
    api_key = "sk-test"
    base_url = "https://relay.example/v1"


def _cred():
    return _Cred()


def _ok_probe():
    from backend.services.provider_probe import ProbeResult, ProbeStep

    return ProbeResult(
        ok=True,
        blocked_at=None,
        summary="模型连通性通过（快速测试）",
        steps=[ProbeStep("L2", "pass", "模型名可用")],
    )
