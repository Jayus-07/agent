"""审核可见性公共出口回归（Batch 1A，2026-10-12）。

契约（审计 P0-2 / P1-3 收口）：
- pending_review ∪ deprecated ∪ 过期 active 文档在全部普通检索出口不可见：
  hybrid_retrieve（ask 链）、retrieve_knowledge（/retrieve 及内部消费）、
  retrieve_documents（/rag/search、/retrieve_docs）。
- fail-closed：Registry 不可用 → ReviewVisibilityUnavailableError，检索显式
  失败——绝不跳过过滤、绝不伪装成正常空结果（修复原 hybrid fail-open）。
- 跨进程失效：invalidate 广播 Redis 版本号，检索方节流轮询感知；Redis
  不可用时退回集合 TTL 兜底（正确性不依赖 Redis）。
"""
import pytest
from langchain_core.documents import Document

import backend.rag.retrieval.review_filter as rf
from backend.rag.retrieval.hybrid import hybrid_retrieve
from backend.rag.retrieval.review_filter import (
    ReviewVisibilityUnavailableError,
    filter_review_blocked,
    get_review_blocked_doc_ids,
    invalidate_pending_review_cache,
)


def _doc(doc_id: str, content: str = "正文") -> Document:
    return Document(page_content=content, metadata={"doc_id": doc_id})


@pytest.fixture(autouse=True)
def _reset_cache():
    rf._block_cache = {"ids": frozenset(), "ts": 0.0}
    rf._version_cache = {"value": None, "ts": 0.0, "last_seen": None}
    yield
    rf._block_cache = {"ids": frozenset(), "ts": 0.0}
    rf._version_cache = {"value": None, "ts": 0.0, "last_seen": None}


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """单测环境默认无 Redis：版本信号退化为 TTL 兜底，不触发真实连接。"""

    def _fail():
        raise ConnectionError("no redis in unit test")

    monkeypatch.setattr("backend.infra.redis.client.get_redis", _fail)


def _patch_registry(monkeypatch, *, rows=(), expired=(), error=None):
    """替换 DocumentRegistry 构造入口（公共模块内为函数内 import，patch 生效）。"""
    from backend.rag.indexing import doc_registry as dr

    class FakeRegistry:
        def __init__(self, path):
            type(self).instances.append(self)

        instances: list = []

        def list_by_statuses(self, statuses):
            if error:
                raise error
            assert set(statuses) == {"pending_review", "deprecated"}
            return list(rows)

        def list_expired(self, now=None):
            if error:
                raise error
            return list(expired)

    FakeRegistry.instances = []
    monkeypatch.setattr(dr, "DocumentRegistry", lambda path: FakeRegistry(path))
    return FakeRegistry


