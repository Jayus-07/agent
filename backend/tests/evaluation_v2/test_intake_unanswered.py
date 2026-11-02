import asyncio
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.evaluation_v2 import repository

ENTRY_ID = 42


def _entry():
    return {"id": ENTRY_ID, "source": "rag_miss", "question": "泉州有什么特产?", "trace_id": "f4ffd1fda1d9"}


def _identity(role="admin"):
    return (
        Identity(user_id="alice", tenant_id="tenant-a"),
        OperatorIdentity(role=role, actor=f"user:alice"),
    )


def test_intake_unanswered_creates_candidate(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    monkeypatch.setattr(routes, "_load_unanswered_entry", lambda _eid: _entry())
    monkeypatch.setattr(routes.repository, "find_candidate_by_source_origin", lambda *_a: None)
    captured = {}

    def _fake_create(tenant_id, actor, *, source_kind, payload, idempotency_key, privacy_status):
        captured.update(tenant_id=tenant_id, actor=actor, source_kind=source_kind,
                        payload=payload, idempotency_key=idempotency_key,
                        privacy_status=privacy_status)
        return {"candidate_id": "c-1", "review_status": "in_review"}

    monkeypatch.setattr(routes.repository, "create_candidate", _fake_create)
    identity, actor = _identity()

    result = asyncio.run(routes.intake_unanswered(
        body=routes.UnansweredIntake(entry_id=ENTRY_ID, idempotency_key="unans-0001-intake"),
        identity=identity, actor=actor,
    ))

    assert result["candidate_id"] == "c-1"
    assert captured["source_kind"] == "trace"
    assert captured["tenant_id"] == "tenant-a"
    assert captured["actor"] == "user:alice"
    assert captured["idempotency_key"] == "unans-0001-intake"
    assert captured["privacy_status"] == "redacted"
    assert captured["payload"]["input"]["query"] == "泉州有什么特产?"
    assert captured["payload"]["metadata"]["source"] == "unanswered_question"
    assert captured["payload"]["source_ref"]["origin"] == str(ENTRY_ID)
    assert captured["payload"]["source_ref"]["origin_source"] == "rag_miss"


def test_intake_unanswered_missing_entry_returns_404(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    monkeypatch.setattr(routes, "_load_unanswered_entry", lambda _eid: None)
    identity, actor = _identity()

    with pytest.raises(HTTPException) as captured:
        asyncio.run(routes.intake_unanswered(
            body=routes.UnansweredIntake(entry_id=999999, idempotency_key="unans-0002-intake"),
            identity=identity, actor=actor,
        ))

    assert captured.value.status_code == 404


def test_intake_unanswered_empty_question_returns_422(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    monkeypatch.setattr(routes, "_load_unanswered_entry",
                        lambda _eid: {**_entry(), "question": "   "})
    identity, actor = _identity()

    with pytest.raises(HTTPException) as captured:
        asyncio.run(routes.intake_unanswered(
            body=routes.UnansweredIntake(entry_id=ENTRY_ID, idempotency_key="unans-0003-intake"),
            identity=identity, actor=actor,
        ))

    assert captured.value.status_code == 422


def test_intake_unanswered_duplicate_origin_replays_without_create(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    existing = {"candidate_id": "c-existing", "review_status": "in_review"}
    monkeypatch.setattr(routes, "_load_unanswered_entry", lambda _eid: _entry())
    monkeypatch.setattr(routes.repository, "find_candidate_by_source_origin",
                        lambda _tenant, origin: existing if origin == str(ENTRY_ID) else None)
    touched = []
    monkeypatch.setattr(routes.repository, "create_candidate",
                        lambda *_a, **_k: touched.append(1))
    identity, actor = _identity()

    result = asyncio.run(routes.intake_unanswered(
        body=routes.UnansweredIntake(entry_id=ENTRY_ID, idempotency_key="unans-0004-intake"),
        identity=identity, actor=actor,
    ))

    assert result["candidate_id"] == "c-existing"
    assert result["duplicate"] is True
    assert touched == []


def test_intake_unanswered_sanitizes_pii(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    monkeypatch.setattr(routes, "_load_unanswered_entry", lambda _eid: {
        **_entry(),
        "question": "我的手机号 13812345678 查不到订单",
    })
    monkeypatch.setattr(routes.repository, "find_candidate_by_source_origin", lambda *_a: None)
    captured = {}
    monkeypatch.setattr(
        routes.repository, "create_candidate",
        lambda _t, _a, *, source_kind, payload, idempotency_key, privacy_status:
            captured.update(payload=payload) or {"candidate_id": "c-2"})
    identity, actor = _identity()

    asyncio.run(routes.intake_unanswered(
        body=routes.UnansweredIntake(entry_id=ENTRY_ID, idempotency_key="unans-0005-intake"),
        identity=identity, actor=actor,
    ))

    assert captured["payload"]["input"]["query"] == "我的手机号 [PHONE_REDACTED] 查不到订单"


def test_intake_unanswered_requires_admin_role(monkeypatch):
    from backend.app.api.routes import evaluation_v2 as routes

    touched = []
    monkeypatch.setattr(routes.repository, "create_candidate",
                        lambda *_a, **_k: touched.append(1))
    identity, actor = _identity(role="editor")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(routes.intake_unanswered(
            body=routes.UnansweredIntake(entry_id=ENTRY_ID, idempotency_key="unans-0006-intake"),
            identity=identity, actor=actor,
        ))

    assert captured.value.status_code == 403
    assert touched == []


class _OriginCursor:
    def __init__(self):
        self.last_params = None
        self.pending = None

    def execute(self, query, params=None):
        self.last_params = params
        # 空 origin 由 repository 层短路，不会触达 SQL；此处仅模拟命中一行
        self.pending = {"candidate_id": "c-x"}

    def fetchone(self):
        row, self.pending = self.pending, None
        return row


def test_find_candidate_by_source_origin_passes_origin_as_parameter(monkeypatch):
    cursor = _OriginCursor()

    @contextmanager
    def transaction(_tenant_id):
        yield object(), cursor

    monkeypatch.setattr(repository, "_transaction", transaction)

    result = repository.find_candidate_by_source_origin("tenant-a", "42")

    assert result == {"candidate_id": "c-x"}
    # origin 必须走参数化占位符（%s），不得拼进 SQL 文本
    assert cursor.last_params == ("tenant-a", "42")
    assert repository.find_candidate_by_source_origin("tenant-a", "  ") is None
