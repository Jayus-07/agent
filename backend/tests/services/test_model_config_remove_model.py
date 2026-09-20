"""`remove_provider_model` 的删除前红线校验。

这些用例锁住的核心是「**删之前挡住**」：引用表没有外键，一旦删了模型，
悬空的角色 / 专项 / 价格引用补不回来，而 main / embedding / rerank 这类
角色缺失会直接让线上能力不可用。所以每条红线都要有独立用例。

用 fake session 打桩 `get_session`（与 `test_model_config_persistence.py` 同路数），
不依赖真实数据库。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services import model_config
from backend.services.model_config import ModelConfigConflict, ModelConfigNotFound


class _Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Session:
    """按 SQL 关键字分派返回，并记录执行过的语句供断言。"""

    def __init__(self, *, model_row=None, role_rows=(), specialized_rows=(), price_total=0):
        self.model_row = model_row
        self.role_rows = list(role_rows)
        self.specialized_rows = list(specialized_rows)
        self.price_total = price_total
        self.executed: list[str] = []
        self.params: list[dict] = []
        self.commit_count = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append(sql)
        self.params.append(dict(params or {}))
        if "llm_models WHERE name" in sql:
            return _Result([self.model_row] if self.model_row else [])
        if "llm_model_role_bindings" in sql:
            return _Result(self.role_rows)
        if "llm_specialized_model_bindings" in sql:
            return _Result(self.specialized_rows)
        if "model_price" in sql:
            return _Result([{"total": self.price_total}])
        return _Result()

    async def commit(self):
        self.commit_count += 1


async def _session_stream(session):
    yield session


def _wire(monkeypatch, session, *, code_entry=None):
    refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(model_config, "get_session", lambda: _session_stream(session))
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", refresh)
    monkeypatch.setattr(
        model_config.models_mod,
        "get_model_entry",
        lambda name: code_entry if code_entry and code_entry.get("name") == name else None,
    )
    return refresh


USER_MODEL = {"provider_id": "custom-a", "display_name": "自建向量模型", "model_kind": "embedding"}


@pytest.mark.asyncio
async def test_removes_user_model_and_records_non_rollbackable_history(monkeypatch):
    session = _Session(model_row=USER_MODEL)
    refresh = _wire(monkeypatch, session)

    result = await model_config.ModelConfigService().remove_provider_model(
        "custom-a", "my-embedding", "user:test-admin"
    )

    assert result == {
        "providerId": "custom-a",
        "name": "my-embedding",
        "display": "自建向量模型",
        "modelKind": "embedding",
    }
    joined = "\n".join(session.executed)
    assert "DELETE FROM llm_models" in joined
    assert "INSERT INTO llm_config_history" in joined
    # 复用 object_type='provider'，但必须显式不可回滚 —— 否则回滚会拿模型信息去覆盖 provider
    history_sql = next(s for s in session.executed if "llm_config_history" in s)
    assert "'provider'" in history_sql
    assert "false" in history_sql
    assert session.commit_count == 1
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejects_code_layer_builtin_model(monkeypatch):
    session = _Session(model_row=None)
    refresh = _wire(
        monkeypatch, session, code_entry={"name": "Qwen/Qwen3-32B", "provider": "siliconflow"}
    )

    with pytest.raises(ModelConfigConflict) as exc:
        await model_config.ModelConfigService().remove_provider_model(
            "siliconflow", "Qwen/Qwen3-32B", "user:test-admin"
        )

    assert "内置模型" in str(exc.value)
    assert not any("DELETE FROM llm_models" in s for s in session.executed)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_model_returns_not_found(monkeypatch):
    session = _Session(model_row=None)
    _wire(monkeypatch, session)

    with pytest.raises(ModelConfigNotFound) as exc:
        await model_config.ModelConfigService().remove_provider_model(
            "custom-a", "nope", "user:test-admin"
        )

    assert "未找到模型" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_model_owned_by_another_provider(monkeypatch):
    session = _Session(model_row={"provider_id": "custom-b", "model_kind": "chat"})
    _wire(monkeypatch, session)

    with pytest.raises(ModelConfigConflict) as exc:
        await model_config.ModelConfigService().remove_provider_model(
            "custom-a", "shared-name", "user:test-admin"
        )

    assert "custom-b" in str(exc.value)
    assert not any("DELETE FROM llm_models" in s for s in session.executed)


@pytest.mark.asyncio
async def test_rejects_model_bound_to_roles(monkeypatch):
    session = _Session(model_row=USER_MODEL, role_rows=[{"role": "embedding"}, {"role": "rerank"}])
    refresh = _wire(monkeypatch, session)

    with pytest.raises(ModelConfigConflict) as exc:
        await model_config.ModelConfigService().remove_provider_model(
            "custom-a", "my-embedding", "user:test-admin"
        )

    message = str(exc.value)
    assert "embedding" in message and "rerank" in message
    assert "角色绑定" in message
    assert not any("DELETE FROM llm_models" in s for s in session.executed)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_model_bound_to_specialized_channel(monkeypatch):
    session = _Session(model_row=USER_MODEL, specialized_rows=[{"role": "embedding"}])
    _wire(monkeypatch, session)

    with pytest.raises(ModelConfigConflict) as exc:
        await model_config.ModelConfigService().remove_provider_model(
            "custom-a", "my-embedding", "user:test-admin"
        )

    assert "专项通道" in str(exc.value)


@pytest.mark.asyncio
async def test_rejects_model_with_price_rows(monkeypatch):
    session = _Session(model_row=USER_MODEL, price_total=4)
    _wire(monkeypatch, session)

    with pytest.raises(ModelConfigConflict) as exc:
        await model_config.ModelConfigService().remove_provider_model(
            "custom-a", "my-embedding", "user:test-admin"
        )

    assert "价格表" in str(exc.value)


@pytest.mark.asyncio
async def test_blank_arguments_rejected(monkeypatch):
    session = _Session(model_row=USER_MODEL)
    _wire(monkeypatch, session)

    with pytest.raises(ModelConfigNotFound):
        await model_config.ModelConfigService().remove_provider_model(
            "  ", "my-embedding", "user:test-admin"
        )
    with pytest.raises(ValueError):
        await model_config.ModelConfigService().remove_provider_model(
            "custom-a", "   ", "user:test-admin"
        )
