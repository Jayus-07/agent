import asyncio
from contextlib import contextmanager
from threading import Lock
from types import SimpleNamespace

from backend.app.api.routes import rag_documents
from backend.app.api.routes.rag_documents import _active_upload_public_item
from backend.rag.indexing.index_run_store_pg import IndexRunStore


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.params = ()

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor


def test_list_active_runs_returns_only_non_terminal_runs(monkeypatch):
    rows = [{"upload_id": "upload-1", "status": "indexing"}]
    cursor = _Cursor(rows)
    connection = _Connection(cursor)
    store = object.__new__(IndexRunStore)
    store._table = "test_rag_index_runs"
    store._lock = Lock()

    @contextmanager
    def fake_conn():
        yield connection

    monkeypatch.setattr(store, "_conn", fake_conn)

    assert store.list_active_runs(
        limit=900,
        tenant_id="tenant-1",
        kb_ids=["kb-1"],
        department="general",
    ) == rows
    assert "status IN ('claimed', 'indexing', 'publishing')" in cursor.query
    assert "tenant_id = %s" in cursor.query
    assert "kb_id = ANY(%s)" in cursor.query
    assert "department = %s" in cursor.query
    assert "ORDER BY created_at DESC" in cursor.query
    assert cursor.params == ("tenant-1", ["kb-1"], "general", 500)


def test_active_upload_projection_checks_manage_scope_and_hides_file_path():
    authz = SimpleNamespace(can_manage_row=lambda row: (row["kb_id"] == "kb-1", ""))
    run = {
        "upload_id": "upload-1",
        "celery_task_id": "celery-1",
        "file_path": "/srv/docs/faq_only.docx",
        "kb_id": "kb-1",
        "department": "general",
        "status": "indexing",
        "stage": "metadata",
        "created_at": "2026-10-19 01:00:00",
    }

    item = _active_upload_public_item(run, authz)

    assert item["doc_name"] == "faq_only.docx"
    assert item["upload_id"] == "upload-1"
    assert item["celery_task_id"] == "celery-1"
    assert "file_path" not in item
    assert _active_upload_public_item({**run, "kb_id": "kb-2"}, authz) is None


def test_list_operations_scopes_admin_active_runs_to_own_tenant(monkeypatch):
    authz = SimpleNamespace(
        is_admin=True,
        principal=SimpleNamespace(tenant_id="tenant-a", user_id="admin-a"),
        kb_scope=frozenset(),
        can_manage_row=lambda _row: (True, ""),
    )
    monkeypatch.setattr(rag_documents, "_require_authz", lambda _request: authz)

    logger_args = {}

    def list_operations(**kwargs):
        logger_args.update(kwargs)
        return {"items": [], "total": 0}

    monkeypatch.setattr(
        rag_documents,
        "_get_op_logger",
        lambda: SimpleNamespace(list=list_operations),
    )
    monkeypatch.setattr(
        rag_documents,
        "_get_registry",
        lambda: SimpleNamespace(_table="doc_registry"),
    )

    captured = {}

    class _RunStore:
        def list_active_runs(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(
        "backend.rag.indexing.index_run_store_pg.get_index_run_store",
        lambda: _RunStore(),
    )

    response = asyncio.run(rag_documents.list_operations(SimpleNamespace()))

    assert logger_args["tenant_id"] == "tenant-a"
    assert logger_args["kb_ids"] is None
    assert captured["tenant_id"] == "tenant-a"
    assert captured["kb_ids"] is None  # admin 可看本租户全部知识库
    assert response["active_uploads"] == []


def test_list_operations_passes_resource_scope_to_terminal_log_query(monkeypatch):
    authz = SimpleNamespace(
        is_admin=False,
        principal=SimpleNamespace(
            tenant_id="tenant-a", user_id="user-a", department="hr",
        ),
        kb_scope=frozenset({"kb-hr"}),
    )
    monkeypatch.setattr(rag_documents, "_require_authz", lambda _request: authz)
    captured = {}
    monkeypatch.setattr(
        rag_documents,
        "_get_op_logger",
        lambda: SimpleNamespace(
            list=lambda **kwargs: (captured.update(kwargs) or {"items": [], "total": 0}),
        ),
    )
    monkeypatch.setattr(
        rag_documents,
        "_get_registry",
        lambda: SimpleNamespace(_table="doc_registry"),
    )

    class _RunStore:
        def list_active_runs(self, **_kwargs):
            return []

    monkeypatch.setattr(
        "backend.rag.indexing.index_run_store_pg.get_index_run_store",
        lambda: _RunStore(),
    )

    asyncio.run(rag_documents.list_operations(SimpleNamespace()))

    assert captured["tenant_id"] == "tenant-a"
    assert captured["kb_ids"] == ["kb-hr"]
    assert captured["department"] == "hr"
    assert captured["user_id"] == "user-a"
    assert captured["registry_table"] == "doc_registry"


def test_operation_log_query_filters_rows_and_total_by_document_scope(monkeypatch):
    from backend.rag.indexing.operation_log_pg import (
        PostgresDocumentOperationLogger,
    )

    class _QueryCursor:
        def __init__(self):
            self.calls = []

        def execute(self, query, params):
            self.calls.append((query, params))

        def fetchone(self):
            return (1,)

        def fetchall(self):
            return []

    class _Connection:
        def __init__(self, cursor):
            self.cursor_instance = cursor

        def cursor(self, cursor_factory=None):
            return self.cursor_instance

    cursor = _QueryCursor()
    connection = _Connection(cursor)
    logger = object.__new__(PostgresDocumentOperationLogger)
    logger._table = "operation_log"
    logger._lock = Lock()

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(logger, "_conn", fake_connection)
    result = logger.list(
        tenant_id="tenant-a",
        kb_ids=["kb-hr"],
        department="hr",
        user_id="user-a",
        registry_table="doc_registry",
    )

    assert result["total"] == 1
    assert len(cursor.calls) == 2
    for query, _params in cursor.calls:
        assert "o.tenant_id = %s" in query
        assert "EXISTS" in query
        assert "d.kb_id = ANY(%s)" in query
        assert "d.department = %s" in query
    assert cursor.calls[0][1] == (
        "tenant-a", "user-a", ["kb-hr"], "hr",
    )
    assert cursor.calls[1][1] == (
        "tenant-a", "user-a", ["kb-hr"], "hr", 20, 0,
    )


def test_pending_failure_scope_filters_tenant_kb_and_department():
    where, params = IndexRunStore._pending_failure_scope(
        tenant_id="tenant-a",
        kb_ids=["kb-hr"],
        department="hr",
    )

    assert where == (
        "runs.tenant_id = %s AND runs.kb_id = ANY(%s) "
        "AND runs.department = %s"
    )
    assert params == ("tenant-a", ["kb-hr"], "hr")

    empty_where, empty_params = IndexRunStore._pending_failure_scope(
        tenant_id="tenant-a",
        kb_ids=[],
        department="hr",
    )
    assert empty_where.endswith("AND FALSE")
    assert empty_params == ("tenant-a",)
