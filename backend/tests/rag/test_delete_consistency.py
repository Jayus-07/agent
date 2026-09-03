"""WS5: Delete consistency — verify purge completeness."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document


class TestVerifyDocPurged:
    def test_clean_delete_no_residue(self):
        """All stores return empty → no residue."""
        from backend.app.api.routes.rag_documents import _verify_doc_purged

        mock_pipeline = MagicMock()
        mock_pipeline.vectordb = MagicMock()
        mock_pipeline.vectordb.get.return_value = {"ids": [], "documents": []}
        mock_pipeline.doc_db = MagicMock()
        mock_pipeline.doc_db.get.return_value = {"ids": [], "documents": []}
        mock_pipeline.bm25_store = MagicMock()
        mock_pipeline.bm25_store.load_docs.return_value = []

        with patch("backend.rag.indexing.chunk_store.get_chunk_store") as mock_cs:
            mock_store = MagicMock()
            mock_store.count_by_doc_id.return_value = 0
            mock_cs.return_value = mock_store

            residue = _verify_doc_purged("doc1", "/path/file.txt", mock_pipeline)
            assert residue == []

    def test_chroma_residue_detected(self):
        """Chroma still has chunks → residue reported."""
        from backend.app.api.routes.rag_documents import _verify_doc_purged

        mock_pipeline = MagicMock()
        mock_pipeline.vectordb = MagicMock()
        mock_pipeline.vectordb.get.return_value = {"ids": ["c1"], "documents": ["text"]}
        mock_pipeline.doc_db = MagicMock()
        mock_pipeline.doc_db.get.return_value = {"ids": [], "documents": []}
        mock_pipeline.bm25_store = MagicMock()
        mock_pipeline.bm25_store.load_docs.return_value = []

        with patch("backend.rag.indexing.chunk_store.get_chunk_store") as mock_cs:
            mock_store = MagicMock()
            mock_store.count_by_doc_id.return_value = 0
            mock_cs.return_value = mock_store

            residue = _verify_doc_purged("doc1", "/path/file.txt", mock_pipeline)
            assert len(residue) > 0
            assert any("chroma" in r.lower() or "chunk" in r.lower() for r in residue)

    def test_bm25_residue_detected(self):
        """BM25 still has entries → residue reported."""
        from backend.app.api.routes.rag_documents import _verify_doc_purged

        mock_pipeline = MagicMock()
        mock_pipeline.vectordb = MagicMock()
        mock_pipeline.vectordb.get.return_value = {"ids": [], "documents": []}
        mock_pipeline.doc_db = MagicMock()
        mock_pipeline.doc_db.get.return_value = {"ids": [], "documents": []}
        mock_pipeline.bm25_store = MagicMock()
        mock_pipeline.bm25_store.load_docs.return_value = [
            Document("stale", metadata={"doc_id": "doc1"}),
        ]

        with patch("backend.rag.indexing.chunk_store.get_chunk_store") as mock_cs:
            mock_store = MagicMock()
            mock_store.count_by_doc_id.return_value = 0
            mock_cs.return_value = mock_store

            residue = _verify_doc_purged("doc1", "/path/file.txt", mock_pipeline)
            assert len(residue) > 0
            assert any("bm25" in r.lower() for r in residue)
