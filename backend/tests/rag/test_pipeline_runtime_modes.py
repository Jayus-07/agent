"""RAGPipeline runtime/evaluation 只读模式契约。"""

from __future__ import annotations

import pytest


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
