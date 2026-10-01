"""BM25 身份收口与跨进程热刷新单元测试（2026-10-01 C 阶段）。

钉死口径：
  - BM25 条目身份 = doc_id（+vector_id），basename 匹配废除——跨 KB 同名
    文件的删除/替换互不影响；
  - 一致性对账按 {doc_id: chunk_count}（旧 basename 口径跨库同名合并计数）；
  - 热刷新：代次变化 → 整组引用一次性替换（pipeline + lc_chain +
    chunk_retriever_base）；加载失败保留旧快照并显式 stale。
"""

import json

import pytest
from langchain_core.documents import Document

from backend.rag.retrieval.bm25_store import (
    BM25_META_VERSION,
    BM25Store,
    _doc_matches,
    doc_id_counts,
    source_files_out_of_sync,
)


def _doc(doc_id: str, content: str, source: str) -> Document:
    return Document(page_content=content,
                    metadata={"doc_id": doc_id, "source_file": source})


# ── 身份口径 ──


def test_doc_matches_ignores_basename_even_across_kb():
    """跨 KB 同名文件：按 doc_id 删除绝不误伤另一库的同名条目（C 阶段验收）。"""
    kb_a = _doc("doc-aaa", "A 库制度正文", "制度.pdf")
    kb_b = _doc("doc-bbb", "B 库制度正文", "制度.pdf")
    # 旧签名兼容：传入 basenames 也只对 doc_id 生效
    assert _doc_matches(kb_a, {"doc-aaa"}, {"制度.pdf"})
    assert not _doc_matches(kb_b, {"doc-aaa"}, {"制度.pdf"}), (
        "basename 兜底匹配会跨 KB 误删同名文件条目（已废除）")


def test_doc_id_counts_and_sync_check():
    docs_a = [_doc("d1", "x", "制度.pdf"), _doc("d1", "y", "制度.pdf"),
              _doc("d2", "z", "制度.pdf")]
    docs_b = [_doc("d1", "x", "制度.pdf"), _doc("d2", "z", "制度.pdf"),
              _doc("d2", "z2", "制度.pdf")]
    # basename 口径下两者完全同形（旧实现误判一致）；doc_id 口径可分辨
    assert doc_id_counts(docs_a) != doc_id_counts(docs_b)
    assert source_files_out_of_sync(docs_a, docs_b)
    assert not source_files_out_of_sync(docs_a, list(docs_a))


def test_meta_version_bumped_for_identity_rebuild():
    assert BM25_META_VERSION >= 3, "身份收口必须触发一次 canonical 重建"


# ── 发布指针 ──


def test_published_generation_reads_pointer(tmp_path):
    store = BM25Store(index_dir=str(tmp_path / "bm25"))
    assert store.published_generation() == ""
    (tmp_path / "bm25").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bm25" / "PUBLISHED.json").write_text(
        json.dumps({"generation": "gen-abc"}), encoding="utf-8")
    assert store.published_generation() == "gen-abc"


def test_published_generation_falls_back_to_meta(tmp_path):
    d = tmp_path / "bm25"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"generation": "gen-meta"}),
                                 encoding="utf-8")
    assert BM25Store(index_dir=str(d)).published_generation() == "gen-meta"


def test_build_writes_pointer_with_generation(tmp_path):
    store = BM25Store(index_dir=str(tmp_path / "bm25"))
    store.build([_doc("d1", "正文内容", "a.md")], k=3,
                metadata={"generation": "gen-1"})
    pointer = json.loads((tmp_path / "bm25" / "PUBLISHED.json").read_text("utf-8"))
    assert pointer["generation"] == "gen-1"
    assert pointer["doc_count"] == 1
    assert store.published_generation() == "gen-1"


# ── 热刷新（整组引用替换）──


class _FakeRetriever:
    def __init__(self):
        self.bm25 = None
        self.person_index = {}


class _FakeChain:
    def __init__(self):
        self.bm25 = None
        self.person_index = {}
        self.chunk_retriever_base = _FakeRetriever()


