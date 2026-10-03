"""知识生命周期 API 回归（2026-10-03，C1/C2 验收「状态机 API 全链用例」）。

只 mock 外部边界：身份（require_rag_* 依赖 + RagAuthorization）与 PG
（registry/audit 连接全部注入 stub）。断言 HTTP 语义与 fail-closed 文案。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_rag_editor, require_rag_user
from backend.app.api.routes import rag_lifecycle
from backend.rag.indexing.lifecycle import KnowledgeLifecycleService, LifecycleAuditLog


class StubRegistry:
    def __init__(self, docs: dict[str, dict]):
        self.docs = docs

    def get_by_doc_id(self, doc_id: str):
        row = self.docs.get(doc_id)
        return dict(row) if row else None

    def transition_status_by_doc_id(self, doc_id, new_status, allowed_from):
        row = self.docs.get(doc_id)
        if not row or row.get("status") not in allowed_from:
            return 0
        row["status"] = new_status
        return 1

    def set_expire_at(self, doc_id, expire_at):
        row = self.docs.get(doc_id)
        if not row or row.get("status") != "active":
            return 0
        row["expire_at"] = expire_at
        return 1


class StubAuthz:
    def __init__(self, manage_ok: bool = True):
        self.manage_ok = manage_ok

    def can_manage_row(self, row):
        return (True, "") if self.manage_ok else (False, "非本部门文档")

    def can_read_row(self, row):
        return (True, "")


class StubPrincipal:
    subject_type = "employee"
    user_id = "op1"


class FakeCursor:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._store.append((sql.strip(), params))

    def fetchall(self):
        return [{"id": 1, "doc_id": "d1", "from_status": "active",
                 "to_status": "deprecated", "action": "active->deprecated",
                 "actor": "employee:op1", "reason": "", "created_at": "now"}]


class FakeConn:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._store)


@pytest.fixture()
def client(monkeypatch):
    registry = StubRegistry({
        "d1": {"doc_id": "d1", "status": "active", "file_name": "政策.md"},
        "d2": {"doc_id": "d2", "status": "failed", "file_name": "旧版.md"},
    })
    audit = LifecycleAuditLog(lambda: FakeConn([]))
    monkeypatch.setattr(rag_lifecycle, "_get_registry", lambda: registry)
    monkeypatch.setattr(
        rag_lifecycle, "_get_lifecycle_service",
        lambda: KnowledgeLifecycleService(registry, audit),
    )
    monkeypatch.setattr(
        rag_lifecycle, "_require_authz",
        lambda request: (StubAuthz(), StubPrincipal()),
    )
    monkeypatch.setattr(rag_lifecycle, "_extract_source", lambda request: "test")

    app = FastAPI()
    app.include_router(rag_lifecycle.router, prefix="/rag")
    app.dependency_overrides[require_rag_user] = lambda: None
    app.dependency_overrides[require_rag_editor] = lambda: None
    with TestClient(app) as c:
        yield c


def test_full_chain_via_api(client):
    """active→deprecated→active 恢复全链走通，每次流转 200 且 ok。"""
    r1 = client.post("/rag/knowledge/d1/lifecycle",
                     json={"to_status": "deprecated", "reason": "活动结束"})
    assert r1.status_code == 200 and r1.json()["ok"] is True
    assert (r1.json()["from"], r1.json()["to"]) == ("active", "deprecated")

    r2 = client.post("/rag/knowledge/d1/lifecycle",
                     json={"to_status": "active", "reason": "恢复"})
    assert r2.status_code == 200 and r2.json()["ok"] is True

    events = client.get("/rag/knowledge/d1/lifecycle/events")
    assert events.status_code == 200 and events.json()["ok"] is True
    assert isinstance(events.json()["events"], list)


def test_fail_closed_active_direct_jump_rejected(client):
    """fail-closed：failed→active 经 API 被拒（源状态不归管辖，等同阻断
    绕过审核直跳 published）。pending_review→active 同理被拒。"""
    r = client.post("/rag/knowledge/d2/lifecycle", json={"to_status": "active"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert ("fail-closed" in body["error"]) or ("不归生命周期操作管辖" in body["error"])


def test_unknown_doc_404(client):
    r = client.post("/rag/knowledge/nope/lifecycle", json={"to_status": "deprecated"})
    assert r.status_code == 404


def test_manage_denied_403(client, monkeypatch):
    monkeypatch.setattr(
        rag_lifecycle, "_require_authz",
        lambda request: (StubAuthz(manage_ok=False), StubPrincipal()),
    )
    r = client.post("/rag/knowledge/d1/lifecycle", json={"to_status": "deprecated"})
    assert r.status_code == 403
    assert r.json()["error"] == "非本部门文档"


def test_expire_at_validation_and_set(client):
    bad = client.post("/rag/knowledge/d1/expire-at", json={"expire_at": "明年年底"})
    assert bad.status_code == 200 and bad.json()["ok"] is False
    assert "ISO" in bad.json()["error"]

    ok = client.post("/rag/knowledge/d1/expire-at",
                     json={"expire_at": "2026-12-31", "reason": "政策年度有效"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["expire_at"] == "2026-12-31"


def test_py_compile_route_module():
    import pathlib
    import py_compile
    py_compile.compile(
        str(pathlib.Path(rag_lifecycle.__file__)), doraise=True
    )
