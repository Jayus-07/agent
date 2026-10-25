"""嵌入角色的预检、费用和后端确认边界，不调用真实供应商。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.infra.llm import specialized
from backend.rag import embedding_singleton
from backend.rag.indexing import embedding_migration
from backend.services import model_config


@pytest.fixture
def preview_env(monkeypatch):
    binding = specialized.SpecializedBinding(
        role="embedding", provider_id="provider", adapter="openai_embedding",
        model_name="new", base_url="https://provider.example/v1",
    )
    monkeypatch.setattr(model_config, "_model_validation_issue", lambda *a, **k: None)
    monkeypatch.setattr(model_config.registry_store, "refresh_registry", AsyncMock(return_value=True))
    monkeypatch.setattr(specialized, "registered_binding", lambda *a: binding)
    monkeypatch.setattr(model_config.model_roles, "resolve_effective", lambda role: {"value": "old"})
    monkeypatch.setattr(model_config.models_mod, "get_model_billing", lambda name: "local")
    monkeypatch.setattr(embedding_migration, "storage_snapshot", lambda: {
        "dimension": 1024, "vectors": 2,
        "collections": [{"collection": "chroma", "vectors": 2,
                         "model": "old", "fingerprint": "old", "status": "ready"}],
    })
    model = SimpleNamespace(embed_documents=lambda texts: [[0.1] * 1024 for _ in texts])
    monkeypatch.setattr(embedding_singleton, "get_registered_embedding", lambda name: model)
    monkeypatch.setattr(model_config, "get_session", lambda: pytest.fail("预检或拒绝不得写角色"))
    return model


@pytest.mark.asyncio
async def test_preview_reports_real_scope_and_dimension_without_role_write(preview_env):
    result = await model_config.ModelConfigService().preview_embedding_change("new")
    assert result["dimension"] == 1024
    assert result["vectors"] == 2
    assert result["requiresRebuild"] is True
    assert result["currentModel"] == "old"
    assert result["traceId"]


@pytest.mark.asyncio
async def test_wrong_dimension_rejected_before_role_or_index_mutation(preview_env):
    preview_env.embed_documents = lambda texts: [[0.1] * 1536]
    with pytest.raises(ValueError, match="1024"):
        await model_config.ModelConfigService().preview_embedding_change("new")


@pytest.mark.asyncio
async def test_unpriced_cloud_rejected_before_model_call(preview_env, monkeypatch):
    from backend.infra.llm import pricing

    monkeypatch.setattr(model_config.models_mod, "get_model_billing", lambda name: "metered")
    monkeypatch.setattr(pricing, "get_current_price_table", lambda *a: pricing.PriceTable([]))
    preview_env.embed_documents = lambda texts: pytest.fail("缺价时不应先调用模型花钱")
    with pytest.raises(ValueError, match="价格"):
        await model_config.ModelConfigService().preview_embedding_change("new")


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,message", [
    ({}, "配置已变化"),
    ({"expected_model": "old", "expected_fingerprint": "changed"}, "供应商配置已变化"),
])
async def test_direct_api_cannot_bypass_preview_confirmation(preview_env, kwargs, message):
    with pytest.raises(model_config.ModelConfigConflict, match=message):
        await model_config.ModelConfigService().update_role("embedding", "new", "user:1", **kwargs)


@pytest.mark.asyncio
async def test_matching_preview_still_requires_explicit_rebuild_confirmation(preview_env):
    service = model_config.ModelConfigService()
    preview = await service.preview_embedding_change("new")
    with pytest.raises(model_config.ModelConfigConflict, match="需确认"):
        await service.update_role("embedding", "new", "user:1",
                                  expected_model="old", expected_fingerprint=preview["fingerprint"])


def test_registered_model_snapshot_never_rebinds_global_role(monkeypatch):
    from backend.infra.llm import registry_store

    snapshot = registry_store.RegistrySnapshot(
        providers=[{"id": "p", "base_url": "https://example.org/v1"}],
        models=[{"name": "new", "provider": "p", "model_kind": "embedding"}],
        roles={"embedding": "old"}, loaded=True,
    )
    monkeypatch.setattr(registry_store, "last_loaded_snapshot", lambda: snapshot)
    binding = specialized.registered_binding("embedding", "new")
    assert binding.model_name == "new"
    assert snapshot.roles == {"embedding": "old"}


@pytest.mark.asyncio
async def test_legacy_rerank_only_save_cannot_overwrite_active_embedding_provider(monkeypatch):
    monkeypatch.setattr(specialized, "resolve_binding", lambda role: SimpleNamespace(provider_id="shared"))
    monkeypatch.setattr(model_config, "get_session", lambda: pytest.fail("拒绝前不得写库"))
    with pytest.raises(model_config.ModelConfigConflict, match="正用于嵌入"):
        await model_config.ModelConfigService().configure_specialized({
            "provider": {"providerId": "shared", "displayName": "覆盖共享供应商",
                         "baseUrl": "https://example.org/v1"},
            "bindings": {"rerank": {"modelName": "reranker"}},
        }, "user:admin")


@pytest.mark.asyncio
async def test_provider_history_cannot_restore_local_billing_as_normal_admin(monkeypatch):
    class Result:
        def mappings(self):
            return self

        def first(self):
            return {"object_type": "provider", "object_key": "other-provider",
                    "old_value": '{"billing":"local"}', "rollbackable": True}

    class Session:
        calls = 0

        async def execute(self, *a, **k):
            self.calls += 1
            assert self.calls == 1, "权限拒绝前不能执行供应商更新"
            return Result()

    session = Session()

    async def sessions():
        yield session

    monkeypatch.setattr(model_config, "get_session", sessions)
    monkeypatch.setattr(specialized, "resolve_binding", lambda role: None)
    with pytest.raises(model_config.ModelConfigConflict, match="超级管理员"):
        await model_config.ModelConfigService().rollback(1, "user:admin")
