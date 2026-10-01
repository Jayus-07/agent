"""WS1-3: BM25 replace_documents, remove-before-add, count-level sync。"""
from __future__ import annotations

import pytest
from langchain_core.documents import Document
from pathlib import Path

from backend.rag.retrieval.bm25_store import BM25Store, _doc_matches, source_files_out_of_sync


class TestDocMatches:
    def test_match_by_doc_id(self):
        doc = Document(page_content="x", metadata={"doc_id": "abc"})
        assert _doc_matches(doc, {"abc"}, set())

    def test_basename_matching_abolished(self):
        """C 阶段收口：basename 匹配废除——跨 KB 同名文件会互删，
        身份只认 doc_id（传入 basenames 仅兼容旧签名，不再参与判定）。"""
        doc = Document(page_content="x", metadata={"source_file": "/path/to/file.txt"})
        assert not _doc_matches(doc, set(), {"file.txt"})
        doc2 = Document(page_content="x", metadata={"file_path": "/other/doc.pdf"})
        assert not _doc_matches(doc2, set(), {"doc.pdf"})

    def test_no_match(self):
        doc = Document(page_content="x", metadata={"doc_id": "abc", "source_file": "a.txt"})
        assert not _doc_matches(doc, {"xyz"}, {"b.txt"})


class TestSourceFilesOutOfSync:
    def test_same_counts(self):
        indexed = [
            Document("", metadata={"source_file": "a.txt"}),
            Document("", metadata={"source_file": "a.txt"}),
        ]
        current = [
            Document("", metadata={"source_file": "a.txt"}),
            Document("", metadata={"source_file": "a.txt"}),
        ]
        assert not source_files_out_of_sync(indexed, current)

    def test_different_counts(self):
        indexed = [
            Document("", metadata={"source_file": "a.txt"}),
            Document("", metadata={"source_file": "a.txt"}),
        ]
        current = [
            Document("", metadata={"source_file": "a.txt"}),
        ]
        assert source_files_out_of_sync(indexed, current)

    def test_different_files(self):
        """C 阶段：对账口径 = doc_id 计数；无 doc_id 的行归并为同一空键。"""
        indexed = [Document("", metadata={"source_file": "a.txt"})]
        current = [Document("", metadata={"source_file": "b.txt"})]
        # 两者都无 doc_id → doc_id 口径下相同（basename 差异不再参与对账）
        assert not source_files_out_of_sync(indexed, current)
        indexed_id = [Document("", metadata={"doc_id": "d1"})]
        current_id = [Document("", metadata={"doc_id": "d2"})]
        assert source_files_out_of_sync(indexed_id, current_id)


class TestReplaceDocuments:
    def test_replace_removes_old_chunks(self, tmp_path):
        store = BM25Store(tmp_path / "bm25")
        old_docs = [
            Document("old chunk 1", metadata={"doc_id": "d1", "source_file": "f.txt"}),
            Document("old chunk 2", metadata={"doc_id": "d1", "source_file": "f.txt"}),
        ]
        store.add_documents(old_docs, k=10)
        assert store.doc_count() == 2

        new_docs = [
            Document("new chunk", metadata={"doc_id": "d1", "source_file": "f.txt"}),
        ]
        store.replace_documents(new_docs, k=10, doc_id="d1", file_path="f.txt")
        assert store.doc_count() == 1

    def test_replace_preserves_other_docs(self, tmp_path):
        store = BM25Store(tmp_path / "bm25")
        store.add_documents([
            Document("other", metadata={"doc_id": "d2", "source_file": "g.txt"}),
        ], k=10)

        new_docs = [
            Document("new", metadata={"doc_id": "d1", "source_file": "f.txt"}),
        ]
        store.replace_documents(new_docs, k=10, doc_id="d1", file_path="f.txt")
        assert store.doc_count() == 2

    def test_load_docs(self, tmp_path):
        store = BM25Store(tmp_path / "bm25")
        store.add_documents([
            Document("text", metadata={"doc_id": "d1", "source_file": "f.txt"}),
        ], k=10)
        loaded = store.load_docs()
        assert len(loaded) == 1
        assert loaded[0].page_content == "text"
