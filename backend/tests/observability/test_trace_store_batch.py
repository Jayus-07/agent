"""TraceStore 完整 Trace 批量读取回归。"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager

from backend.observability.trace_store_pg import PostgresTraceStore


def test_list_with_spans_uses_one_bounded_query(monkeypatch):
    store = PostgresTraceStore.__new__(PostgresTraceStore)
    store._lock = threading.Lock()
    store._table = "test_trace_store"
    trace = {"id": "trace-1", "spans": [{"id": "span-1", "type": "tool_call"}]}
    statements = []

    @contextmanager
    def connection():
        yield object()

    class Result:
        def fetchall(self):
            return [{"data": json.dumps(trace)}]

    def execute(_connection, sql, params):
        statements.append((sql, params))
        return Result()

    monkeypatch.setattr(store, "_conn", connection)
    monkeypatch.setattr(store, "_exec", execute)

    rows = store.list_with_spans(37)

    assert rows == [trace]
    assert len(statements) == 1
    assert "ORDER BY created_at DESC LIMIT %s" in statements[0][0]
    assert statements[0][1][1] == 37


def test_latest_trace_ids_by_upload_is_tenant_scoped(monkeypatch):
    store = PostgresTraceStore.__new__(PostgresTraceStore)
    store._lock = threading.Lock()
    store._table = "test_trace_store"
    statements = []

    @contextmanager
    def connection():
        yield object()

    class Result:
        def fetchall(self):
            return [
                {"trace_id": "trace-new", "upload_id": "upload-1"},
                {"trace_id": "trace-old", "upload_id": "upload-1"},
                {"trace_id": "trace-2", "upload_id": "upload-2"},
            ]

    def execute(_connection, sql, params):
        statements.append((sql, params))
        return Result()

    monkeypatch.setattr(store, "_conn", connection)
    monkeypatch.setattr(store, "_exec", execute)

    assert store.latest_trace_ids_by_upload(
        ["upload-1", "upload-2", "upload-1"], "tenant-a",
    ) == {"upload-1": "trace-new", "upload-2": "trace-2"}
    assert len(statements) == 1
    sql, params = statements[0]
    assert "workflow_name = %s" in sql
    assert "{tags,tenant_id}" in sql
    assert "{tags,upload_id}" in sql
    assert params == ("knowledge_index", "tenant-a", ["upload-1", "upload-2"])


def test_latest_trace_ids_by_upload_needs_tenant_scope(monkeypatch):
    store = PostgresTraceStore.__new__(PostgresTraceStore)
    store._lock = threading.Lock()
    store._table = "test_trace_store"
    monkeypatch.setattr(
        store, "_conn",
        lambda: (_ for _ in ()).throw(AssertionError("must not query without tenant")),
    )

    assert store.latest_trace_ids_by_upload(["upload-1"], "") == {}
