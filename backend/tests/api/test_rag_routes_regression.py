"""RAG 路由回归（2026-10-04，验收清单 K3/K5）。

- K3：根 POST /rag 的 body 字段是 question（RAGAskRequest schema），端点
  此前读 req.query → AttributeError 恒 500。回归断言：question 透传到
  pipeline.ask 且 200。
- K5：GET /documents/{id} 详情补暴露 summary（上传链路已生成的 LLM 摘要）。

只 mock 外部边界：pipeline（LLM/检索）、registry（PG）、authz 与身份依赖。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import (
    require_admin_user,
    require_rag_editor,
    require_rag_user,
)
from backend.app.api.routes import rag as rag_route
from backend.app.api.routes import rag_documents
from backend.app.api.routes import rag_upload


class StubPipeline:
    def __init__(self):
        self.ask_calls: list[tuple[str, dict]] = []

    def ask(self, question, **kwargs):
        self.ask_calls.append((question, kwargs))
        return "知识库答案"


def _client(app: FastAPI) -> TestClient:
    app.dependency_overrides[require_rag_user] = lambda: SimpleNamespace(
        user_id="1", authenticated=True)
    return TestClient(app)


def _admin_client(app: FastAPI) -> TestClient:
    app.dependency_overrides[require_rag_user] = lambda: SimpleNamespace(
        user_id="1", authenticated=True)
    app.dependency_overrides[require_admin_user] = lambda: SimpleNamespace(
        role="admin", actor="user:1")
    app.dependency_overrides[rag_documents.require_kb_catalog_super_admin] = lambda: SimpleNamespace(
        role="super_admin", actor="user:1")
    return TestClient(app)


def _async_value(value):
    async def _return(*_args, **_kwargs):
        return value
    return _return


def test_root_ask_reads_question_field(monkeypatch):
    """K3 回归：body 用 question 字段必须可达 pipeline.ask（修复前恒 500）。"""
    stub = StubPipeline()
    monkeypatch.setattr(rag_route, "require_rag_ready", lambda: None)
    monkeypatch.setattr(rag_route, "get_rag_pipeline", lambda: stub)
    monkeypatch.setattr(
        rag_route, "require_principal",
        lambda request: SimpleNamespace(
            subject_type="employee", department="", permissions=None),
    )
    app = FastAPI()
    app.include_router(rag_route.router)

    resp = _client(app).post(
        "/rag", json={"question": "三坊七巷有什么特色？", "session_id": "t1"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "知识库答案"
    assert body["query"] == "三坊七巷有什么特色？"
    assert stub.ask_calls and stub.ask_calls[0][0] == "三坊七巷有什么特色？"


def test_root_ask_passes_kb_id_through(monkeypatch):
    """D-10 回归：契约字段 kb_id 必须透传 pipeline.ask（修复前被丢弃恒查 default）。"""
    stub = StubPipeline()
    monkeypatch.setattr(rag_route, "require_rag_ready", lambda: None)
    monkeypatch.setattr(rag_route, "get_rag_pipeline", lambda: stub)
    monkeypatch.setattr(
        rag_route, "require_principal",
        lambda request: SimpleNamespace(
            subject_type="employee", department="", permissions=None),
    )
    app = FastAPI()
    app.include_router(rag_route.router)

    resp = _client(app).post(
        "/rag", json={"question": "三坊七巷有什么特色？", "kb_id": "travel"})

    assert resp.status_code == 200, resp.text
    kwargs = stub.ask_calls[0][1]
    assert kwargs.get("kb_id") == "travel"
    assert resp.json()["kb_id"] == "travel"


def test_document_detail_exposes_summary(monkeypatch):
    """K5 回归：详情 DTO 含 summary 字段（库内有值即回显）。"""
    row = {
        "doc_id": "d1", "file_name": "福州-景点-三坊七巷.md", "kb_id": "travel",
        "status": "active", "summary": "三坊七巷是福州历史文化街区。",
        "chunk_count": 3, "version_id": "travel-v2", "doc_version": 2,
    }

    class StubReg:
        def get_by_doc_id(self, doc_id):
            return dict(row) if doc_id == "d1" else None

    class StubAuthz:
        def can_read_row(self, doc):
            return (True, "")

    monkeypatch.setattr(rag_documents, "_get_registry", lambda: StubReg())
    monkeypatch.setattr(rag_documents, "_require_authz", _async_value(StubAuthz()))
    app = FastAPI()
    app.include_router(rag_documents.router)

    resp = _client(app).get("/documents/d1")

    assert resp.status_code == 200, resp.text
    assert resp.json()["doc"]["summary"] == "三坊七巷是福州历史文化街区。"
    assert resp.json()["doc"]["version_id"] == "travel-v2"
    assert resp.json()["doc"]["doc_version"] == 2


def test_document_detail_hides_unauthorized_doc_with_404(monkeypatch):
    """S6/API4：越权详情与不存在文档同形返回 404。"""
    class StubReg:
        def get_by_doc_id(self, doc_id):
            return {"doc_id": doc_id, "status": "active", "kb_id": "policy_finance"}

    class StubAuthz:
        def can_read_row(self, doc):
            return False

    monkeypatch.setattr(rag_documents, "_get_registry", lambda: StubReg())
    monkeypatch.setattr(rag_documents, "_require_authz", _async_value(StubAuthz()))
    app = FastAPI()
    app.include_router(rag_documents.router)

    resp = _client(app).get("/documents/foreign-doc")

    assert resp.status_code == 404
    assert resp.json() == {"ok": False, "error": "文档不存在"}


def test_pending_list_passes_principal_tenant_to_registry(monkeypatch):
    captured = {}

    class StubReg:
        def list_pending_review(self, **kwargs):
            captured.update(kwargs)
            return {
                "items": [{
                    "doc_id": "pending-a", "tenant_id": "tenant-a",
                    "kb_id": "policy_hr", "department": "hr",
                    "permission_scope": "general", "status": "pending_review",
                    "file_name": "a.md",
                }],
                "total": 1, "page": 1, "page_size": 20,
            }

    class StubAuthz:
        principal = SimpleNamespace(tenant_id="tenant-a", department="hr")
        kb_scope = {"policy_hr"}
        is_admin = False

        def can_manage_row(self, row):
            return row.get("tenant_id") == self.principal.tenant_id, ""

    monkeypatch.setattr(rag_documents, "_get_registry", lambda: StubReg())
    monkeypatch.setattr(rag_documents, "_require_authz", _async_value(StubAuthz()))
    app = FastAPI()
    app.include_router(rag_documents.router)

    resp = _client(app).get("/pending")

    assert resp.status_code == 200, resp.text
    assert captured["tenant_id"] == "tenant-a"
    assert resp.json()["total"] == 1


def test_kb_authority_counts_only_the_authenticated_tenant(monkeypatch):
    captured = []

    class StubRegistry:
        def count_by_kb_id(self, kb_id, *, tenant_id):
            captured.append((kb_id, tenant_id))
            return 2

    identity = SimpleNamespace(tenant_id="tenant-a", authenticated=True)
    monkeypatch.setattr(rag_documents, "_kb_management_identity", lambda _request: identity)
    monkeypatch.setattr(
        "backend.rag.knowledge_base_store.list_knowledge_bases",
        lambda *, include_inactive: [{"kb_id": "kb-hr", "name": "HR"}],
    )
    monkeypatch.setattr(
        "backend.rag.knowledge_base_store.list_departments",
        lambda _tenant_id, *, include_inactive: [],
    )
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: StubRegistry())

    response = asyncio.run(rag_documents.kb_authority_overview(SimpleNamespace()))

    assert captured == [("kb-hr", "tenant-a")]
    assert response["knowledge_bases"][0]["doc_count"] == 2


def test_upload_failure_list_uses_resource_scope_for_items_and_count(monkeypatch):
    from backend.app.api import identity

    principal = SimpleNamespace(
        tenant_id="tenant-a", user_id="user-a", department="hr",
    )
    authz = SimpleNamespace(
        is_admin=False,
        kb_scope=frozenset({"kb-hr", "kb-finance"}),
        can_upload_to=lambda kb_id, department: (
            kb_id == "kb-hr" and department == "hr",
            "",
        ),
    )
    monkeypatch.setattr(identity, "require_principal", lambda _request: principal)
    monkeypatch.setattr(
        rag_upload.RagAuthorization,
        "build",
        lambda _principal: authz,
    )

    captured = {}

    class StubStore:
        def list_pending_failures(self, **kwargs):
            captured["list"] = kwargs
            return []

        def count_pending_failures(self, **kwargs):
            captured["count"] = kwargs
            return 0

    monkeypatch.setattr(
        "backend.rag.indexing.index_run_store_pg.get_index_run_store",
        lambda: StubStore(),
    )

    response = asyncio.run(
        rag_upload.list_upload_failures(SimpleNamespace(), limit=25),
    )

    expected_scope = {
        "tenant_id": "tenant-a",
        "kb_ids": ["kb-hr"],
        "department": "hr",
    }
    assert captured["list"] == {"limit": 25, **expected_scope}
    assert captured["count"] == expected_scope
    assert response == {"ok": True, "total": 0, "items": []}


def test_remote_delete_disconnect_reports_unknown_state(monkeypatch):
    """远端删除断连不能回成 HTTP 200/失败，必须显式标记结果待核对。"""
    from backend.config import rag as rag_config

    class StubRegistry:
        def get_by_doc_id(self, doc_id):
            return {"doc_id": doc_id, "file_name": "制度.pdf", "file_path": ""}

    class StubAuthz:
        principal = SimpleNamespace(user_id="operator-1", tenant_id="tenant-a")

        def can_manage_row(self, doc):
            return True, ""

    class StubPipeline:
        def delete_document_cascade(self, doc_id):
            raise RuntimeError("RAG 删除级联失败: Server disconnected")

    logs = []
    monkeypatch.setattr(rag_config, "RAG_MODE", "remote")
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: StubRegistry())
    monkeypatch.setattr(rag_documents, "_require_authz", _async_value(StubAuthz()))
    monkeypatch.setattr(rag_documents, "get_rag_pipeline", lambda: StubPipeline())
    monkeypatch.setattr(
        rag_documents, "_safe_log_op",
        lambda *args, **kwargs: logs.append(kwargs),
    )

    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: None
    app.dependency_overrides[require_rag_editor] = lambda: None

    response = TestClient(app).delete("/documents/doc-1")

    assert response.status_code == 502
    assert response.json()["ok"] is False
    assert response.json()["code"] == "delete_outcome_unknown"
    assert response.json()["operation_state"] == "unknown"
    assert "Server disconnected" in response.json()["error"]
    assert logs[-1]["result"] == "unknown"
    assert logs[-1]["detail"]["operation_state"] == "unknown"
    assert logs[-1]["tenant_id"] == "tenant-a"


def test_remote_delete_missing_document_returns_http_404(monkeypatch):
    """rag-service 明确拒绝删除时按失败返回 404，不能把业务错误当成功。"""
    from backend.config import rag as rag_config

    class StubRegistry:
        def get_by_doc_id(self, doc_id):
            return {"doc_id": doc_id, "file_name": "制度.pdf", "file_path": ""}

    class StubAuthz:
        principal = SimpleNamespace(user_id="operator-1", tenant_id="tenant-a")

        def can_manage_row(self, doc):
            return True, ""

    class StubPipeline:
        def delete_document_cascade(self, doc_id):
            return {"ok": False, "error": "文档不存在"}

    monkeypatch.setattr(rag_config, "RAG_MODE", "remote")
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: StubRegistry())
    monkeypatch.setattr(rag_documents, "_require_authz", _async_value(StubAuthz()))
    monkeypatch.setattr(rag_documents, "get_rag_pipeline", lambda: StubPipeline())
    monkeypatch.setattr(rag_documents, "_safe_log_op", lambda *args, **kwargs: None)

    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: None
    app.dependency_overrides[require_rag_editor] = lambda: None

    response = TestClient(app).delete("/documents/doc-1")

    assert response.status_code == 404
    assert response.json()["ok"] is False
    assert response.json()["operation_state"] == "failed"


def test_ask_response_carries_answer_meta(monkeypatch):
    """A5 回归：/ask 响应带 answer_meta（拒答时含 answer_status 稳定码）。"""
    from backend.app.api.routes import rag_search
    from backend.rag.pipeline import AskOutcome

    stub = SimpleNamespace(
        ask_result=lambda *a, **k: AskOutcome(
            answer="知识库暂无相关资料。",
            sources=[],
            answer_meta={"answer_status": "rag_no_evidence", "source_count": 0},
        ),
    )
    monkeypatch.setattr(rag_search, "get_rag_pipeline", lambda: stub)
    monkeypatch.setattr(
        rag_search, "require_principal",
        lambda request: SimpleNamespace(
            user_id="1", user_name="tester", tenant_id="default", department="",
            roles=("super_admin",), permissions=None, subject_type="employee",
            authenticated=True, auth_type="jwt", source="header"),
    )
    app = FastAPI()
    app.include_router(rag_search.router)

    resp = _client(app).post(
        "/ask", json={"question": "于山的开放时间？", "kb_id": "travel"})

    assert resp.status_code == 200, resp.text
    meta = resp.json().get("answer_meta")
    assert meta and meta.get("answer_status") == "rag_no_evidence"


def test_snapshot_answer_meta_merges_ctx_answer_status(monkeypatch):
    """A5 根因回归：_snapshot_answer_meta 必须并入 ctx.meta 的 answer_status
    （修复前只拷 chain._last_meta，稳定码到不了 answer_meta）。"""
    from backend.rag import context as rag_context
    from backend.rag.pipeline import RAGPipeline

    class FakeChain:
        _last_meta = {"can_answer": False}
        _last_sources: list = []

    class FakeSelf:
        lc_chain = FakeChain()
        last_answer_meta = {}

    monkeypatch.setattr(
        rag_context, "get_context",
        lambda: SimpleNamespace(meta={"answer_status": "rag_no_evidence"}),
    )

    fake = FakeSelf()
    RAGPipeline._snapshot_answer_meta(fake)

    assert fake.last_answer_meta.get("answer_status") == "rag_no_evidence"


def test_search_rejects_unsupported_top_k_and_filter():
    """API3：未纳入契约的 top_k/filter 不得被 Pydantic 静默忽略。"""
    from pydantic import ValidationError
    from backend.app.api.routes._rag_shared import SearchRequest

    for payload in ({"query": "x", "top_k": 0},
                    {"query": "x", "filter": {"department": "finance"}}):
        try:
            SearchRequest.model_validate(payload)
        except ValidationError:
            continue
        raise AssertionError("非法搜索参数必须被 schema 层拒绝")


def test_search_exposes_citation_version_chain(monkeypatch):
    """A3：REST search 结果必须透传引用版本链字段。"""
    from langchain_core.documents import Document
    from backend.app.api.routes import rag_search

    class SearchPipeline:
        is_index_stale = False

        def retrieve_documents(self, *args, **kwargs):
            return [Document(
                page_content="v1 evidence",
                metadata={
                    "doc_id": "d1", "chunk_id": "c1", "kb_id": "travel",
                    "source_file": "guide.md", "version_id": "v1",
                    "supersedes_version_id": "v0", "score": 0.91,
                },
            )]

    monkeypatch.setattr(rag_search, "get_rag_pipeline", lambda: SearchPipeline())
    monkeypatch.setattr(
        rag_search, "require_principal",
        lambda request: SimpleNamespace(
            user_id="1", user_name="tester", tenant_id="default", department="",
            roles=("super_admin",), permissions=None, subject_type="employee",
            authenticated=True, auth_type="jwt", source="header"),
    )
    class Authz:
        def can_search_kb(self, kb_id):
            return True

        def can_read_row(self, row):
            return True

    monkeypatch.setattr(rag_search.RagAuthorization, "build", lambda principal: Authz())
    app = FastAPI()
    app.include_router(rag_search.router)

    resp = _client(app).post("/search", json={"query": "guide", "kb_id": "travel"})
    assert resp.status_code == 200, resp.text
    metadata = resp.json()["results"][0]["metadata"]
    assert metadata["version_id"] == "v1"
    assert metadata["supersedes_version_id"] == "v0"


def test_create_knowledge_base_accepts_multiple_active_departments(monkeypatch):
    from backend.rag import knowledge_base_store

    captured = {}
    monkeypatch.setattr(
        rag_documents,
        "_kb_management_identity",
        lambda _request: SimpleNamespace(
            authenticated=True, tenant_id="tenant-a", user_id="u-1"),
    )
    monkeypatch.setattr(
        knowledge_base_store,
        "list_active_departments",
        lambda _tenant: [
            {"id": "hr", "label": "人事部", "active": True},
            {"id": "finance", "label": "财务部", "active": True},
        ],
    )

    def create_record(**kwargs):
        captured.update(kwargs)
        return {"kb_id": kwargs["kb_id"], "version": 0, "active": True}

    monkeypatch.setattr(knowledge_base_store, "create_knowledge_base", create_record)
    app = FastAPI()
    app.include_router(rag_documents.router)

    response = _admin_client(app).post("/knowledge-bases", json={
        "kb_id": "shared_policy",
        "name": "共享制度库",
        "domain": "policy",
        "owner_depts": ["hr", "finance"],
        "audience": "internal",
    })

    assert response.status_code == 201, response.text
    assert captured["owner_depts"] == ["hr", "finance"]
    assert captured["tenant_id"] == "tenant-a"


def test_tenant_admin_cannot_write_platform_kb_catalog(monkeypatch):
    from backend.app.api.deps import require_user_actor
    from backend.security import events

    monkeypatch.setattr(events, "record_security_event", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: SimpleNamespace(
        user_id="1", authenticated=True)

    async def tenant_admin():
        return SimpleNamespace(role="admin", actor="user:1")

    app.dependency_overrides[require_user_actor] = tenant_admin
    response = _client(app).post("/knowledge-bases", json={
        "kb_id": "tenant_policy",
        "name": "租户制度库",
        "domain": "policy",
        "owner_depts": ["hr"],
    })

    assert response.status_code == 403
    assert response.json()["detail"] == "知识库目录修改仅限 super_admin"


def test_create_knowledge_base_rejects_inactive_department(monkeypatch):
    from backend.rag import knowledge_base_store

    monkeypatch.setattr(
        rag_documents,
        "_kb_management_identity",
        lambda _request: SimpleNamespace(
            authenticated=True, tenant_id="tenant-a", user_id="u-1"),
    )
    monkeypatch.setattr(
        knowledge_base_store,
        "list_active_departments",
        lambda _tenant: [{"id": "hr", "label": "人事部", "active": True}],
    )
    create_called = False

    def create_record(**_kwargs):
        nonlocal create_called
        create_called = True

    monkeypatch.setattr(knowledge_base_store, "create_knowledge_base", create_record)
    app = FastAPI()
    app.include_router(rag_documents.router)

    response = _admin_client(app).post("/knowledge-bases", json={
        "kb_id": "shared_policy",
        "name": "共享制度库",
        "domain": "policy",
        "owner_depts": ["finance"],
    })

    assert response.status_code == 400
    assert "已停用" in response.json()["detail"]
    assert not create_called
