"""远端子 Trace 即使未由父请求回传 ID，也应能从现有 parent_id 关系发现。

2026-10-10 管理端/API 主题后，get_trace 与 list_children 均带资源级授权
与租户作用域：本文件的打桩点同步迁移（身份来自网关验签注入的
resolve_identity；store 子查询必须声明 tenant_id）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

from backend.app.api.routes import observability
from backend.observability.trace_store_pg import PostgresTraceStore

_TENANT = "tenant-a"


def _identity_stub(_request):
    return SimpleNamespace(user_id="u-1", tenant_id=_TENANT)


def test_trace_detail_discovers_persisted_child_ids(monkeypatch):
    parent = {
        "id": "agent-parent",
        "children_ids": ["already-linked"],
        "spans": [],
    }

    class Store:
        def get(self, _trace_id):
            return None

        def list_children(self, parent_id, limit=50, *, tenant_id=None):
            assert parent_id == "agent-parent"
            assert tenant_id == _TENANT
            return [
                {"id": "rag-child", "parent_id": parent_id},
                {"id": "already-linked", "parent_id": parent_id},
                {"trace_id": "sql-child", "parent_id": parent_id},
            ]

    monkeypatch.setattr(observability.trace_collector, "get", lambda _trace_id: parent)
    monkeypatch.setattr(observability, "get_trace_store", lambda: Store())
    monkeypatch.setattr(observability, "_backfill_usage", lambda _data: None)
    monkeypatch.setattr(observability, "_to_trace_dto", lambda data: data)
    # 身份与资源级授权打桩：本用例只验证「parent_id 关系发现」，授权规则
    # 由 _trace_authz 自身的测试覆盖。
    monkeypatch.setattr("backend.app.api.identity.resolve_identity", _identity_stub)
    monkeypatch.setattr(
        "backend.app.api.routes._trace_authz.trace_visible_to",
        lambda *_args, **_kwargs: True,
    )

    result = asyncio.run(observability.get_trace(
        "agent-parent",
        request=SimpleNamespace(),
        _reader=SimpleNamespace(role="admin"),
    ))

    assert result["children_ids"] == ["already-linked", "rag-child", "sql-child"]


def test_trace_store_queries_children_using_existing_parent_id_column(monkeypatch):
    store = PostgresTraceStore.__new__(PostgresTraceStore)
    store._lock = threading.Lock()
    store._table = "trace_store"
    captured = {}

    class Cursor:
        def fetchall(self):
            return [
                {"data": json.dumps({
                    "id": "rag-child", "parent_id": "agent-parent",
                    "tags": {"tenant_id": _TENANT},
                })},
                # 其他租户的子 Trace 一律不可见
                {"data": json.dumps({
                    "id": "foreign-child", "parent_id": "agent-parent",
                    "tags": {"tenant_id": "tenant-b"},
                })},
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

    rows = store.list_children("agent-parent", limit=17, tenant_id=_TENANT)

    assert rows == [{"id": "rag-child", "parent_id": "agent-parent",
                     "tags": {"tenant_id": _TENANT}}]
    assert "WHERE parent_id = %s" in captured["query"]
    assert captured["params"] == ("agent-parent", 17)
