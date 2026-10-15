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
