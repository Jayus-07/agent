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
        principal=SimpleNamespace(tenant_id="tenant-a"),
        kb_scope=frozenset(),
        can_manage_row=lambda _row: (True, ""),
    )
    monkeypatch.setattr(rag_documents, "_require_authz", lambda _request: authz)
    monkeypatch.setattr(
        rag_documents,
        "_get_op_logger",
        lambda: SimpleNamespace(list=lambda **_kwargs: {"items": [], "total": 0}),
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

    assert captured["tenant_id"] == "tenant-a"
    assert captured["kb_ids"] is None  # admin 可看本租户全部知识库
    assert response["active_uploads"] == []
