"""文档列表使用实际专项 Embedding 模型的回归测试。"""
from __future__ import annotations

from backend.app.api.routes import rag_documents
from backend.infra.llm import specialized


def test_embedding_model_name_prefers_specialized_runtime_binding(monkeypatch):
    specialized.set_bindings(
        {
            "embedding": {
                "provider_id": "specialized-api",
                "adapter": "openai_embedding",
                "model_name": "qwen3.7-text-embedding",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "enabled": True,
            }
        }
    )
    monkeypatch.setattr(rag_documents, "ENV_MODE", "cloud")

    try:
        assert rag_documents._embedding_model_name() == "qwen3.7-text-embedding"
    finally:
        specialized.reset_for_tests()
