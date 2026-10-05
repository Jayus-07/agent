"""RAG 路由回归（2026-10-04，验收清单 K3/K5）。

- K3：根 POST /rag 的 body 字段是 question（RAGAskRequest schema），端点
  此前读 req.query → AttributeError 恒 500。回归断言：question 透传到
  pipeline.ask 且 200。
- K5：GET /documents/{id} 详情补暴露 summary（上传链路已生成的 LLM 摘要）。

只 mock 外部边界：pipeline（LLM/检索）、registry（PG）、authz 与身份依赖。
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_rag_user
from backend.app.api.routes import rag as rag_route
from backend.app.api.routes import rag_documents


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
    monkeypatch.setattr(rag_documents, "_require_authz", lambda request: StubAuthz())
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
    monkeypatch.setattr(rag_documents, "_require_authz", lambda request: StubAuthz())
    app = FastAPI()
    app.include_router(rag_documents.router)

    resp = _client(app).get("/documents/foreign-doc")

    assert resp.status_code == 404
    assert resp.json() == {"ok": False, "error": "文档不存在"}


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
