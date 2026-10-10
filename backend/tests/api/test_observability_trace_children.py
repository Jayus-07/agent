"""远端子 Trace 即使未由父请求回传 ID，也应能从现有 parent_id 关系发现。"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager

from backend.app.api.routes import observability
from backend.observability.trace_store_pg import PostgresTraceStore


def test_trace_detail_discovers_persisted_child_ids(monkeypatch):
    parent = {
        "id": "agent-parent",
        "children_ids": ["already-linked"],
        "spans": [],
    }

    class Store:
        def get(self, _trace_id):
            return None

        def list_children(self, parent_id, limit=50):
            assert parent_id == "agent-parent"
            return [
                {"id": "rag-child", "parent_id": parent_id},
                {"id": "already-linked", "parent_id": parent_id},
                {"trace_id": "sql-child", "parent_id": parent_id},
            ]

    monkeypatch.setattr(observability.trace_collector, "get", lambda _trace_id: parent)
    monkeypatch.setattr(observability, "get_trace_store", lambda: Store())
    monkeypatch.setattr(observability, "_backfill_usage", lambda _data: None)
    monkeypatch.setattr(observability, "_to_trace_dto", lambda data: data)

    result = asyncio.run(observability.get_trace("agent-parent"))

    assert result["children_ids"] == ["already-linked", "rag-child", "sql-child"]


def test_trace_store_queries_children_using_existing_parent_id_column(monkeypatch):
    store = PostgresTraceStore.__new__(PostgresTraceStore)
    store._lock = threading.Lock()
    store._table = "trace_store"
    captured = {}

    class Cursor:
        def fetchall(self):
            return [
                {"data": json.dumps({"id": "rag-child", "parent_id": "agent-parent"})}
            ]

    @contextmanager
    def connection():
        yield object()

    def execute(_conn, query, params):
        captured["query"] = query
        captured["params"] = params
        return Cursor()

    monkeypatch.setattr(store, "_conn", connection)
    monkeypatch.setattr(store, "_exec", execute)

    rows = store.list_children("agent-parent", limit=17)

    assert rows == [{"id": "rag-child", "parent_id": "agent-parent"}]
    assert "WHERE parent_id = %s" in captured["query"]
    assert captured["params"] == ("agent-parent", 17)