class _RefreshPipeline:
    """最小 pipeline 替身：只实现热刷新协议消费的成员。"""

    def __init__(self, store: BM25Store, docs):
        self.mode = "runtime"
        self.bm25_store = store
        self.bm25 = store.load(k=3)
        self._person_to_doc_cache = {"占位": ["d0"]}
        self.person_index = dict(self._person_to_doc_cache)
        self.lc_chain = _FakeChain()
        self.lc_chain.bm25 = self.bm25
        self.lc_chain.person_index = self.person_index
        self.lc_chain.chunk_retriever_base.bm25 = self.bm25
        self._loaded_bm25_generation = store.published_generation()
        self._refresh_lock = __import__("threading").Lock()
        self.is_index_stale = False

    def _build_person_index(self):
        return {"占位": ["d0"]}

    # 热刷新原语（与 RAGPipeline 同一实现，从 pipeline 摘取协议测试）
    ensure_retrieval_index_fresh = None  # 由 bind 注入


def _bind_ensure(pipeline):
    from backend.rag.pipeline import RAGPipeline

    pipeline.ensure_retrieval_index_fresh = lambda: RAGPipeline.ensure_retrieval_index_fresh(
        pipeline)


def test_hot_refresh_swaps_whole_reference_group(tmp_path):
    """发布新代次后：不重启进程，pipeline/chain/retriever 三层引用同步换新。"""
    store = BM25Store(index_dir=str(tmp_path / "bm25"))
    docs_v1 = [_doc("d1", "旧版本正文内容", "a.md")]
    store.build(docs_v1, k=3, metadata={"generation": "gen-1"})
    pipe = _RefreshPipeline(store, docs_v1)
    _bind_ensure(pipe)
    old_bm25 = pipe.bm25
    assert pipe._loaded_bm25_generation == "gen-1"

    # Worker 发布 gen-2（新词可命中）
    docs_v2 = docs_v1 + [_doc("d2", "新增术语热刷新", "b.md")]
    store.build(docs_v2, k=3, metadata={"generation": "gen-2"})

    pipe.ensure_retrieval_index_fresh()

    assert pipe._loaded_bm25_generation == "gen-2"
    new_bm25 = pipe.bm25
    assert new_bm25 is not old_bm25
    # 整组引用：chain 与 chunk_retriever_base 都不再是旧对象
    assert pipe.lc_chain.bm25 is new_bm25
    assert pipe.lc_chain.chunk_retriever_base.bm25 is new_bm25
    assert pipe.lc_chain.person_index is pipe.person_index
    assert pipe.lc_chain.chunk_retriever_base.person_index is pipe.person_index
    assert not pipe.is_index_stale
    # 新词可命中、旧内容保留
    hits = [d.metadata.get("doc_id") for d in new_bm25.invoke("热刷新")]
    assert "d2" in hits


def test_hot_refresh_same_generation_is_noop(tmp_path):
    store = BM25Store(index_dir=str(tmp_path / "bm25"))
    store.build([_doc("d1", "正文", "a.md")], k=3, metadata={"generation": "gen-1"})
    pipe = _RefreshPipeline(store, [])
    _bind_ensure(pipe)
    old = pipe.bm25
    pipe.ensure_retrieval_index_fresh()
    assert pipe.bm25 is old


def test_hot_refresh_load_failure_keeps_old_and_marks_stale(tmp_path):
    """新代次加载失败：保留旧快照继续服务 + 显式 stale（不静默谎报可检索）。"""
    store = BM25Store(index_dir=str(tmp_path / "bm25"))
    store.build([_doc("d1", "旧正文", "a.md")], k=3, metadata={"generation": "gen-1"})
    pipe = _RefreshPipeline(store, [])
    _bind_ensure(pipe)
    old_bm25 = pipe.bm25

    store.build([_doc("d2", "新正文", "b.md")], k=3, metadata={"generation": "gen-2"})
    # 破坏全部产物（bundle + 旧格式回退文件），使 load 无可用快照
    for name in ("index.pkl", "corpus.pkl", "docs.pkl"):
        (tmp_path / "bm25" / name).write_bytes(b"corrupted-not-a-pickle")

    pipe.ensure_retrieval_index_fresh()

    assert pipe.bm25 is old_bm25, "加载失败必须保留旧快照"
    assert pipe.is_index_stale is True, "必须显式标记索引未就绪"
    assert pipe.lc_chain.bm25 is old_bm25
