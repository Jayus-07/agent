"""RAG 重复上传的已发布产物完整性核对。"""

import hashlib

from backend.app.api.routes import rag_upload


class _ChunkStore:
    def __init__(self, rows):
        self.rows = rows

    def get_by_doc_id(self, doc_id):
        return list(self.rows)


def test_same_hash_is_not_duplicate_when_registered_vector_is_missing(
    tmp_path, monkeypatch,
):
    source = tmp_path / "source.txt"
    source.write_text("测试原文", encoding="utf-8")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    doc = {
        "doc_id": "doc-1",
        "chunk_count": 2,
        "chunk_ids": '["vector-1", "vector-2"]',
    }
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store",
        lambda: _ChunkStore([{}, {}]),
    )
    monkeypatch.setattr(
        rag_upload, "_active_vector_ids_for_doc",
        lambda doc_id, expected_ids: {"vector-1"},
    )

    assert not rag_upload._active_document_artifacts_complete(
        doc, str(source), expected_hash,
    )


def test_same_hash_is_duplicate_when_registered_vectors_are_present(
    tmp_path, monkeypatch,
):
    source = tmp_path / "source.txt"
    source.write_text("测试原文", encoding="utf-8")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    doc = {
        "doc_id": "doc-1",
        "chunk_count": 2,
        "chunk_ids": '["vector-1", "vector-2"]',
    }
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store",
        lambda: _ChunkStore([{}, {}]),
    )
    monkeypatch.setattr(
        rag_upload, "_active_vector_ids_for_doc",
        lambda doc_id, expected_ids: {"vector-1", "vector-2"},
    )

    assert rag_upload._active_document_artifacts_complete(
        doc, str(source), expected_hash,
    )
