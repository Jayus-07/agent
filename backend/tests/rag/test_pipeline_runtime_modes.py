"""RAGPipeline runtime/evaluation 只读模式契约。"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document


def test_evaluation_mode_does_not_sync(monkeypatch):
    from backend.rag import pipeline as module

    calls: list[str] = []
    monkeypatch.setattr(module, "get_embedding", lambda: object())
    monkeypatch.setattr(
        module.RAGPipeline,
        "_load_existing_db",
        lambda self, path, kind: object(),
    )
    monkeypatch.setattr(
        module.RAGPipeline,
        "_init_vector_dbs_incremental",
        lambda self: calls.append("sync"),
    )
    monkeypatch.setattr(module.RAGPipeline, "_init_retrievers", lambda self: None)

    module.RAGPipeline(mode="evaluation")

    assert calls == []


def test_runtime_mode_never_builds_bm25(monkeypatch):
    from backend.rag import pipeline as module

    pipeline = module.RAGPipeline.__new__(module.RAGPipeline)
    pipeline.mode = "runtime"
    pipeline.embedding = object()
    pipeline.vectordb = type(
        "Vector",
        (),
        {
            "get": lambda self: {
                "ids": ["c1"],
                "documents": ["text"],
                "metadatas": [{}],
            }
        },
    )()
    pipeline.doc_db = object()

    class ReadOnlyBM25Store:
        def load(self, k):
            return None

        def build(self, *args, **kwargs):
            raise AssertionError("runtime mode attempted BM25 write")

    monkeypatch.setattr(module, "BM25Store", ReadOnlyBM25Store)

    with pytest.raises(RuntimeError, match="BM25"):
        pipeline._init_retrievers()


@pytest.mark.parametrize("mode", ["runtime", "evaluation"])
def test_read_only_modes_reject_vector_sync(mode):
    from backend.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.mode = mode

    with pytest.raises(RuntimeError, match="禁止.*向量库"):
        pipeline._init_vector_dbs_incremental()
    with pytest.raises(RuntimeError, match="禁止.*向量库"):
        pipeline._init_vector_dbs_full()


def test_index_mode_can_skip_implicit_sync_for_explicit_import(monkeypatch):
    from backend.rag import pipeline as module

    calls: list[str] = []
    monkeypatch.setattr(module, "get_embedding", lambda: object())
    monkeypatch.setattr(
        module.RAGPipeline,
        "_load_existing_db",
        lambda self, path, kind: object(),
    )
    monkeypatch.setattr(
        module.RAGPipeline,
        "_init_vector_dbs_incremental",
        lambda self: calls.append("sync"),
    )
    monkeypatch.setattr(module.RAGPipeline, "_init_retrievers", lambda self: None)

    module.RAGPipeline(mode="index", auto_sync=False)

    assert calls == []


def test_unordered_bm25_hash_accepts_vector_store_return_order():
    from backend.rag.retrieval.bm25_store import compute_content_hash_unordered

    docs = [
        Document(page_content="a", metadata={"source_file": "one.md"}),
        Document(page_content="b", metadata={"source_file": "two.md"}),
    ]

    assert compute_content_hash_unordered(docs) == compute_content_hash_unordered(
        list(reversed(docs))
    )