class TestBlockedIds:

    def test_reads_registry(self, monkeypatch):
        _patch_registry(
            monkeypatch,
            rows=[{"doc_id": "d1"}, {"doc_id": "d2"}, {"doc_id": ""}],
            expired=[{"doc_id": "d3"}],
        )
        ids = get_review_blocked_doc_ids()
        assert ids == frozenset({"d1", "d2", "d3"})  # 空 doc_id 被剔除，过期并入

    def test_cache_prevents_repeat_query(self, monkeypatch):
        reg = _patch_registry(monkeypatch, rows=[{"doc_id": "d1"}])
        get_review_blocked_doc_ids()
        get_review_blocked_doc_ids()
        assert len(reg.instances) == 1, "TTL 内不重复查库"

    def test_registry_down_fail_closed(self, monkeypatch):
        """fail-closed：Registry 故障必须显式失败，不得返回空集合放行。"""
        _patch_registry(monkeypatch, error=RuntimeError("pg down"))
        with pytest.raises(ReviewVisibilityUnavailableError):
            get_review_blocked_doc_ids()

    def test_redis_unavailable_falls_back_to_ttl(self, monkeypatch):
        """Redis 不可用 → 版本信号退化为 TTL 兜底，集合获取不受影响。"""
        _patch_registry(monkeypatch, rows=[{"doc_id": "d1"}])
        assert get_review_blocked_doc_ids() == frozenset({"d1"})

    def test_version_signal_invalidates_cache(self, monkeypatch):
        """跨进程失效：版本号变化后集合缓存立即重建（不等 TTL）。"""
        rows = [{"doc_id": "old"}]

        reg = _patch_registry(monkeypatch, rows=rows)
        assert get_review_blocked_doc_ids() == frozenset({"old"})

        # 模拟另一进程：新文档进入 pending_review（原地改，闭包可见）并 INCR 版本号
        rows.append({"doc_id": "new"})
        rf._version_cache["ts"] = 0.0  # 版本轮询节流到期
        rf._version_cache["last_seen"] = 1
        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis",
            lambda: type("R", (), {"get": lambda self, k: b"2"})(),
        )

        assert get_review_blocked_doc_ids() == frozenset({"old", "new"})
        assert len(reg.instances) == 2, "版本变化立即重建，未等 30s TTL"

    def test_invalidate_broadcasts_and_clears(self, monkeypatch):
        incr = {"n": 0}

        class FakeRedis:
            def incr(self, key):
                incr["n"] += 1
                return 1

        monkeypatch.setattr(
            "backend.infra.redis.client.get_redis", lambda: FakeRedis())
        reg = _patch_registry(monkeypatch, rows=[{"doc_id": "d1"}])
        get_review_blocked_doc_ids()
        invalidate_pending_review_cache()
        assert incr["n"] == 1, "审批/下线方广播跨进程失效信号"
        get_review_blocked_doc_ids()
        assert len(reg.instances) == 2, "invalidate 后本进程立即重建缓存"


class TestFilterReviewBlocked:

    def test_blocks_pending_review_docs(self, monkeypatch):
        monkeypatch.setattr(rf, "get_review_blocked_doc_ids",
                            lambda: frozenset({"blocked"}))
        docs = [_doc("ok1"), _doc("blocked"), _doc("ok2")]
        out = filter_review_blocked(docs)
        assert [d.metadata["doc_id"] for d in out] == ["ok1", "ok2"]

    def test_empty_blocked_set_noop(self, monkeypatch):
        monkeypatch.setattr(rf, "get_review_blocked_doc_ids", lambda: frozenset())
        docs = [_doc("a"), _doc("b")]
        out = filter_review_blocked(docs)
        assert out is docs, "无待审文档时原样返回（零开销路径）"

    def test_docs_without_metadata_dropped_if_blocked_unknown(self, monkeypatch):
        monkeypatch.setattr(rf, "get_review_blocked_doc_ids",
                            lambda: frozenset({"x"}))
        plain = "纯字符串"
        out = filter_review_blocked([plain])
        assert out == [plain], "无 metadata 的对象按不属于 blocked 处理"

    def test_registry_down_propagates(self, monkeypatch):
        """过滤入口同样 fail-closed：不吞异常、不返回部分结果。"""
        def _boom():
            raise ReviewVisibilityUnavailableError("pg down")

        monkeypatch.setattr(rf, "get_review_blocked_doc_ids", _boom)
        with pytest.raises(ReviewVisibilityUnavailableError):
            filter_review_blocked([_doc("a")])


class TestHybridRetrieveWrapper:

    def test_wrapper_filters_impl_result(self, monkeypatch):
        """ask 链路包装层对 _hybrid_retrieve_impl 的全部出口生效。"""
        monkeypatch.setattr(rf, "get_review_blocked_doc_ids",
                            lambda: frozenset({"bad"}))

        def fake_impl(query, vr, br, k=5, doc_ids=None, rrf_k=60,
                      metadata_filter=None, expanded_queries=None):
            return [_doc("good"), _doc("bad")]

        import backend.rag.retrieval.hybrid as hybrid_mod
        monkeypatch.setattr(hybrid_mod, "_hybrid_retrieve_impl", fake_impl)
        out = hybrid_retrieve("q", None, None)
        assert [d.metadata["doc_id"] for d in out] == ["good"]

    def test_wrapper_fail_closed(self, monkeypatch):
        """ask 链路同样 fail-closed：Registry 故障向上抛而非放行。"""
        def _boom():
            raise ReviewVisibilityUnavailableError("pg down")

        monkeypatch.setattr(rf, "get_review_blocked_doc_ids", _boom)
        import backend.rag.retrieval.hybrid as hybrid_mod
        monkeypatch.setattr(
            hybrid_mod, "_hybrid_retrieve_impl",
            lambda *a, **k: [_doc("any")])
        with pytest.raises(ReviewVisibilityUnavailableError):
            hybrid_retrieve("q", None, None)


