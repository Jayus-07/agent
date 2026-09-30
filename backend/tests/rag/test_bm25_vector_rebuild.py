"""BM25 必须从当前向量集合重建，并能排除被替换的旧 chunk。"""

import json

import pytest
from langchain_core.documents import Document

from backend.rag.retrieval.bm25_store import BM25Store


class _FakeVectorStore:
    _collection = "chroma"

    def __init__(self, rows):
        self._rows = rows

    def get(self):
        return {
            "ids": [row[0] for row in self._rows],
            "documents": [row[1] for row in self._rows],
            "metadatas": [row[2] for row in self._rows],
        }


def _doc(vector_id: str, text: str) -> Document:
    return Document(
        page_content=text,
        metadata={"vector_id": vector_id, "doc_id": vector_id.split(":")[0]},
    )


def test_rebuild_from_vectorstore_replaces_stale_bm25_documents(tmp_path):
    store = BM25Store(tmp_path / "bm25")
    store.build([_doc("stale:0", "旧文档")])
    vector_store = _FakeVectorStore([
        ("new:0", "新的文档内容", {"doc_id": "new", "chunk_id": "new:0"}),
        ("new:1", "新的第二段内容", {"doc_id": "new", "chunk_id": "new:1"}),
    ])

    store.rebuild_from_vectorstore(vector_store)

    assert {doc.metadata["vector_id"] for doc in store.load_docs()} == {
        "new:0",
        "new:1",
    }
    meta = json.loads((tmp_path / "bm25" / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "vectorstore"
    assert meta["collection"] == "chroma"
    assert meta["vector_count"] == 2


def test_rebuild_from_vectorstore_excludes_superseded_chunks(tmp_path):
    store = BM25Store(tmp_path / "bm25")
    vector_store = _FakeVectorStore([
        ("doc:old", "旧版本", {"doc_id": "doc", "chunk_id": "doc:old"}),
        ("doc:new", "新版本", {"doc_id": "doc", "chunk_id": "doc:new"}),
        ("other:0", "其他文档", {"doc_id": "other", "chunk_id": "other:0"}),
    ])

    store.rebuild_from_vectorstore(vector_store, exclude_ids={"doc:old"})

    assert {doc.metadata["vector_id"] for doc in store.load_docs()} == {
        "doc:new",
        "other:0",
    }


def test_failed_build_keeps_previous_snapshot(tmp_path, monkeypatch):
    store = BM25Store(tmp_path / "bm25")
    store.build([_doc("stable:0", "稳定版本")])

    def fail_build(*args, **kwargs):
        raise RuntimeError("tokenizer unavailable")

    monkeypatch.setattr(
        "backend.rag.retrieval.bm25_store.BM25Retriever.from_documents",
        fail_build,
    )
    with pytest.raises(RuntimeError, match="tokenizer unavailable"):
        store.build([_doc("broken:0", "新版本")])

    assert [doc.metadata["vector_id"] for doc in store.load_docs()] == [
        "stable:0"
    ]
