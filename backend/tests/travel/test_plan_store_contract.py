"""行程版本账本 SQL 契约：租户隔离、Active 保留与当前版本 CAS。"""
from __future__ import annotations

from contextlib import contextmanager

from backend.travel.core import plan_store


class _Cursor:
    def __init__(self, *, result=None, rowcount=1):
        self.statements: list[tuple[str, tuple]] = []
        self.result = result
        self.rowcount = rowcount

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))

    def fetchone(self):
        return self.result


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _patch_store(monkeypatch, cursor):
    @contextmanager
    def connection():
        yield _Connection(cursor)

    monkeypatch.setattr(plan_store, "_conn", connection)
    monkeypatch.setattr(plan_store, "_ensure_table", lambda: True)
    monkeypatch.setattr(plan_store, "enabled", lambda: True)


def test_schema_and_pruning_keep_tenant_scope_and_latest_active(monkeypatch):
    cursor = _Cursor()
    _patch_store(monkeypatch, cursor)

    assert plan_store.save_version(
        "conv-1", "user-1", {"plan_version": 3, "brief": {}},
        tenant_id="tenant-a",
    )

    assert "PRIMARY KEY (tenant_id, user_id, conversation_id, plan_version)" in plan_store._SCHEMA_SQL
    insert_sql, insert_params = cursor.statements[1]
    assert "ON CONFLICT (tenant_id, user_id, conversation_id, plan_version)" in insert_sql
    assert insert_params[:4] == ("conv-1", 3, "tenant-a", "user-1")
    prune_sql, prune_params = cursor.statements[2]
    assert "plan_status = 'confirmed'" in prune_sql
    assert "plan_version <> COALESCE" in prune_sql
    assert prune_sql.count("%s") == len(prune_params)
    assert prune_params[:3] == ("tenant-a", "conv-1", "user-1")
    assert prune_params[-3:] == ("tenant-a", "conv-1", "user-1")


def test_confirm_cas_requires_latest_version_inside_tenant_scope(monkeypatch):
    cursor = _Cursor(result=("confirmed",))
    _patch_store(monkeypatch, cursor)

    result = plan_store.confirm_version(
        "conv-1", "user-1", 2, tenant_id="tenant-a")

    assert result == "confirmed"
    lock_sql, lock_params = cursor.statements[0]
    assert "pg_advisory_xact_lock" in lock_sql
    assert lock_params == ("tenant-a:user-1:conv-1",)
    cas_sql, cas_params = cursor.statements[1]
    assert "plan_status = 'waiting_confirmation'" in cas_sql
    assert "SELECT max(p.plan_version)" in cas_sql
    assert "p.tenant_id = %s AND p.user_id = %s" in cas_sql
    assert cas_sql.count("%s") == len(cas_params)
    assert cas_params[:3] == ("tenant-a", "conv-1", "user-1")
