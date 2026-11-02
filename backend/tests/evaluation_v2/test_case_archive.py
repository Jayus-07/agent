from contextlib import contextmanager
import uuid as uuid_mod

import pytest
from fastapi import HTTPException

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.evaluation_v2 import repository

CASE_ID = "3f2b8c1a-5d4e-4f6a-9b7c-1a2b3c4d5e6f"


class ArchiveCursor:
    """按 SQL 片段分发结果的假 cursor，语句全部记录供断言。"""

    def __init__(self, *, case_row, published_reference=None):
        self.case_row = case_row
        self.published_reference = published_reference
        self.pending = None
        self.statements = []
        self.update_count = 0

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.statements.append((normalized, params))
        if "FROM evaluation_v2.eval_cases AS c" in normalized:
            self.pending = self.case_row
        elif "FROM evaluation_v2.eval_dataset_items AS i" in normalized:
            self.pending = self.published_reference
        elif "UPDATE evaluation_v2.eval_cases" in normalized:
            self.update_count += 1
        else:
            raise AssertionError(f"未预期 SQL: {normalized}")

    def fetchone(self):
        row, self.pending = self.pending, None
        return row


@contextmanager
def transaction_for(cursor):
    yield object(), cursor


def install_transaction(monkeypatch, cursor):
    monkeypatch.setattr(repository, "_transaction", lambda _tenant_id: transaction_for(cursor))


def approved_case_row():
    return {
        "case_id": CASE_ID,
        "lifecycle_status": "approved",
        "revision": 1,
        "content_hash": "b" * 64,
    }


def test_archive_case_success_moves_to_archived_and_writes_audit(monkeypatch):
    cursor = ArchiveCursor(case_row=approved_case_row())
    install_transaction(monkeypatch, cursor)
    audit = []
    monkeypatch.setattr(repository, "_record_audit", lambda *args, **kwargs: audit.append((args, kwargs)))

    result = repository.archive_case("tenant-a", "user:alice", CASE_ID)

    assert result == {
        "case_id": CASE_ID,
        "lifecycle_status": "archived",
        "latest_revision": 1,
        "archived": True,
    }
    assert cursor.update_count == 1
    update_params = cursor.statements[-1][1]
    assert update_params == ("tenant-a", CASE_ID, "approved")
    assert len(audit) == 1
    assert audit[0][0][3:6] == ("case.archived", "case", CASE_ID)
    assert audit[0][1]["before_hash"] == "b" * 64
    assert audit[0][1]["details"]["previous_status"] == "approved"


def test_archive_case_idempotent_replay_returns_without_new_audit(monkeypatch):
    archived_row = approved_case_row()
    archived_row["lifecycle_status"] = "archived"
    cursor = ArchiveCursor(case_row=archived_row)
    install_transaction(monkeypatch, cursor)
    audit = []
    monkeypatch.setattr(repository, "_record_audit", lambda *args, **kwargs: audit.append((args, kwargs)))

    result = repository.archive_case("tenant-a", "user:alice", CASE_ID)

    assert result == {
        "case_id": CASE_ID,
        "lifecycle_status": "archived",
        "latest_revision": 1,
        "archived": False,
    }
    assert cursor.update_count == 0
    assert audit == []


def test_archive_case_rejects_case_referenced_by_published_version(monkeypatch):
    cursor = ArchiveCursor(
        case_row=approved_case_row(),
        published_reference={"?column?": 1},
    )
    install_transaction(monkeypatch, cursor)
    audit = []
    monkeypatch.setattr(repository, "_record_audit", lambda *args, **kwargs: audit.append((args, kwargs)))

    with pytest.raises(repository.RepositoryConflictError, match="已发布数据集版本"):
        repository.archive_case("tenant-a", "user:alice", CASE_ID)

    assert cursor.update_count == 0
    assert audit == []


def test_archive_case_missing_case_returns_not_found(monkeypatch):
    cursor = ArchiveCursor(case_row=None)
    install_transaction(monkeypatch, cursor)

    with pytest.raises(repository.RepositoryNotFoundError):
        repository.archive_case("tenant-a", "user:alice", CASE_ID)


def test_archive_case_rejects_invalid_uuid():
    with pytest.raises(ValueError, match="UUID"):
        repository.archive_case("tenant-a", "user:alice", "not-a-uuid")


def test_archive_endpoint_requires_admin_role(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    touched = []
    monkeypatch.setattr(routes.repository, "archive_case", lambda *_a, **_k: touched.append(1))

    with pytest.raises(HTTPException) as captured:
        import asyncio
        asyncio.run(routes.archive_case(
            case_id=uuid_mod.UUID(CASE_ID),
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="editor", actor="user:alice"),
        ))

    assert captured.value.status_code == 403
    assert touched == []


def test_archive_endpoint_maps_conflict_to_409(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    def _raise_conflict(*_args, **_kwargs):
        raise repository.RepositoryConflictError("Case 已进入已发布数据集版本，禁止归档")

    monkeypatch.setattr(routes.repository, "archive_case", _raise_conflict)

    with pytest.raises(HTTPException) as captured:
        import asyncio
        asyncio.run(routes.archive_case(
            case_id=uuid_mod.UUID(CASE_ID),
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="admin", actor="user:alice"),
        ))

    assert captured.value.status_code == 409
