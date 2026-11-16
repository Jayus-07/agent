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
