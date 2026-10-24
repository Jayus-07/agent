import pytest

from backend.config import database
from backend.evaluation.corpus_snapshots import capture
from backend.rag.indexing import chunk_store, doc_registry


def test_empty_kb_rejected_before_embedding_initialization(monkeypatch):
    shared_config = {"host": "isolated-test"}
    monkeypatch.setattr(database, "DOC_REGISTRY_PG_CONFIG", shared_config)
    monkeypatch.setattr(database, "RAG_STORES_PG_CONFIG", shared_config)
    monkeypatch.setattr(database, "VECTOR_PG_CONFIG", shared_config)

    class EmptyRegistry:
        def list_by_kb(self, kb_id):
            assert kb_id == "empty-kb"
            return []

    def unexpected_initialization(*args, **kwargs):
        pytest.fail("空知识库预检不应初始化 Chunk Store 或 Embedding")

    monkeypatch.setattr(doc_registry, "DocumentRegistry", EmptyRegistry)
    monkeypatch.setattr(chunk_store, "get_chunk_store", unexpected_initialization)
    monkeypatch.setattr("backend.rag.embedding_singleton.get_embedding", unexpected_initialization)

    with pytest.raises(ValueError, match="知识库没有已发布文档"):
        capture("empty-kb", "test-actor")
