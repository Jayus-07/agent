"""原文预览端点用户级鉴权回归（2026-10-03 实机验收 P0 修复）。

缺陷：`GET /rag/documents/{doc_id}/file` 在 RAG_MODE=remote 部署下，remote
分支只带内部令牌转发 rag-service（服务间信任，无用户上下文），用户级
can_read_row 裁决只写在 local 分支 → 任何登录用户可读任意
permission_scope 受限文档（实机 uitest_user 读 hr_confidential 文档
200 复现）。

修复契约（对齐 review/delete 的「归属校验先于任何模式分支」军规）：
- registry 行存在性 + active + can_read_row 裁决必须发生在 remote/local
  分支之前，任一不满足 → 404（与不存在同形，不泄露存在性）；
- remote 分支在裁决通过后才允许转发；转发非 200 按快照缺失 404。

本测试直接调用生产路由协程（鉴权次序是被测对象本身），monkeypatch
_require_authz/_get_registry/get_rag_pipeline/RAG_MODE 四个依赖。
"""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from backend.app.api.routes import rag_documents


class _StubAuthz:
    def __init__(self, can_read: bool):
        self._can_read = can_read
        self.is_admin = False
        self.user_permissions = None
        self.principal = types.SimpleNamespace(permissions=None, roles=("viewer",))

    def can_read_row(self, row) -> bool:
        return self._can_read


class _StubRegistry:
    def __init__(self, doc: dict | None):
        self._doc = doc
        self.calls = 0

    def get_by_doc_id(self, doc_id: str):
        self.calls += 1
        return dict(self._doc) if self._doc and doc_id == self._doc.get("doc_id") else None


class _StubPipeline:
    def __init__(self):
        self.fetch_calls: list[str] = []

    def fetch_document_file(self, doc_id: str):
        self.fetch_calls.append(doc_id)
        return 200, b"%PDF-stub", "application/pdf"


_RESTRICTED_DOC = {
    "doc_id": "doc-1",
    "status": "active",
    "kb_id": "policy_general",
    "permission_scope": "hr_confidential",
    "file_path": "/app/data/docs/policy_general/general/secret.pdf",
}


@pytest.fixture()
def route_env(monkeypatch):
    """patch 路由的四个依赖，返回可切换的桩集合。"""
    authz = _StubAuthz(can_read=False)
    registry = _StubRegistry(_RESTRICTED_DOC)
    pipeline = _StubPipeline()
    monkeypatch.setattr(rag_documents, "_require_authz", lambda request: authz)
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: registry)
    monkeypatch.setattr(rag_documents, "get_rag_pipeline", lambda: pipeline)
    env = types.SimpleNamespace(authz=authz, registry=registry, pipeline=pipeline)
    yield env


def _request() -> types.SimpleNamespace:
    return types.SimpleNamespace(headers={}, client=None)


@pytest.mark.asyncio
async def test_remote_restricted_doc_denied_before_forward(route_env, monkeypatch):
    """回归主用例：remote 模式下无权用户不得触达 rag-service（此前 200 缺陷）。"""
    monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
    with pytest.raises(HTTPException) as exc:
        await rag_documents.preview_document_file("doc-1", _request())
    assert exc.value.status_code == 404
    # 关键断言：请求绝不得到达 rag-service（修复前会转发并返回 200）
    assert route_env.pipeline.fetch_calls == []


@pytest.mark.asyncio
async def test_remote_authorized_doc_forwards(route_env, monkeypatch):
    """remote 模式：裁决通过才转发，返回字节流。"""
    route_env.authz._can_read = True
    monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
    resp = await rag_documents.preview_document_file("doc-1", _request())
    assert resp.status_code == 200
    assert route_env.pipeline.fetch_calls == ["doc-1"]


@pytest.mark.asyncio
async def test_remote_missing_doc_denied_without_forward(route_env, monkeypatch):
    """remote 模式：不存在文档 404，不转发（不泄露存在性）。"""
    monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
    with pytest.raises(HTTPException) as exc:
        await rag_documents.preview_document_file("no-such-doc", _request())
    assert exc.value.status_code == 404
    assert route_env.pipeline.fetch_calls == []


@pytest.mark.asyncio
async def test_remote_inactive_doc_denied(route_env, monkeypatch):
    """remote 模式：非 active（软删/待审）文档一律 404。"""
    route_env.authz._can_read = True
    route_env.registry._doc = {**_RESTRICTED_DOC, "status": "pending_review"}
    monkeypatch.setattr("backend.config.rag.RAG_MODE", "remote")
    with pytest.raises(HTTPException) as exc:
        await rag_documents.preview_document_file("doc-1", _request())
    assert exc.value.status_code == 404
    assert route_env.pipeline.fetch_calls == []


@pytest.mark.asyncio
async def test_local_restricted_doc_denied(route_env, monkeypatch):
    """local 模式：同一裁决先行（两分支共用一次归属校验）。"""
    monkeypatch.setattr("backend.config.rag.RAG_MODE", "local")
    with pytest.raises(HTTPException) as exc:
        await rag_documents.preview_document_file("doc-1", _request())
    assert exc.value.status_code == 404
