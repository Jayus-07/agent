"""BM25 持久化存储测试 — BM25Store 构建/加载/增量/删除/过期检查。"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document


def _make_docs(count: int, prefix: str = "doc") -> list:
    """创建测试用 Document 列表。"""
    docs = []
    for i in range(count):
        docs.append(Document(
            page_content=f"{prefix}_{i} 这是第 {i} 个测试文档的内容。" + " 测试 " * (i + 1),
            metadata={"doc_id": f"{prefix}_{i}", "index": i},
        ))
    return docs


# ======================= Fixtures =======================

@pytest.fixture
def index_dir():
    """创建临时索引目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_docs():
    """创建 10 个测试文档。"""
    return _make_docs(10)


# ======================= TestBuildAndLoad =======================

class TestBuildAndLoad:
    """构建与加载基本功能测试。"""

    def test_build_creates_files(self, index_dir, sample_docs):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        retriever = store.build(sample_docs, k=5)

        # 验证返回的 retriever 可用
        assert retriever is not None
        assert retriever.k == 5

        # 验证 3 个持久化文件存在
        assert os.path.exists(os.path.join(index_dir, "corpus.pkl"))
        assert os.path.exists(os.path.join(index_dir, "docs.pkl"))
        assert os.path.exists(os.path.join(index_dir, "meta.json"))

    def test_load_restores_retriever(self, index_dir, sample_docs):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(sample_docs, k=10)

        # 新建 store 实例模拟重启，加载索引
        store2 = BM25Store(index_dir=index_dir)
        retriever = store2.load(k=10)

        assert retriever is not None
        assert retriever.k == 10
        # 检索应返回结果
        results = retriever.invoke("测试文档")
        assert len(results) > 0

    def test_load_nonexistent_returns_none(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        retriever = store.load()
        assert retriever is None

    def test_meta_content(self, index_dir, sample_docs):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(sample_docs, k=5)

        meta = store._read_meta()
        assert meta["doc_count"] == 10
        assert meta["version"] == 1
        assert "build_time_s" in meta
        assert "built_at" in meta
        assert meta["build_time_s"] >= 0  # 极快构建可能四舍五入为 0.0


# ======================= TestSearch =======================

class TestSearch:
    """检索准确性测试。"""

    def test_search_finds_relevant(self, index_dir):
        from retrieval.bm25_store import BM25Store

        docs = [
            Document(page_content="Python 编程语言入门教程", metadata={"doc_id": "d1"}),
            Document(page_content="Java 面向对象编程指南", metadata={"doc_id": "d2"}),
            Document(page_content="Python 机器学习实战", metadata={"doc_id": "d3"}),
            Document(page_content="美食烹饪大全", metadata={"doc_id": "d4"}),
        ]

        store = BM25Store(index_dir=index_dir)
        store.build(docs, k=3)

        retriever = store.load(k=3)
        results = retriever.invoke("Python 编程")

        # 应优先返回包含 "Python" 的文档
        assert len(results) > 0
        top_doc_ids = [d.metadata["doc_id"] for d in results[:2]]
        assert "d1" in top_doc_ids or "d3" in top_doc_ids

    def test_search_respects_k(self, index_dir):
        from retrieval.bm25_store import BM25Store

        docs = _make_docs(20)
        store = BM25Store(index_dir=index_dir)
        store.build(docs, k=3)

        retriever = store.load(k=3)
        results = retriever.invoke("测试")
        assert len(results) <= 3


# ======================= TestAddDocuments =======================

class TestAddDocuments:
    """增量添加文档测试。"""

    def test_add_documents_increases_count(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(_make_docs(5, "original"), k=5)

        assert store.doc_count() == 5

        new_docs = _make_docs(3, "new")
        store.add_documents(new_docs, k=5)

        assert store.doc_count() == 8

    def test_add_documents_is_searchable(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        original = [Document(page_content="原始文档内容", metadata={"doc_id": "orig_1"})]
        store.build(original, k=5)

        new_docs = [Document(page_content="新增的独特关键词 独角兽", metadata={"doc_id": "new_1"})]
        store.add_documents(new_docs, k=5)

        retriever = store.load(k=5)
        results = retriever.invoke("独角兽")
        assert len(results) > 0
        assert results[0].metadata["doc_id"] == "new_1"


# ======================= TestRemoveDocuments =======================

class TestRemoveDocuments:
    """按 doc_id 删除文档测试。"""

    def test_remove_reduces_count(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(_make_docs(10, "keep"), k=5)
        assert store.doc_count() == 10

        store.remove_documents(["keep_0", "keep_1", "keep_2"], k=5)
        assert store.doc_count() == 7

    def test_remove_nonexistent_noop(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(_make_docs(5, "keep"), k=5)
        count_before = store.doc_count()

        store.remove_documents(["nonexistent_id"], k=5)
        assert store.doc_count() == count_before

    def test_remove_no_index_noop(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        # 没有构建索引就删除：不报错
        result = store.remove_documents(["some_id"], k=5)
        assert result is None

    def test_remove_all_then_load_returns_none(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        doc_ids = [f"doc_{i}" for i in range(5)]
        docs = [Document(page_content=f"content {i}", metadata={"doc_id": did}) for i, did in enumerate(doc_ids)]
        store.build(docs, k=5)
        assert store.doc_count() == 5

        store.remove_documents(doc_ids, k=5)
        # 全部删除 → doc_count = 0，索引文件被清理
        assert store.doc_count() == 0
        assert store.load() is None


# ======================= TestIsStale =======================

class TestIsStale:
    """过期检查测试。"""

    def test_no_index_is_stale(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        assert store.is_stale is True

    def test_built_index_not_stale(self, index_dir, sample_docs):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(sample_docs, k=5)
        assert store.is_stale is False

    def test_empty_docs_is_stale(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build([], k=5)
        assert store.is_stale is True


# ======================= TestDocCount =======================

class TestDocCount:
    """文档计数测试。"""

    def test_empty_index_count_zero(self, index_dir):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        assert store.doc_count() == 0

    def test_count_matches_built(self, index_dir, sample_docs):
        from retrieval.bm25_store import BM25Store

        store = BM25Store(index_dir=index_dir)
        store.build(sample_docs, k=5)
        assert store.doc_count() == len(sample_docs)
