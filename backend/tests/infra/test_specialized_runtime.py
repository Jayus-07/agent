"""专项 embedding/rerank 绑定的进程内运行时缓存契约。"""
from __future__ import annotations

from backend.infra.llm import specialized
from backend.infra.llm.registry_store import RegistrySnapshot


def test_specialized_binding_is_runtime_resolved_without_exposing_key() -> None:
    specialized.set_bindings(
        {
            "embedding": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_embedding",
                "model_name": "qwen3.7-text-embedding",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "options": {"dimensions": 1024},
                "enabled": True,
            }
        }
    )

    binding = specialized.resolve_binding("embedding")

    assert binding is not None
    assert binding.model_name == "qwen3.7-text-embedding"
    assert binding.provider_id == "dashscope-rag"
    assert "key" not in repr(binding).lower()


def test_disabled_or_unknown_specialized_binding_is_not_resolved() -> None:
    specialized.set_bindings(
        {
            "rerank": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_rerank",
                "model_name": "qwen3.7-text-rerank",
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "options": {},
                "enabled": False,
            }
        }
    )

    assert specialized.resolve_binding("rerank") is None
    assert specialized.resolve_binding("unknown") is None


def test_registry_snapshot_carries_specialized_bindings() -> None:
    snapshot = RegistrySnapshot(
        specialized={
            "rerank": {
                "provider_id": "dashscope-rag",
                "adapter": "dashscope_rerank",
                "model_name": "qwen3.7-text-rerank",
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "enabled": True,
            }
        }
    )

    assert snapshot.specialized["rerank"]["model_name"] == "qwen3.7-text-rerank"
