"""WS4: Index consistency checker — orphan/missing detection, repair."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from backend.rag.indexing.consistency import (
    ConsistencyIssue,
    ConsistencyReport,
    IndexConsistencyChecker,
)


class TestConsistencyIssue:
    def test_dataclass(self):
        issue = ConsistencyIssue(
            severity="error",
            store="chroma",
            doc_id="abc",
            detail="orphan chunk",
        )
        assert issue.severity == "error"
        assert issue.store == "chroma"


class TestConsistencyReport:
    def test_consistent_when_no_issues(self):
        report = ConsistencyReport(issues=[])
        assert report.consistent
        assert report.error_count == 0
        assert report.warning_count == 0

    def test_inconsistent_with_issues(self):
        issues = [
            ConsistencyIssue("error", "chroma_chunk", "d1", "orphan"),
            ConsistencyIssue("warning", "bm25", "d2", "stale"),
        ]
        report = ConsistencyReport(issues=issues)
        assert not report.consistent
        assert report.error_count == 1
        assert report.warning_count == 1

    def test_summary(self):
        issues = [
            ConsistencyIssue("error", "chroma_chunk", "d1", "orphan"),
        ]
        report = ConsistencyReport(issues=issues)
        s = report.summary()
        assert "1" in s
        assert "issue" in s.lower() or "error" in s.lower()


class TestIndexConsistencyChecker:
    def test_check_with_mock_pipeline(self):
        """Smoke test: checker can run with a mock pipeline."""
        mock_pipeline = MagicMock()
        mock_pipeline.vectordb = MagicMock()
        mock_pipeline.vectordb.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mock_pipeline.doc_db = MagicMock()
        mock_pipeline.doc_db.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mock_pipeline.bm25_store = MagicMock()
        mock_pipeline.bm25_store.load_docs.return_value = []
        mock_pipeline.bm25_store.doc_count.return_value = 0

        mock_reg = MagicMock()
        mock_reg.list_active.return_value = []

        with patch("backend.rag.indexing.doc_registry.DocumentRegistry", return_value=mock_reg):
            with patch("backend.config.DOC_REGISTRY_PATH", ":memory:"):
                checker = IndexConsistencyChecker(mock_pipeline)
                checker.registry = mock_reg
                report = checker.check()
                assert isinstance(report, ConsistencyReport)
