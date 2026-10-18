"""RAGPipeline runtime/evaluation 只读模式契约。"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document


def test_evaluation_mode_does_not_sync(monkeypatch):
    from backend.rag import pipeline as module

    calls: list[str] = []
    monkeypatch.setattr(module, "get_embedding", lambda: object())
    monkeypatch.setattr(
        module.RAGPipeline,
        "_load_existing_db",
        lambda self, path, kind: object(),
    )
    monkeypatch.setattr(
        module.RAGPipeline,
        "_init_vector_dbs_incremental",
        lambda self: calls.append("sync"),
    )
    monkeypatch.setattr(module.RAGPipeline, "_init_retrievers", lambda self: None)

    module.RAGPipeline(mode="evaluation")

    assert calls == []


def test_runtime_mode_never_builds_bm25(monkeypatch):
    from backend.rag import pipeline as module

    pipeline = module.RAGPipeline.__new__(module.RAGPipeline)
    pipeline.mode = "runtime"
    pipeline.embedding = object()
    pipeline.vectordb = type(
        "Vector",
        (),
        {
            "get": lambda self: {
                "ids": ["c1"],
                "documents": ["text"],
                "metadatas": [{}],
            }
        },
    )()
    pipeline.doc_db = object()

    class ReadOnlyBM25Store:
        def load(self, k):
            return None

        def build(self, *args, **kwargs):
            raise AssertionError("runtime mode attempted BM25 write")

    monkeypatch.setattr(module, "BM25Store", ReadOnlyBM25Store)

    with pytest.raises(RuntimeError, match="BM25"):
        pipeline._init_retrievers()


@pytest.mark.parametrize("mode", ["runtime", "evaluation"])
def test_read_only_modes_reject_vector_sync(mode):
    from backend.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.mode = mode

    with pytest.raises(RuntimeError, match="禁止.*向量库"):
        pipeline._init_vector_dbs_incremental()
    with pytest.raises(RuntimeError, match="禁止.*向量库"):
        pipeline._init_vector_dbs_full()


def test_index_mode_can_skip_implicit_sync_for_explicit_import(monkeypatch):
    from backend.rag import pipeline as module

    calls: list[str] = []
    monkeypatch.setattr(module, "get_embedding", lambda: object())
    monkeypatch.setattr(
        module.RAGPipeline,
        "_load_existing_db",
        lambda self, path, kind: object(),
    )
    monkeypatch.setattr(
        module.RAGPipeline,
        "_init_vector_dbs_incremental",
        lambda self: calls.append("sync"),
    )
    monkeypatch.setattr(module.RAGPipeline, "_init_retrievers", lambda self: None)

    module.RAGPipeline(mode="index", auto_sync=False)

    assert calls == []


def test_unordered_bm25_hash_accepts_vector_store_return_order():
    from backend.rag.retrieval.bm25_store import compute_content_hash_unordered

    docs = [
        Document(page_content="a", metadata={"source_file": "one.md"}),
        Document(page_content="b", metadata={"source_file": "two.md"}),
    ]

    assert compute_content_hash_unordered(docs) == compute_content_hash_unordered(
        list(reversed(docs))
    )


class _FakeRetriever:
    def __init__(self, docs):
        self.docs = docs


class TestReadOnlySnapshotSelfHeal:
    """N-1（2026-10-05 全量 503 事故）：只读门快照校验失败时先从 canonical
    向量源重建一次再复检；复检仍不一致才拒绝启动，且错误带可执行恢复命令。

    事故链：删除级联只改内存 BM25，磁盘快照滞后 → 重启集合/hash 校验失败
    直接 raise → rag-service 拒启 40 分钟直至人工重建。"""

    def _mk_pipeline(self, module, monkeypatch, store):
        pipeline = module.RAGPipeline.__new__(module.RAGPipeline)
        pipeline.mode = "runtime"
        pipeline.embedding = object()
        pipeline.doc_db = object()
        pipeline._person_to_doc_cache = {}  # __new__ 跳过 __init__，补 person index 桩
        pipeline.vectordb = type(
            "Vector", (),
            {"get": lambda self: {
                "ids": ["c1"], "documents": ["text"], "metadatas": [{}]}},
        )()
        monkeypatch.setattr(module, "BM25Store", lambda: store)
        mismatch = {"v": True}
        store.mismatch = mismatch
        monkeypatch.setattr(
            module, "source_files_out_of_sync", lambda a, b: mismatch["v"])
        monkeypatch.setattr(module, "compute_content_hash", lambda docs: "h-canonical")
        monkeypatch.setattr(
            module, "compute_content_hash_unordered",
            lambda docs: "u-nempty" if docs else "u-empty")
        return pipeline, mismatch

    def test_stale_snapshot_self_heals_via_one_rebuild(self, monkeypatch):
        from backend.rag import pipeline as module

        class SelfHealingStore:
            built = 0
            _count = 0
            _hash = "h-drift"
            mismatch = {"v": True}

            def load(self, k):
                return _FakeRetriever([])

            @property
            def is_stale(self):
                return self._count == 0

            def doc_count(self):
                return self._count

            def get_content_hash(self):
                return self._hash

            def get_metadata(self):
                return {}

            def build(self, docs, k=None, metadata=None):
                SelfHealingStore.built += 1
                SelfHealingStore._count = len(docs)
                SelfHealingStore._hash = "h-canonical"
                self.mismatch["v"] = False  # 重建后与 canonical 集合一致
                return _FakeRetriever(list(docs))

        store = SelfHealingStore()
        pipeline, _ = self._mk_pipeline(module, monkeypatch, store)

        pipeline._init_retrievers()  # 不抛 = 自愈成功

        assert store.built == 1, "必须恰好重建一次（N-1 自愈）"
        assert pipeline.bm25.docs

    def test_unhealable_snapshot_raises_with_recovery_command(self, monkeypatch):
        from backend.rag import pipeline as module

        class BrokenStore:
            def load(self, k):
                return _FakeRetriever([])

            @property
            def is_stale(self):
                return True  # 重建后仍 stale（模拟 canonical 源不可用）

            def doc_count(self):
                return 0

            def get_content_hash(self):
                return "h-drift"

            def get_metadata(self):
                return {}

            def build(self, docs, k=None, metadata=None):
                return _FakeRetriever(list(docs))

        pipeline, _ = self._mk_pipeline(module, monkeypatch, BrokenStore())

        with pytest.raises(RuntimeError, match="reconcile"):
            pipeline._init_retrievers()


class TestColdStartEmptyKB:
    """空库冷启动放行（2026-10-19 ragu1 隔离栈首传实证）。

    全新部署向量库无 chunk 且无已发布 BM25 快照是合法状态：首批文档发布前
    检索返回空，发布时由 indexer 建立 BM25。此前 runtime 初始化无条件拒绝
    「向量库没有可用于 BM25 校验的 chunk」，新库首个上传任务重试 3 次终败
    （service_unavailable）——鸡生蛋死锁。防混代语义保留：向量库空但磁盘
    BM25 快照非空仍视为向量数据丢失并拒绝。"""

    def _mk_pipeline(self, module, monkeypatch, store):
        pipeline = module.RAGPipeline.__new__(module.RAGPipeline)
        pipeline.mode = "runtime"
        pipeline.embedding = object()
        pipeline.doc_db = object()
        pipeline._person_to_doc_cache = {}  # __new__ 跳过 __init__，补 person index 桩
        pipeline.vectordb = type(
            "Vector", (),
            {"get": lambda self: {"ids": [], "documents": [], "metadatas": []}},
        )()
        monkeypatch.setattr(module, "BM25Store", lambda: store)
        return pipeline

    def test_empty_vectors_and_empty_snapshot_boots_with_empty_index(
            self, monkeypatch):
        from backend.rag import pipeline as module

        built = {"n": 0}

        class ColdStore:
            def load(self, k):
                return None

            def doc_count(self):
                return 0

            def get_content_hash(self):
                return ""

            def get_metadata(self):
                return {}

            def build(self, *args, **kwargs):
                built["n"] += 1
                return _FakeRetriever([])

        pipeline = self._mk_pipeline(module, monkeypatch, ColdStore())

        pipeline._init_retrievers()  # 不抛 = 冷启动放行

        assert pipeline.bm25 is None
        assert built["n"] == 0, "只读模式冷启动不得构建 BM25"

    def test_vectors_lost_but_snapshot_present_still_rejected(self, monkeypatch):
        from backend.rag import pipeline as module

        class OrphanSnapshotStore:
            def load(self, k):
                return _FakeRetriever([Document(page_content="orphan")])

            @property
            def is_stale(self):
                return False

            def doc_count(self):
                return 1

            def get_content_hash(self):
                return "h-orphan"

            def get_metadata(self):
                return {}

        pipeline = self._mk_pipeline(module, monkeypatch, OrphanSnapshotStore())

        with pytest.raises(RuntimeError, match="向量库没有可用于 BM25 校验"):
            pipeline._init_retrievers()