class _FakeContextPipeline:
    """绕过 __init__ 的最小 pipeline 骨架（沿用 runtime_modes 测试模式）。"""

    def __init__(self, module, docs):
        self._module = module
        self.bm25 = None  # BM25 腿软降级路径覆盖不到，专注向量腿可见性
        self.chunk_retriever = type(
            "R", (),
            {
                "_docs": docs,
                "retrieve": lambda self, q, k=5, metadata_filter=None:
                    list(self._docs),
                "vectordb": type(
                    "V", (),
                    {
                        "_docs": docs,
                        "similarity_search_with_score":
                            lambda self, q, k=10, filter=None:
                                [(d, 0.5) for d in self._docs],
                    },
                )(),
            },
        )()

    def ensure_retrieval_index_fresh(self):
        pass

    def _prepare_context(self, *a, **k):
        pass

    def _cleanup(self):
        pass

    def _doc_matches_filter(self, meta, filt):
        return True

    def _constrain_filter_to_authorized(self, mf, authorized):
        return dict(mf or {})


class TestPipelineExits:
    """pipeline 两个公共检索出口的可见性接线（/retrieve、/rag/search 消费）。"""

    @pytest.fixture(autouse=True)
    def _no_kb_authz(self, monkeypatch):
        """屏蔽 KB 授权矩阵（1A 只验证审核可见性维度，授权有独立测试）。"""
        monkeypatch.setattr(
            "backend.rag.authz.retrieval_authorized_kbs",
            lambda *a, **k: None)

    def _pipeline(self, module, docs):
        return _FakeContextPipeline(module, docs)

    def test_retrieve_knowledge_hides_pending(self, monkeypatch):
        from backend.rag import pipeline as module

        monkeypatch.setattr(rf, "get_review_blocked_doc_ids",
                            lambda: frozenset({"blocked"}))
        p = self._pipeline(module, [_doc("ok"), _doc("blocked")])
        text = module.RAGPipeline.retrieve_knowledge(
            p, "q", kb_id="kb", subject_type="employee", department="general")
        assert text.count("正文") == 1, "pending 文档正文不得进入结果"

    def test_retrieve_knowledge_fail_closed(self, monkeypatch):
        from backend.rag import pipeline as module

        def _boom():
            raise ReviewVisibilityUnavailableError("pg down")

        monkeypatch.setattr(rf, "get_review_blocked_doc_ids", _boom)
        p = self._pipeline(module, [_doc("ok")])
        with pytest.raises(ReviewVisibilityUnavailableError):
            module.RAGPipeline.retrieve_knowledge(
                p, "q", kb_id="kb", subject_type="employee", department="general")

    def test_retrieve_documents_hides_pending(self, monkeypatch):
        from backend.rag import pipeline as module

        monkeypatch.setattr(rf, "get_review_blocked_doc_ids",
                            lambda: frozenset({"blocked"}))
        p = self._pipeline(module, [_doc("ok"), _doc("blocked")])
        docs = module.RAGPipeline.retrieve_documents(
            p, "q", kb_id="kb", subject_type="employee", department="general")
        assert [d.metadata["doc_id"] for d in docs] == ["ok"]

    def test_retrieve_documents_fail_closed(self, monkeypatch):
        from backend.rag import pipeline as module

        def _boom():
            raise ReviewVisibilityUnavailableError("pg down")

        monkeypatch.setattr(rf, "get_review_blocked_doc_ids", _boom)
        p = self._pipeline(module, [_doc("ok")])
        with pytest.raises(ReviewVisibilityUnavailableError):
            module.RAGPipeline.retrieve_documents(
                p, "q", kb_id="kb", subject_type="employee", department="general")
