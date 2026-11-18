"""RAG 文档处理模型血缘 API 契约。"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_rag_user
from backend.app.api.routes import rag_documents


class _Identity:
    actor = "user:test"
    role = "admin"
    kind = "user"


class _Repository:
    def list_runs(self, doc_id, **kwargs):
        return {"items": [{"run_id": "run-1", "doc_id": doc_id}], "total": 1,
                "page": kwargs.get("page", 1), "page_size": kwargs.get("page_size", 20)}

    def get_run_detail(self, doc_id, run_id):
        return {"run_id": run_id, "doc_id": doc_id, "steps": []}


class _Authz:
    def can_read_row(self, _doc):
        return True


class _Registry:
    def get_by_doc_id(self, doc_id):
        return {"doc_id": doc_id, "status": "active"}


class _CandidateRegistry:
    def get_by_doc_id(self, _doc_id):
        return None


def test_processing_lineage_routes_return_runs_and_detail(monkeypatch):
    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: _Identity()
    # 路由已收口到请求级 Principal/RagAuthorization；测试同时隔离
    # 授权与 registry 外部边界，避免依赖本机登录头和真实 PG 数据。
    async def _require_authz(_request):
        return _Authz()

    monkeypatch.setattr(rag_documents, "_require_authz", _require_authz)
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: _Registry())
    monkeypatch.setattr(
        rag_documents,
        "get_processing_lineage_repository",
        lambda: _Repository(),
        raising=False,
    )
    client = TestClient(app)

    runs = client.get("/documents/doc-1/processing-runs").json()
    detail = client.get("/documents/doc-1/processing-runs/run-1").json()

    assert runs["items"][0]["run_id"] == "run-1"
    assert detail["run_id"] == "run-1"


def test_unpublished_candidate_run_detail_is_authorized_and_labeled(monkeypatch):
    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: _Identity()
    authz = SimpleNamespace(
        principal=SimpleNamespace(tenant_id="tenant-a"),
        kb_scope={"kb-test"},
        can_read_row=lambda row: row.get("kb_id") == "kb-test",
    )

    async def _require_authz(_request):
        return authz

    captured = {}

    class _CandidateRepository:
        def get_candidate_run_scope(self, doc_id, run_id, **kwargs):
            captured["scope"] = (doc_id, run_id, kwargs)
            return {
                "run_id": run_id,
                "doc_id": doc_id,
                "file_hash": "a" * 64,
                "trace_id": "trace-1",
                "kb_id": "kb-test",
                "tenant_id": "tenant-a",
                "department": "general",
                "permission_scope": "general",
            }

        def get_run_detail(self, doc_id, run_id):
            captured["detail"] = (doc_id, run_id)
            return {
                "run_id": run_id,
                "doc_id": doc_id,
                "file_hash": "a" * 64,
                "trace_id": "trace-1",
                "steps": [],
            }

    monkeypatch.setattr(rag_documents, "_require_authz", _require_authz)
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: _CandidateRegistry())
    monkeypatch.setattr(
        rag_documents,
        "get_processing_lineage_repository",
        lambda: _CandidateRepository(),
    )
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(by_processing_run=lambda _run_id: []),
    )
    client = TestClient(app)

    response = client.get("/documents/doc-1/processing-runs/run-1")

    assert response.status_code == 200
    detail = response.json()
    assert detail["publication_status"] == "unpublished"
    assert detail["document_registered"] is False
    assert detail["file_hash"] == "a" * 64
    assert detail["trace_id"] == "trace-1"
    assert detail["kb_id"] == "kb-test"
    assert captured["scope"] == (
        "doc-1",
        "run-1",
        {"tenant_id": "tenant-a", "kb_ids": ["kb-test"]},
    )
    assert captured["detail"] == ("doc-1", "run-1")


def test_unpublished_candidate_run_detail_hides_out_of_scope_candidate(monkeypatch):
    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: _Identity()
    authz = SimpleNamespace(
        principal=SimpleNamespace(tenant_id="tenant-a"),
        kb_scope={"kb-visible"},
        can_read_row=lambda _row: False,
    )

    async def _require_authz(_request):
        return authz

    class _CandidateRepository:
        def get_candidate_run_scope(self, _doc_id, _run_id, **_kwargs):
            return {
                "run_id": "run-1",
                "doc_id": "doc-1",
                "kb_id": "kb-hidden",
                "tenant_id": "tenant-a",
            }

        def get_run_detail(self, *_args):
            raise AssertionError("未授权运行不得读取详情")

    monkeypatch.setattr(rag_documents, "_require_authz", _require_authz)
    monkeypatch.setattr(rag_documents, "_get_registry", lambda: _CandidateRegistry())
    monkeypatch.setattr(
        rag_documents,
        "get_processing_lineage_repository",
        lambda: _CandidateRepository(),
    )
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: SimpleNamespace(by_processing_run=lambda _run_id: []),
    )
    client = TestClient(app)

    response = client.get("/documents/doc-1/processing-runs/run-1")

    assert response.status_code == 200
    assert response.json() == {
        "doc_id": "doc-1", "run_id": "run-1", "error": "文档不存在",
    }


def test_llm_chunking_fallback_stats_route_uses_visible_scope(monkeypatch):
    app = FastAPI()
    app.include_router(rag_documents.router)
    app.dependency_overrides[require_rag_user] = lambda: _Identity()
    authz = SimpleNamespace(
        principal=SimpleNamespace(tenant_id="tenant-a", department="support"),
        kb_scope={"kb-a"},
        is_admin=False,
        user_permissions=("support",),
    )

    async def _require_authz(_request):
        return authz

    captured = {}

    class _StatsRepository:
        def chunking_fallback_stats(self, **kwargs):
            captured.update(kwargs)
            return {
                "count_unit": "processing_run",
                "candidate_count": 2,
                "llm_call_count": 1,
                "applied_count": 1,
            }

    monkeypatch.setattr(rag_documents, "_require_authz", _require_authz)
    monkeypatch.setattr(
        rag_documents, "_get_registry",
        lambda: (_ for _ in ()).throw(AssertionError("统计查询不应依赖文档登记表")),
    )
    monkeypatch.setattr(
        rag_documents,
        "get_processing_lineage_repository",
        lambda: _StatsRepository(),
    )
    monkeypatch.setenv("RAG_LLM_CHUNK_RULE_REVIEW_THRESHOLD", "3")
    client = TestClient(app)

    response = client.get("/chunking/llm-fallback-stats?days=14")

    assert response.status_code == 200
    assert response.json()["count_unit"] == "processing_run"
    assert response.json()["candidate_count"] == 2
    assert captured == {
        "tenant_id": "tenant-a",
        "kb_ids": ["kb-a"],
        "user_permissions": ["support"],
        "is_admin": False,
        "department": "support",
        "days": 14,
        "review_threshold": 3,
    }
