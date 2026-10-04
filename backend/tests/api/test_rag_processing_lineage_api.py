"""RAG 文档处理模型血缘 API 契约。"""

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
    monkeypatch.setattr(rag_documents, "_require_authz", lambda _request: _Authz())
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
