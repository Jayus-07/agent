"""待复核文档在 remote 部署下的审核闭环测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest


class _FakeStore:
    def __init__(self, doc_id: str):
        self.ids = [doc_id]

    def delete(self, *, where):
        self.ids = []

    def get(self, *, where):
        return {"ids": list(self.ids)}


class _FakeChunkStore:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id

    def delete_by_doc_id(self, doc_id: str):
        if doc_id == self.doc_id:
            self.doc_id = ""

    def count_by_doc_id(self, doc_id: str) -> int:
        return int(doc_id == self.doc_id and bool(self.doc_id))


class _FakeRegistry:
    def __init__(self, doc_id: str, file_path: str):
        self.doc = {
            "doc_id": doc_id,
            "status": "pending_review",
            "file_path": file_path,
            "file_name": Path(file_path).name,
        }
        self.updated: list[tuple[str, str]] = []

    def get_by_doc_id(self, doc_id: str):
        return dict(self.doc) if doc_id == self.doc["doc_id"] else None

    def update_status_by_doc_id(self, doc_id: str, status: str) -> int:
        if doc_id != self.doc["doc_id"] or self.doc["status"] != "pending_review":
            return 0
        self.doc["status"] = status
        self.updated.append((doc_id, status))
        return 1


class _FakePipeline:
    def __init__(self, doc_id: str, *, fail_bm25: bool = False):
        self.vectordb = _FakeStore(doc_id)
        self.doc_db = _FakeStore(doc_id)
        self.bm25_store = SimpleNamespace(
            load_docs=lambda: [SimpleNamespace(metadata={"doc_id": doc_id})]
        )
        self._doc_id = doc_id
        self.fail_bm25 = fail_bm25

    def remove_documents_from_bm25(self, doc_ids, file_paths=None):
        if self.fail_bm25:
            raise RuntimeError("BM25 rebuild failed")
        self.bm25_store.load_docs = lambda: []


def test_reject_success_purges_before_marking_deleted(tmp_path, monkeypatch):
    from backend.rag.indexing import review_service

    doc_id = "dup-1"
    source = tmp_path / "duplicate.txt"
    source.write_text("duplicate", encoding="utf-8")
    registry = _FakeRegistry(doc_id, str(source))
    pipeline = _FakePipeline(doc_id)
    chunk_store = _FakeChunkStore(doc_id)
    monkeypatch.setattr(review_service, "_get_chunk_store", lambda: chunk_store)

    result = review_service.review_pending_doc(
        doc_id, "reject", registry=registry, pipeline=pipeline,
    )

    assert result == {"ok": True, "doc_id": doc_id, "new_status": "deleted", "warnings": None}
    assert registry.updated == [(doc_id, "deleted")]
    assert not source.exists()
    assert pipeline.vectordb.ids == []
    assert pipeline.doc_db.ids == []
    assert chunk_store.count_by_doc_id(doc_id) == 0


def test_reject_failure_keeps_pending_and_source_file(tmp_path, monkeypatch):
    from backend.rag.indexing import review_service

    doc_id = "dup-2"
    source = tmp_path / "duplicate.txt"
    source.write_text("duplicate", encoding="utf-8")
    registry = _FakeRegistry(doc_id, str(source))
    pipeline = _FakePipeline(doc_id, fail_bm25=True)
    chunk_store = _FakeChunkStore(doc_id)
    monkeypatch.setattr(review_service, "_get_chunk_store", lambda: chunk_store)

    result = review_service.review_pending_doc(
        doc_id, "reject", registry=registry, pipeline=pipeline,
    )

    assert result["ok"] is False
    assert registry.doc["status"] == "pending_review"
    assert source.exists()
    assert result["warnings"]


def test_approve_only_changes_registry_and_invalidates_review_cache(monkeypatch):
    from backend.rag.indexing import review_service

    registry = _FakeRegistry("dup-3", "C:/docs/duplicate.txt")
    pipeline = MagicMock()
    invalidated = []
    monkeypatch.setattr(
        review_service, "_invalidate_review_cache",
        lambda: invalidated.append(True),
    )

    result = review_service.review_pending_doc(
        "dup-3", "approve", registry=registry, pipeline=pipeline,
    )

    assert result == {"ok": True, "doc_id": "dup-3", "new_status": "active", "warnings": None}
    assert registry.updated == [("dup-3", "active")]
    pipeline.assert_not_called()
    assert invalidated == [True]


def test_remote_proxy_posts_review_decision_with_internal_token(monkeypatch):
    from backend.rag.client import RAGServiceProxy

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.read()
        captured["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(200, json={"ok": True, "new_status": "deleted"})

    monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "internal-secret")
    proxy = RAGServiceProxy(
        base_url="http://test-rag:8090", transport=httpx.MockTransport(handler),
    )

    result = proxy.review_pending_doc("dup-4", "reject")

    assert result["new_status"] == "deleted"
    assert captured["path"] == "/admin/pending/dup-4/decision"
    assert b'"action":"reject"' in captured["body"]
    assert captured["token"] == "internal-secret"


@pytest.mark.parametrize("route_name, action", [
    ("approve_pending_doc", "approve"),
    ("reject_pending_doc", "reject"),
])
def test_remote_review_routes_delegate_to_rag_service(monkeypatch, route_name, action):
    from backend.app.api.routes import rag_documents

    class _Authorization:
        def can_manage_row(self, _doc):
            return True, ""

    class _Registry:
        def get_by_doc_id(self, _doc_id):
            return {"doc_id": _doc_id, "status": "pending_review"}

    class _Proxy:
        def review_pending_doc(self, doc_id, requested_action):
            assert requested_action == action
            return {"ok": True, "doc_id": doc_id, "new_status": "active" if action == "approve" else "deleted", "warnings": None}

    monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
    monkeypatch.setattr(rag_documents, "get_rag_pipeline", lambda: _Proxy())
    monkeypatch.setattr(rag_documents, "_require_authz", lambda _request: _Authorization())
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: _Registry())
    monkeypatch.setattr(rag_documents, "_extract_source", lambda _request: "test")
    monkeypatch.setattr(rag_documents, "_safe_log_op", MagicMock())

    result = asyncio.run(getattr(rag_documents, route_name)("dup-5", None))

    assert result["ok"] is True
    assert result["new_status"] == ("active" if action == "approve" else "deleted")


class _DeleteFakeRegistry:
    """delete 级联用：任意状态可删 + mark_deleted 语义。"""

    def __init__(self, doc_id: str, file_path: str, status: str = "active"):
        self.doc = {
            "doc_id": doc_id, "status": status,
            "file_path": file_path, "file_name": Path(file_path).name,
        }
        self.deleted_rows = 0

    def get_by_doc_id(self, doc_id: str):
        return dict(self.doc) if doc_id == self.doc["doc_id"] else None

    def mark_deleted_by_doc_id(self, doc_id: str) -> int:
        if doc_id != self.doc["doc_id"]:
            return 0
        self.deleted_rows += 1
        self.doc["status"] = "deleted"
        return 1


def test_delete_cascade_success_full_cleanup(tmp_path, monkeypatch):
    """删除级联：软删 + 向量/chunk_store/BM25 清理 + 源文件删除 + 残留校验通过。"""
    from backend.rag.indexing import review_service

    doc_id = "del-1"
    source = tmp_path / "target.md"
    source.write_text("content", encoding="utf-8")
    registry = _DeleteFakeRegistry(doc_id, str(source), status="active")
    pipeline = _FakePipeline(doc_id)
    chunk_store = _FakeChunkStore(doc_id)
    monkeypatch.setattr(review_service, "_get_chunk_store", lambda: chunk_store)
    monkeypatch.setattr(review_service, "_invalidate_review_cache", lambda: None)

    result = review_service.delete_document_cascade(
        doc_id, registry=registry, pipeline=pipeline,
    )

    assert result["ok"] is True
    assert result["degraded"] is False and result["warnings"] is None
    assert result["deleted_rows"] == 1
    assert registry.doc["status"] == "deleted"
    assert not source.exists()
    assert pipeline.vectordb.ids == [] and pipeline.doc_db.ids == []
    assert chunk_store.count_by_doc_id(doc_id) == 0


def test_delete_cascade_degrades_without_rollback(tmp_path, monkeypatch):
    """BM25 清理失败 → degraded 警告，但软删不回滚（delete 用户预期=删掉）。"""
    from backend.rag.indexing import review_service

    doc_id = "del-2"
    source = tmp_path / "target2.md"
    source.write_text("content", encoding="utf-8")
    registry = _DeleteFakeRegistry(doc_id, str(source), status="active")
    pipeline = _FakePipeline(doc_id, fail_bm25=True)
    chunk_store = _FakeChunkStore(doc_id)
    monkeypatch.setattr(review_service, "_get_chunk_store", lambda: chunk_store)
    monkeypatch.setattr(review_service, "_invalidate_review_cache", lambda: None)

    result = review_service.delete_document_cascade(
        doc_id, registry=registry, pipeline=pipeline,
    )

    assert result["ok"] is True
    assert result["degraded"] is True and result["warnings"]
    assert registry.doc["status"] == "deleted"  # 软删不回滚
    assert not source.exists()  # 文件清理不受 BM25 失败影响


def test_delete_cascade_missing_doc(tmp_path, monkeypatch):
    """文档不存在 → 明确失败，不动任何存储。"""
    from backend.rag.indexing import review_service

    pipeline = _FakePipeline("ghost")
    monkeypatch.setattr(review_service, "_get_chunk_store", lambda: _FakeChunkStore("x"))
    result = review_service.delete_document_cascade(
        "ghost", registry=_DeleteFakeRegistry("other", str(tmp_path / "x.md")),
        pipeline=pipeline,
    )
    assert result["ok"] is False and "不存在" in result["error"]
