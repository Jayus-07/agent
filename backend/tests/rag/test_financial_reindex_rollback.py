"""财务文档重解析失败时，旧版最新标记必须按向量 ID 精确恢复。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from threading import Lock
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore


class _Registry:
    def __init__(self, row: dict):
        self.row = dict(row)
        self.deleted = False

    def get_by_path(self, _path: str) -> dict:
        return self.row

    def update_status(self, _path: str, status: str) -> None:
        self.row["status"] = status

    def update_fields(self, _path: str, fields: dict) -> None:
        self.row.update(fields)

    def mark_deleted_by_doc_id(self, _doc_id: str) -> None:
        self.deleted = True


class _VectorStore:
    def __init__(self, latest: dict[str, bool]):
        self.latest = dict(latest)
        self.calls: list[tuple[list[str], dict]] = []

    def update_metadata_by_ids(self, ids: list[str], metadata_update: dict) -> int:
        self.calls.append((list(ids), dict(metadata_update)))
        for vector_id in ids:
            if vector_id in self.latest:
                self.latest[vector_id] = metadata_update["is_latest"]
        return sum(vector_id in self.latest for vector_id in ids)


def _reindexer(*, row, vector_store):
    indexer = object.__new__(IncrementalIndexer)
    indexer.registry = _Registry(row)
    indexer.vectordb = vector_store
    indexer.doc_db = MagicMock()
    return indexer


def test_failed_financial_reindex_restores_only_current_vectors(tmp_path, monkeypatch):
    source = tmp_path / "2026-Q3-report.md"
    source.write_text("财务报告原文", encoding="utf-8")
    row = {
        "file_path": str(source),
        "doc_id": "doc-financial",
        "doc_type": "financial",
        "chunk_ids": json.dumps(["current-1", "current-2"]),
        "doc_db_id": "doc-vector-current",
        "status": "active",
        "file_hash": "previous-hash",
    }
    vectors = _VectorStore({
        "current-1": True,
        "current-2": True,
        "history-1": False,
    })
    indexer = _reindexer(row=row, vector_store=vectors)
    indexer._index_file = MagicMock(side_effect=RuntimeError("embedding failed"))
    monkeypatch.setattr(
        IncrementalIndexer,
        "_should_use_version_snapshot",
        staticmethod(lambda _doc_type, _path: True),
    )

    with pytest.raises(RuntimeError, match="embedding failed"):
        indexer.reindex_file(str(source))

    assert vectors.latest == {
        "current-1": True,
        "current-2": True,
        "history-1": False,
    }
    assert vectors.calls == [
        (["current-1", "current-2"], {"is_latest": False}),
        (["current-1", "current-2"], {"is_latest": True}),
    ]
    assert indexer.registry.row["status"] == "active"
    assert indexer.registry.row["file_hash"] == "previous-hash"
    assert not indexer.registry.deleted


def test_financial_reindex_without_old_chunk_ids_fails_before_mutation(
    tmp_path, monkeypatch,
):
    source = tmp_path / "2026-Q3-report.md"
    source.write_text("财务报告原文", encoding="utf-8")
    row = {
        "file_path": str(source),
        "doc_id": "doc-financial",
        "doc_type": "financial",
        "chunk_ids": "[]",
        "doc_db_id": "doc-vector-current",
        "status": "active",
        "file_hash": "previous-hash",
    }
    vectors = _VectorStore({"history-1": False})
    indexer = _reindexer(row=row, vector_store=vectors)
    indexer._index_file = MagicMock()
    monkeypatch.setattr(
        IncrementalIndexer,
        "_should_use_version_snapshot",
        staticmethod(lambda _doc_type, _path: True),
    )

    with pytest.raises(ValueError, match="缺少当前分块 ID"):
        indexer.reindex_file(str(source))

    assert vectors.calls == []
    indexer._index_file.assert_not_called()
    assert not indexer.registry.deleted


def test_update_metadata_by_ids_scopes_to_collection_and_exact_ids():
    class _Cursor:
        def __init__(self):
            self.query = ""
            self.params = ()

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return [("current-1",), ("current-2",)]

    class _Connection:
        def __init__(self, cursor):
            self.cursor_instance = cursor

        def cursor(self):
            return self.cursor_instance

    cursor = _Cursor()
    store = object.__new__(PgVectorKnowledgeStore)
    store._table = "test_rag_vectors"
    store._collection = "test-collection"
    store._lock = Lock()

    @contextmanager
    def fake_connection():
        yield _Connection(cursor)

    store._conn = fake_connection

    updated = store.update_metadata_by_ids(
        ["current-1", "current-2"], {"is_latest": False},
    )

    assert updated == 2
    assert "collection = %s AND id = ANY(%s)" in cursor.query
    assert cursor.params == (
        '{"is_latest": false}',
        "test-collection",
        ["current-1", "current-2"],
    )
