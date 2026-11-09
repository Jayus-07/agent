"""RAG 生命周期发布守卫回归（#61 / #62 / #65）。

钉死三条契约：
  1. 文档在排队期间被下线（deprecated）或送审（pending_review）后，迟到
     发布输掉 CAS，不得把撤销决策覆写回 active；新上传仍可替换待审核行。
  2. chunk 详情端点对非 active 文档只向管理员暴露正文，普通可读主体拿不到。
  3. 索引发布前按 actor 当前授权复验，失权即按 superseded 终止。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import psycopg2
import pytest

from backend.config.database import DOC_REGISTRY_PG_CONFIG
from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.lifecycle import KnowledgeLifecycleService

PG_TABLE = "doc_registry_lifecycle_guard_test"
PATH = "test://lifecycle-guard/doc.md"
DOC_ID = "lifecycle_guard_doc"


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(
    not _pg_alive(), reason="No PostgreSQL reachable (DOC_REGISTRY_PG_CONFIG)")


@pytest.fixture()
def registry(monkeypatch):
    monkeypatch.setenv("DOC_REGISTRY_BACKEND", "postgres")
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
    reg = DocumentRegistry("ignored/path.db")
    reg.clear()
    yield reg
    reg.clear()


def _register_active(reg):
    reg.register(PATH, DOC_ID, "a" * 64, "kb_guard", ["c1"], "db_guard",
                 metadata={"doc_type": "general"})
    row = reg.get_by_doc_id(DOC_ID)
    assert row["status"] == "active"
    return row


def _publish(reg, *, generation="g2", base_generation="", base_status="active"):
    return reg.register_published(
        PATH, DOC_ID, "b" * 64, "kb_guard", ["c2"], "db_guard2",
        metadata={"doc_type": "general"},
        active_generation=generation,
        expected_base_generation=base_generation,
        expected_base_status=base_status,
    )


@needs_pg
def test_deprecated_doc_rejects_queued_publish(registry):
    """下线后迟到发布：CAS 返回 0，状态保持 deprecated（不得复活）。"""
    _register_active(registry)
    assert registry.transition_status_by_doc_id(DOC_ID, "deprecated", ("active",)) == 1

    assert _publish(registry) == 0
    row = registry.get_by_doc_id(DOC_ID)
    assert row["status"] == "deprecated"
    assert row["active_generation"] == ""


@needs_pg
def test_send_to_review_rejects_queued_publish(registry):
    """送审后迟到发布同样被拒（撤销决策不被覆写）。"""
    _register_active(registry)
    assert registry.transition_status_by_doc_id(DOC_ID, "pending_review", ("active",)) == 1

    assert _publish(registry) == 0
    assert registry.get_by_doc_id(DOC_ID)["status"] == "pending_review"


@needs_pg
def test_new_upload_replaces_pending_review_row(registry):
    """待审核行可被新上传替换：认领状态= pending_review → 发布放行。"""
    _register_active(registry)
    assert registry.transition_status_by_doc_id(DOC_ID, "pending_review", ("active",)) == 1

    assert _publish(registry, base_status="pending_review") == 1
    assert registry.get_by_doc_id(DOC_ID)["status"] == "active"


@needs_pg
def test_legacy_run_without_base_status_keeps_old_behavior(registry):
    """旧运行记录（无认领状态）不做状态守卫，保持既有 CAS 语义。"""
    _register_active(registry)
    assert registry.transition_status_by_doc_id(DOC_ID, "deprecated", ("active",)) == 1

    assert _publish(registry, base_status="") == 1


# ── #61 撤销时取消在途运行 ──────────────────────────────────────────


class _FakeRegistry:
    def __init__(self, doc):
        self.doc = dict(doc)
        self.transitions: list[tuple] = []

    def get_by_doc_id(self, doc_id):
        return dict(self.doc) if doc_id == self.doc["doc_id"] else None

    def transition_status_by_doc_id(self, doc_id, new_status, allowed_from):
        if self.doc["status"] not in allowed_from:
            return 0
        self.transitions.append((doc_id, new_status))
        self.doc["status"] = new_status
        return 1

    def set_expire_at(self, doc_id, expire_at):
        return 1


class _FakeAudit:
    def record(self, *a, **kw):
        return None


class _FakeRunStore:
    def __init__(self, runs):
        self.runs = runs
        self.marked: list[tuple[str, str]] = []

    def list_non_terminal_by_file(self, file_path):
        return [dict(r) for r in self.runs if r["file_path"] == file_path]

    def mark_status(self, upload_id, status, stage="", error=""):
        self.marked.append((upload_id, status))
        return {"upload_id": upload_id, "status": status}


def test_withdrawal_cancels_inflight_runs():
    reg = _FakeRegistry({
        "doc_id": DOC_ID, "status": "active", "file_path": "/docs/a.md",
    })
    runs = _FakeRunStore([
        {"upload_id": "u1", "file_path": "/docs/a.md", "status": "indexing"},
        {"upload_id": "u2", "file_path": "/docs/other.md", "status": "indexing"},
    ])
    service = KnowledgeLifecycleService(reg, _FakeAudit(), run_store=runs)

    result = service.transition(DOC_ID, "deprecated", actor="user:1")

    assert result["ok"] is True
    assert result["cancelled_runs"] == ["u1"]
    assert runs.marked == [("u1", "superseded")]


def test_run_store_failure_does_not_block_transition():
    reg = _FakeRegistry({
        "doc_id": DOC_ID, "status": "active", "file_path": "/docs/a.md",
    })

    class _BrokenStore:
        def list_non_terminal_by_file(self, file_path):
            raise RuntimeError("db down")

    service = KnowledgeLifecycleService(reg, _FakeAudit(), run_store=_BrokenStore())
    result = service.transition(DOC_ID, "deprecated", actor="user:1")

    assert result["ok"] is True
    assert result["to"] == "deprecated"
    assert result["cancelled_runs"] == []


# ── #62 chunk 详情发布状态门 ───────────────────────────────────────


class _Authz:
    def __init__(self, is_admin):
        self.is_admin = is_admin

    def can_read_row(self, row):
        return True


class _Reg:
    def __init__(self, status):
        self.doc = {
            "doc_id": DOC_ID, "status": status, "chunk_ids": '["c1"]',
            "tenant_id": "default", "kb_id": "kb_guard", "file_path": "/x.md",
        }

    def get_by_doc_id(self, doc_id):
        return dict(self.doc)


class _ChunkStore:
    def get_by_doc_id(self, doc_id):
        return [{"doc_id": doc_id, "chunk_index": 0, "content": "正文",
                 "char_count": 2, "section_title": "s", "doc_type": "general",
                 "kb_id": "kb_guard", "department": "d"}]


def _call_chunk_detail(monkeypatch, *, status: str, is_admin: bool):
    from backend.app.api.routes import rag_documents as routes
    from backend.rag.indexing import chunk_store as chunk_store_mod

    async def _authz(_request):
        return _Authz(is_admin)

    monkeypatch.setattr(routes, "_require_authz", _authz)
    monkeypatch.setattr(routes, "_get_registry", lambda: _Reg(status))
    monkeypatch.setattr(chunk_store_mod, "get_chunk_store", lambda: _ChunkStore())
    return asyncio.run(routes.get_chunk_detail(DOC_ID, SimpleNamespace()))


def test_chunk_detail_hides_non_active_from_non_admin(monkeypatch):
    for status in ("deprecated", "pending_review"):
        result = _call_chunk_detail(monkeypatch, status=status, is_admin=False)
        assert result["total"] == 0
        assert result["chunks"] == []
        assert result["status"] == status


def test_chunk_detail_keeps_admin_diagnostics(monkeypatch):
    result = _call_chunk_detail(monkeypatch, status="deprecated", is_admin=True)
    assert result["total"] == 1
    assert result["chunks"][0]["content"] == "正文"


def test_chunk_detail_active_unchanged(monkeypatch):
    result = _call_chunk_detail(monkeypatch, status="active", is_admin=False)
    assert result["total"] == 1


# ── #65 发布前授权复验 ─────────────────────────────────────────────


def _reverify():
    from backend.app.api.routes.rag_upload import _reverify_publish_authorization
    return _reverify_publish_authorization


def test_publish_recheck_denies_revoked_actor(monkeypatch):
    from backend.security import task_authorization as ta

    def _deny(*_a, **_kw):
        raise ta.TaskAuthorizationDenied("用户已禁用")

    monkeypatch.setattr(ta, "resolve_task_authorization", _deny)
    from backend.rag.indexing.publish import PublishAuthorizationRevoked
    with pytest.raises(PublishAuthorizationRevoked, match="用户已禁用"):
        _reverify()(actor_id="42", tenant_id="default",
                    kb_id="kb_guard", department="d")


def test_publish_recheck_denies_kb_scope_loss(monkeypatch):
    from backend.rag import authz as rag_authz

    class _Denied:
        @classmethod
        def build(cls, principal):
            raise rag_authz.RagAuthorizationError("KB 不可见")

    monkeypatch.setattr(rag_authz, "RagAuthorization", _Denied)
    from backend.security import task_authorization as ta
    from backend.security.principal import Principal
    monkeypatch.setattr(
        ta, "resolve_task_authorization",
        lambda *_a, **_kw: SimpleNamespace(
            principal=Principal(user_id="42", tenant_id="default",
                                authenticated=True)))
    from backend.rag.indexing.publish import PublishAuthorizationRevoked
    with pytest.raises(PublishAuthorizationRevoked):
        _reverify()(actor_id="42", tenant_id="default",
                    kb_id="kb_guard", department="d")


def test_publish_recheck_allows_valid_actor(monkeypatch):
    from backend.security import task_authorization as ta
    from backend.rag import authz as rag_authz
    from backend.security.principal import Principal

    seen: dict = {}

    class _Allow:
        @classmethod
        def build(cls, principal):
            seen["department"] = principal.department
            return SimpleNamespace(
                can_upload_to=lambda kb, dept: (True, ""))

    monkeypatch.setattr(rag_authz, "RagAuthorization", _Allow)
    monkeypatch.setattr(
        ta, "resolve_task_authorization",
        lambda *_a, **_kw: SimpleNamespace(
            principal=Principal(user_id="42", tenant_id="default",
                                department="", authenticated=True)))
    assert _reverify()(actor_id="42", tenant_id="default",
                       kb_id="kb_guard", department="cs_policy") is None
    # 部门口径取运行记录（HTTP 头部门），不是 auth.users.dept
    assert seen["department"] == "cs_policy"


def test_publish_recheck_skips_placeholder_actors(monkeypatch):
    from backend.security import task_authorization as ta

    def _boom(*_a, **_kw):
        raise AssertionError("占位身份不应触发授权查询")

    monkeypatch.setattr(ta, "resolve_task_authorization", _boom)
    for actor in ("", "system", "default", "anonymous"):
        assert _reverify()(actor_id=actor, tenant_id="default",
                           kb_id="kb_guard", department="d") is None
