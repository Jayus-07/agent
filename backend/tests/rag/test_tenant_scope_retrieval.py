"""RAG 检索链租户隔离回归（安全审计 #67/#60，2026-11-09）。

#67 rag.askchain.empty-tenant-no-predicate：
  ask 链空租户此前不产生任何租户谓词，跨租户语料可进入模型上下文。
  修复后 _prepare_context 空租户缺省 "default" 并强制谓词，与兄弟出口
  retrieve_documents/retrieve_knowledge 缺省形态一致；答案缓存 scope
  显式加入租户段（空租户调用不再共享缓存键）。

#60 rag-expansion-tenant-scope：
  adaptive 扩 K / parent / neighbor 扩展按 doc_id、chunk_id 直取存储，
  无租户谓词且收口集不含 tenant——跨租户同键行可绕过租户下推直达
  LLM 上下文。修复后三处扩展 get 补租户谓词下推，_stage2_postprocess
  与 neighbor 兜底统一租户后置收口（与 retrieve_documents._keep 同语义：
  缺 tenant 键的存量行按 default 归属）。

对照口径：双租户（tenant_a/tenant_b）+ 缺键 legacy 行三向互证。
"""
import pytest
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from types import SimpleNamespace

from backend.rag import context as rag_context
from backend.rag.pipeline import RAGPipeline
from backend.rag.retrieval.retrievers import (
    AdaptiveRetriever,
    ChunkLevelRetriever,
    _Staging,
    effective_tenant_id_from_filter,
)


@pytest.fixture(autouse=True)
def _active_review_registry(monkeypatch):
    """检索收口单测使用确定性 active Registry（同 test_retrievers 模式）。"""
    import backend.rag.retrieval.review_filter as review_filter
    from backend.rag.indexing import doc_registry

    class ActiveRegistry:
        def __init__(self, path):
            pass

        def list_by_statuses(self, statuses):
            return []

        def list_expired(self, now=None):
            return []

        def list_visible_doc_ids(self, doc_ids, now=None):
            return {doc_id for doc_id in (doc_ids or []) if doc_id}

    monkeypatch.setattr(doc_registry, "DocumentRegistry", ActiveRegistry)
    monkeypatch.setattr(review_filter, "_read_remote_version", lambda: None)
    review_filter._block_cache = {"ids": frozenset(), "ts": 0.0}
    review_filter._version_cache = {"value": None, "ts": 0.0, "last_seen": None}
    yield
    review_filter._block_cache = {"ids": frozenset(), "ts": 0.0}
    review_filter._version_cache = {"value": None, "ts": 0.0, "last_seen": None}


@pytest.fixture(autouse=True)
def _clear_context():
    rag_context.clear_context()
    yield
    rag_context.clear_context()


def _stub_router(monkeypatch, candidates):
    import backend.rag.routing.kb_router as kb_router_mod

    class FakeRouter:
        def route(self, question):
            return {"candidates": [{"kb_id": k} for k in candidates]}

    monkeypatch.setattr(kb_router_mod, "KBRouter", FakeRouter)


def _stub_analyzer(monkeypatch, qf=None):
    import backend.rag.retrieval.query_analyzer as qa_mod

    class FakeParsed:
        intent = "fact"

        def to_metadata_filter(self):
            return dict(qf or {})

    class FakeAnalyzer:
        def analyze(self, question):
            return FakeParsed()

    monkeypatch.setattr(qa_mod, "QueryAnalyzer", FakeAnalyzer)


def _mk_retriever(doc_db=None, vectordb=None):
    return ChunkLevelRetriever(
        doc_db=doc_db or SimpleNamespace(),
        vectordb=vectordb or SimpleNamespace(),
        chunk_retriever=SimpleNamespace(),
        bm25=SimpleNamespace(),
    )


# =====================================================
# #67：ask 链空租户谓词
# =====================================================

class TestPrepareContextTenantPredicate:
    def test_empty_tenant_defaults_to_default(self, monkeypatch):
        """无凭据对照：空租户（无声明、无上下文身份）→ 谓词缺省 default。"""
        _stub_router(monkeypatch, [])
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(object(), "*", "无法路由的问题")
        mf = rag_context.get_context().metadata_filter
        assert mf.get("tenant_id") == "default"

    def test_declared_tenant_passthrough(self, monkeypatch):
        """双租户对照：显式租户原样下推，不被缺省值覆写。"""
        _stub_router(monkeypatch, [])
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(
            object(), "*", "问题", tenant_id="tenant_a")
        mf = rag_context.get_context().metadata_filter
        assert mf.get("tenant_id") == "tenant_a"

    def test_graph_identity_tenant_borrowed(self, monkeypatch):
        """图路径绑定身份的租户被借读，调用方未传不降级 default。"""
        from backend.orchestration.request_context import RequestContext

        graph_ctx = RequestContext(
            session_id="s1", user_id="u1", tenant_id="tenant_a")
        graph_ctx.bind()
        _stub_router(monkeypatch, [])
        _stub_analyzer(monkeypatch, {})
        RAGPipeline._prepare_context(object(), "*", "问题")
        mf = rag_context.get_context().metadata_filter
        assert mf.get("tenant_id") == "tenant_a"


