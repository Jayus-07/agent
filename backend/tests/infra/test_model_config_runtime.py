"""模型角色 DB 覆盖必须真正影响聊天热路径。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.config import model_roles
from backend.infra.llm import models
from backend.infra.llm import proxy
from backend.infra.llm import registry_store
from backend.infra.llm import specialized
from backend.infra.llm.credentials import ProviderCredentials
from backend.services.model_config import _model_validation_issue


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    old_default = proxy._default_llm
    old_model = getattr(proxy, "_default_llm_model", None)
    model_roles.reset_overrides()
    models.reset_dynamic_models_for_tests()
    specialized.reset_for_tests()
    old_fallback = proxy._fallback_llm
    old_overrides = dict(proxy._override_llm_cache)
    proxy._default_llm = None
    if hasattr(proxy, "_default_llm_model"):
        proxy._default_llm_model = None
    proxy._fallback_llm = None
    proxy._override_llm_cache.clear()
    yield
    model_roles.reset_overrides()
    models.reset_dynamic_models_for_tests()
    specialized.reset_for_tests()
    proxy._default_llm = old_default
    if hasattr(proxy, "_default_llm_model"):
        proxy._default_llm_model = old_model
    proxy._fallback_llm = old_fallback
    proxy._override_llm_cache.clear()
    proxy._override_llm_cache.update(old_overrides)


def test_db_main_role_bypasses_stale_factory(monkeypatch):
    target = "deepseek-v4-flash"
    model_roles.inject_overrides({"main": target})
    built: list[str] = []

    monkeypatch.setattr(proxy, "_build_llm_for", lambda name: built.append(name) or object())
    monkeypatch.setattr(proxy, "get_llm_factory", lambda: None)

    assert proxy.get_active_model_name() == target
    assert proxy._resolve_active_llm() is proxy._default_llm
    assert built == [target]


def test_dynamic_model_provider_is_used_by_role_snapshot():
    models.set_dynamic_models(
        [{"name": "glm-4.6", "provider": "glm-coding", "source": "db"}]
    )

    assert model_roles.provider_of("glm-4.6") == "glm-coding"
    assert models.is_registered_model("glm-4.6") is True


def test_db_role_override_wins_and_legacy_module_value_is_ignored():
    """DB 角色覆盖必须优先，且历史模块值不能重新引入 env 兜底。"""
    model_roles.inject_overrides({"doc": "db-doc-model"})

    assert model_roles.resolve_runtime_name("doc", "legacy-doc-model") == "db-doc-model"

    model_roles.reset_overrides()
    assert model_roles.resolve_runtime_name("doc", "legacy-doc-model") == (
        model_roles.resolve_effective("doc")["value"]
    )
    assert model_roles.resolve_runtime_name("doc", "legacy-doc-model") != "legacy-doc-model"


def test_non_index_role_consumers_read_db_overrides():
    """非索引角色的历史消费者都必须读取同一份 DB 覆盖。"""
    model_roles.inject_overrides({
        "tool_selector": "tool-db",
        "ocr": "ocr-db",
        "rerank": "rerank-db",
        "eval_gen": "eval-db",
    })

    from backend.evaluation import generation
    from backend.orchestration.graph import tool_selector
    from backend.rag import reranker
    from backend.rag.preprocessing.parser import ocr

    assert tool_selector._configured_tool_selector_model() == "tool-db"
    assert ocr._configured_ocr_model() == "ocr-db"
    assert reranker._configured_rerank_model() == "rerank-db"
    assert generation._configured_eval_model() == "eval-db"


def test_specialized_role_requires_registered_model(monkeypatch):
    """专项角色也必须从已登记目录选择，不能保存自由文本模型名。"""
    models.set_dynamic_models([
        {"name": "BAAI/bge-m3", "provider": "siliconflow", "model_kind": "embedding"},
        {"name": "qwen-vl-max", "provider": "qwen", "model_kind": "chat"},
    ])
    monkeypatch.setattr(
        "backend.infra.llm.credentials.resolve_credentials",
        lambda provider_id, model_name=None: ProviderCredentials(
            provider=provider_id,
            api_key="db-test-placeholder",
            base_url="https://example.invalid/v1",
            source="db",
            version=1,
        ),
    )

    assert _model_validation_issue("BAAI/bge-m3", role="embedding") is None
    assert _model_validation_issue("qwen-vl-max", role="ocr") is None
    assert _model_validation_issue("no-such-model", role="embedding") is not None
    assert _model_validation_issue("no-such-model", role="ocr") is not None


def test_registered_model_kind_cannot_be_bound_to_wrong_specialized_role():
    models.set_dynamic_models([
        {
            "name": "registered-embedding",
            "provider": "custom-api",
            "model_kind": "embedding",
        }
    ])

    issue = _model_validation_issue("registered-embedding", role="main")
    assert issue is not None
    assert "向量模型" in issue


def test_registry_refresh_invalidates_cached_instances_after_config_change(monkeypatch):
    """供应商/密钥变更后，旧 LLM 实例不能继续留在任何代理缓存里。"""
    proxy._default_llm = object()
    proxy._default_llm_model = "deepseek-v4-flash"
    proxy._fallback_llm = object()
    proxy._override_llm_cache["deepseek-v4-flash"] = object()

    monkeypatch.setattr(
        registry_store,
        "load_registry",
        AsyncMock(
            return_value=registry_store.RegistrySnapshot(loaded=True)
        ),
    )

    assert asyncio.run(registry_store.refresh_registry()) is True
    assert proxy._default_llm is None
    assert proxy._default_llm_model is None
    assert proxy._fallback_llm is None
    assert proxy._override_llm_cache == {}


def test_registry_refresh_does_not_invalidate_unchanged_snapshot(monkeypatch):
    """15 秒轮询的同一快照不能反复重建 LLM 客户端。"""
    snapshot = registry_store.RegistrySnapshot(loaded=True)
    monkeypatch.setattr(
        registry_store,
        "load_registry",
        AsyncMock(return_value=snapshot),
    )
    invalidate = Mock()
    monkeypatch.setattr(proxy, "invalidate_runtime_caches", invalidate)
    monkeypatch.setattr(registry_store, "_last_runtime_signature", None)

    assert asyncio.run(registry_store.refresh_registry()) is True
    assert asyncio.run(registry_store.refresh_registry()) is True
    assert invalidate.call_count == 1


def test_embedding_runtime_uses_specialized_provider_binding(monkeypatch):
    from backend.rag import embedding_singleton

    specialized.set_bindings(
        {
            "embedding": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_embedding",
                "model_name": "qwen3.7-text-embedding",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "options": {"dimensions": 1024},
            }
        }
    )
    monkeypatch.setattr(
        embedding_singleton.credentials_mod,
        "resolve_credentials",
        lambda provider_id, model_name=None: ProviderCredentials(
            provider=provider_id,
            api_key="sk-test-placeholder",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            source="db",
            version=2,
        ),
    )

    config = embedding_singleton._resolve_cloud_embedding_config()

    assert config["model"] == "qwen3.7-text-embedding"
    assert config["api_key"] == "sk-test-placeholder"
    assert config["base_url"].endswith("compatible-mode/v1")
    assert config["dimensions"] == 1024


def test_rerank_runtime_uses_specialized_adapter_binding(monkeypatch):
    from backend.rag import reranker

    specialized.set_bindings(
        {
            "rerank": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_rerank",
                "model_name": "qwen3.7-text-rerank",
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "options": {},
            }
        }
    )
    monkeypatch.setattr(
        reranker.credentials_mod,
        "resolve_credentials",
        lambda provider_id, model_name=None: ProviderCredentials(
            provider=provider_id,
            api_key="sk-test-placeholder",
            base_url="https://dashscope.aliyuncs.com/api/v1",
            source="db",
            version=2,
        ),
    )

    config = reranker._resolve_rerank_runtime_config()

    assert config["model"] == "qwen3.7-text-rerank"
    assert config["api_key"] == "sk-test-placeholder"
    assert config["api_format"] == "dashscope"
    assert config["base_url"].endswith("api/v1")


def test_existing_embedding_wrapper_hot_swaps_after_registry_change(monkeypatch):
    """RAGPipeline 已持有 wrapper 时，管理端改 Key/模型也要切换出站客户端。"""
    from backend.rag import embedding_singleton

    class FakeEmbedding:
        def __init__(self, label: str):
            self.label = label

        def embed_query(self, text: str):
            return [self.label, text]

        def embed_documents(self, texts):
            return [[self.label, text] for text in texts]

    specialized.set_bindings(
        {
            "embedding": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_embedding",
                "model_name": "model-before",
                "base_url": "https://example.com/v1",
                "options": {},
            }
        }
    )
    current = {"value": FakeEmbedding("before")}
    monkeypatch.setattr(
        embedding_singleton,
        "_get_cloud_embedding",
        lambda: current["value"],
    )

    wrapper = embedding_singleton._TrackedEmbedding(current["value"], tracker=None)
    assert wrapper.embed_query("q") == ["before", "q"]

    current["value"] = FakeEmbedding("after")
    specialized.set_bindings(
        {
            "embedding": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_embedding",
                "model_name": "model-after",
                "base_url": "https://example.com/v1",
                "options": {},
            }
        }
    )

    assert wrapper.embed_query("q") == ["after", "q"]


def test_existing_rerank_wrapper_hot_swaps_after_registry_change(monkeypatch):
    """RAGPipeline 已编译时，重排绑定更新应在下一次请求替换 backend。"""
    from backend.rag import reranker

    class FakeReranker:
        pass

    created: list[FakeReranker] = []

    def build_backend():
        backend = FakeReranker()
        created.append(backend)
        return backend

    monkeypatch.setattr(reranker, "get_reranker_backend", build_backend)
    specialized.set_bindings(
        {
            "rerank": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_rerank",
                "model_name": "rerank-before",
                "base_url": "https://example.com/api/v1",
                "options": {},
            }
        }
    )
    wrapper = reranker.RerankCompressor()
    wrapper._ensure_backend()
    first = wrapper.backend

    specialized.set_bindings(
        {
            "rerank": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_rerank",
                "model_name": "rerank-after",
                "base_url": "https://example.com/api/v1",
                "options": {},
            }
        }
    )
    wrapper._ensure_backend()

    assert first is not wrapper.backend
    assert len(created) == 2
