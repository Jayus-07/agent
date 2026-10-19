from contextlib import contextmanager
from threading import Lock
from types import SimpleNamespace

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