class TestEffectiveTenantHelper:
    def test_direct_and_and_wrapped_and_missing(self):
        assert effective_tenant_id_from_filter({"tenant_id": "tenant_a"}) == "tenant_a"
        assert effective_tenant_id_from_filter(
            {"$and": [{"kb_id": "k1"}, {"tenant_id": "tenant_b"}]}
        ) == "tenant_b"
        assert effective_tenant_id_from_filter({}) == "default"
        assert effective_tenant_id_from_filter(None) == "default"


# =====================================================
# #60：扩展收口租户后过滤
# =====================================================

def _tenant_docs():
    return [
        Document("a", metadata={"chunk_id": "c1", "doc_id": "d1",
                                "tenant_id": "tenant_a"}),
        Document("b", metadata={"chunk_id": "c2", "doc_id": "d1",
                                "tenant_id": "tenant_b"}),
        Document("legacy", metadata={"chunk_id": "c3", "doc_id": "d2"}),
    ]


class TestStage2TenantPostFilter:
    @pytest.fixture(autouse=True)
    def _no_adaptive(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.ADAPTIVE_RETRIEVAL_ENABLED", False)

    def test_postprocess_drops_foreign_tenant(self, monkeypatch):
        """双租户对照：tenant_a 请求剔除 tenant_b 行，缺键行按 default 归属剔除。"""
        import backend.rag.indexing.chunk_store as chunk_store_mod
        monkeypatch.setattr(
            chunk_store_mod, "get_chunk_store",
            lambda: (_ for _ in ()).throw(RuntimeError("no store")))

        st = _Staging(
            query="q", span=None,
            metadata_filter={"tenant_id": "tenant_a"})
        st.docs = _tenant_docs()
        _mk_retriever()._stage2_postprocess(st)
        tenants = {str(d.metadata.get("tenant_id")) for d in st.docs}
        assert tenants == {"tenant_a"}

    def test_postprocess_default_keeps_legacy_rows(self, monkeypatch):
        """default 请求保留缺键 legacy 行（pgvector 缺省归属语义对齐）。"""
        import backend.rag.indexing.chunk_store as chunk_store_mod
        monkeypatch.setattr(
            chunk_store_mod, "get_chunk_store",
            lambda: (_ for _ in ()).throw(RuntimeError("no store")))

        st = _Staging(query="q", span=None, metadata_filter={})
        st.docs = _tenant_docs()
        _mk_retriever()._stage2_postprocess(st)
        tenants = {str(d.metadata.get("tenant_id") or "default")
                   for d in st.docs}
        assert tenants == {"default"}

    def test_neighbor_fallback_drops_foreign_tenant(self, monkeypatch):
        """neighbor 兜底（绕过 _stage2_postprocess 的独立出口）同样收口。"""
        retriever = _mk_retriever(doc_db=SimpleNamespace(
            get=lambda where: {
                "documents": ["full-a", "full-b"],
                "metadatas": [
                    {"doc_id": "d1", "tenant_id": "tenant_a"},
                    {"doc_id": "d1", "tenant_id": "tenant_b"},
                ],
            }))
        st = _Staging(
            query="q", span=None, doc_ids=["d1"],
            metadata_filter={"tenant_id": "tenant_a"})
        result = retriever._stage2_neighbor_fallback(st)
        docs, _ = result
        assert docs is not None
        assert all(
            str(d.metadata.get("tenant_id")) == "tenant_a" for d in docs)


# =====================================================
# #60：扩展 get 租户谓词下推
# =====================================================

class _CaptureGet:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {"ids": [], "documents": [], "metadatas": []}

    def get(self, where=None):
        self.calls.append(where)
        return self.payload


class TestExpansionGetTenantPredicate:
    def test_neighbor_expansion_where_carries_tenant(self):
        """doc_id $in 直取的 where 必须携带租户谓词（tenant_a 双向对照）。"""
        capture = _CaptureGet()
        retriever = _mk_retriever(doc_db=capture)
        retriever._neighbor_expansion(
            "q", ["d1", "d2"], {"tenant_id": "tenant_a"})
        assert capture.calls, "neighbor expansion 未调用 doc_db.get"
        where = capture.calls[0]
        assert where.get("doc_id", {}).get("$in") == ["d1", "d2"]
        assert where.get("tenant_id") == "tenant_a"

    def test_neighbor_expansion_where_defaults_when_missing(self):
        """无凭据对照：租户缺失时谓词缺省 default 而非省略。"""
        capture = _CaptureGet()
        retriever = _mk_retriever(doc_db=capture)
        retriever._neighbor_expansion("q", ["d1"], {})
        assert capture.calls[0].get("tenant_id") == "default"

    def test_lookup_parents_where_carries_tenant(self, monkeypatch):
        """chunk_id $in 直取（chunk_store 不可用降级向量腿）同样带谓词。"""
        import backend.rag.indexing.chunk_store as chunk_store_mod
        monkeypatch.setattr(
            chunk_store_mod, "get_chunk_store",
            lambda: (_ for _ in ()).throw(RuntimeError("no store")))
        rag_context.set_context(rag_context.RagRequestState(
            metadata_filter={"tenant_id": "tenant_a"}, query="q"))
        capture = _CaptureGet()
        retriever = _mk_retriever(vectordb=capture)
        retriever._lookup_parents(["p1"])
        assert capture.calls, "parent lookup 未调用 vectordb.get"
        where = capture.calls[0]
        assert where.get("chunk_id", {}).get("$in") == ["p1"]
        assert where.get("tenant_id") == "tenant_a"


class _ClusterBaseRetriever(BaseRetriever):
    """全部 chunk 同 doc_id → 恒触发 Cluster Expansion。"""

    def _get_relevant_documents(self, query, *, run_manager=None):
        return [
            Document(f"chunk-{i}", metadata={
                "doc_id": "d1", "chunk_id": f"c{i}",
                "rerank_score": 0.9, "tenant_id": "tenant_a",
            })
            for i in range(3)
        ]


class TestAdaptiveExpansionTenant:
    def test_fulltext_replacement_filters_foreign_tenant(self):
        """双租户同 doc_id 对照：全文替换 get 带谓词，输出仅本租户全文。

        fake doc_db 不严格执行 where（返回双租户行），验证 full_doc_map
        的后置过滤仍兜底——跨租户全文不进入 LLM 上下文。
        """
        rag_context.set_context(rag_context.RagRequestState(
            metadata_filter={"tenant_id": "tenant_a"}, query="q"))
        capture = _CaptureGet(payload={
            "documents": ["full-a", "full-b"],
            "metadatas": [
                {"doc_id": "d1", "tenant_id": "tenant_a"},
                {"doc_id": "d1", "tenant_id": "tenant_b"},
            ],
        })
        retriever = AdaptiveRetriever(
            base_retriever=_ClusterBaseRetriever(), doc_db=capture)
        result = retriever.invoke("q")
        assert capture.calls, "cluster expansion 未调用 doc_db.get"
        assert capture.calls[0].get("tenant_id") == "tenant_a"
        fulltexts = [d for d in result if d.metadata.get("chunk_id") is None]
        assert len(fulltexts) == 1
        assert fulltexts[0].page_content == "full-a"
        assert fulltexts[0].metadata.get("tenant_id") == "tenant_a"


# =====================================================
# #67 次生面：答案缓存 scope 租户隔离
# =====================================================

class TestAnswerCacheScopeTenant:
    def test_scope_includes_tenant_segment(self):
        scope = RAGPipeline._authorization_scope(SimpleNamespace(
            subject_type="employee", department="hr",
            permissions=None, tenant_id="tenant_a"))
        assert scope.endswith(":tenant_a")

    def test_scope_empty_tenant_defaults(self):
        """空租户与显式 default 的 scope 相同、与 tenant_a 不同（缓存隔离）。"""
        empty = RAGPipeline._authorization_scope(SimpleNamespace(
            subject_type="employee", department="hr",
            permissions=None, tenant_id=""))
        explicit = RAGPipeline._authorization_scope(SimpleNamespace(
            subject_type="employee", department="hr",
            permissions=None, tenant_id="default"))
        other = RAGPipeline._authorization_scope(SimpleNamespace(
            subject_type="employee", department="hr",
            permissions=None, tenant_id="tenant_a"))
        assert empty == explicit
        assert empty != other
