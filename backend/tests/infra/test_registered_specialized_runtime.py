"""供应商模型目录驱动专项运行时的回归测试。"""
from __future__ import annotations

import asyncio

from backend.infra.llm import registry_store
from backend.infra.llm import credentials as credentials_store
from backend.infra.llm.credentials import ProviderCredentials


def test_registry_refresh_derives_independent_specialized_bindings(monkeypatch):
    """向量和重排从模型目录解析各自 provider，不能共享一个旧专项凭据。"""
    snapshot = registry_store.RegistrySnapshot(
        providers=[
            {
                "id": "embedding-provider",
                "display_name": "向量供应商",
                "base_url": "https://embedding.example/v1",
                "enabled": True,
            },
            {
                "id": "rerank-provider",
                "display_name": "重排供应商",
                "base_url": "https://rerank.example/v1",
                "enabled": True,
            },
        ],
        models=[
            {
                "name": "embedding-model",
                "provider": "embedding-provider",
                "model_kind": "embedding",
                "source": "db",
            },
            {
                "name": "rerank-model",
                "provider": "rerank-provider",
                "model_kind": "rerank",
                "source": "db",
            },
        ],
        credentials={
            "embedding-provider": ProviderCredentials(
                provider="embedding-provider",
                api_key="embedding-key",
                base_url="https://embedding.example/v1",
                source="db",
                version=1,
            ),
            "rerank-provider": ProviderCredentials(
                provider="rerank-provider",
                api_key="rerank-key",
                base_url="https://rerank.example/v1",
                source="db",
                version=2,
            ),
        },
        roles={"embedding": "embedding-model", "rerank": "rerank-model"},
        specialized={},
        loaded=True,
    )
    captured: dict[str, dict] = {}
    monkeypatch.setattr(registry_store, "load_registry", _async_value(snapshot))
    monkeypatch.setattr(
        registry_store._specialized,
        "set_bindings",
        lambda bindings: captured.update(bindings),
    )
    monkeypatch.setattr(registry_store, "_last_runtime_signature", None)

    assert asyncio.run(registry_store.refresh_registry()) is True

    assert captured["embedding"]["provider_id"] == "embedding-provider"
    assert captured["rerank"]["provider_id"] == "rerank-provider"
    assert captured["embedding"]["model_name"] == "embedding-model"
    assert captured["rerank"]["model_name"] == "rerank-model"
    assert credentials_store.resolve_credentials("embedding-provider").api_key == "embedding-key"
    assert credentials_store.resolve_credentials("rerank-provider").api_key == "rerank-key"


def test_registry_refresh_preserves_specialized_endpoint_path(monkeypatch):
    """供应商根地址不能覆盖专项协议需要的兼容模式路径。"""
    snapshot = registry_store.RegistrySnapshot(
        providers=[
            {
                "id": "dashscope-provider",
                "display_name": "阿里云百炼",
                "base_url": "https://dashscope.aliyuncs.com",
                "enabled": True,
            },
        ],
        models=[
            {
                "name": "qwen3.7-text-embedding",
                "provider": "dashscope-provider",
                "model_kind": "embedding",
                "source": "db",
            },
        ],
        credentials={
            "dashscope-provider": ProviderCredentials(
                provider="dashscope-provider",
                api_key="embedding-key",
                base_url="https://dashscope.aliyuncs.com",
                source="db",
                version=1,
            ),
        },
        roles={"embedding": "qwen3.7-text-embedding"},
        specialized={
            "embedding": {
                "provider_id": "dashscope-provider",
                "adapter": "dashscope_embedding",
                "model_name": "qwen3.7-text-embedding",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "options": {"dimensions": 1024},
                "enabled": True,
            },
        },
        loaded=True,
    )
    captured: dict[str, dict] = {}
    monkeypatch.setattr(registry_store, "load_registry", _async_value(snapshot))
    monkeypatch.setattr(
        registry_store._specialized,
        "set_bindings",
        lambda bindings: captured.update(bindings),
    )
    monkeypatch.setattr(registry_store, "_last_runtime_signature", None)

    assert asyncio.run(registry_store.refresh_registry()) is True

    assert captured["embedding"]["base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert captured["embedding"]["adapter"] == "dashscope_embedding"
    assert captured["embedding"]["options"] == {"dimensions": 1024}


def _async_value(value):
    async def _load():
        return value

    return _load
