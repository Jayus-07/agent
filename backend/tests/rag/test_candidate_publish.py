"""候选发布协议单元测试（2026-10-01 B 阶段，纯 fake 无 PG 依赖）。

钉死契约：候选不可见、旧版不动、成功后发布、失败自动恢复；同逻辑文档
任意时刻至多一个发布者（输者明确 superseded）；重试不得重复发布。
"""

import os

import pytest

from backend.rag.indexing.publish import (
    BufferingChunkStore,
    CandidatePublishError,
    CandidateStores,
    CompositeVectorStore,
    SupersededCandidate,
    publish_candidate,
    switch_bm25_snapshot,
)


# ── fakes ──


class FakeRunStore:
    def __init__(self):
        self.status = {}

    def mark_status(self, upload_id, status, stage="", error=""):
        self.status[upload_id] = status
        return {"upload_id": upload_id, "status": status}


class FakeRegistry:
    """register_published 的 CAS 语义内存实现。"""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.register_calls = 0

    def get_by_path(self, file_path):
        return self.rows.get(file_path)

    def register_published(self, *, file_path, active_generation,
                           expected_base_generation, **kw):
        self.register_calls += 1
        row = self.rows.get(file_path)
        if row is None:
            self.rows[file_path] = {"active_generation": active_generation, **kw}
            return 1
        if row.get("active_generation") == expected_base_generation:
            row.update(kw)
            row["active_generation"] = active_generation
            return 1
        return 0

    def bump_doc_version(self, doc_id, delta=1):
        return 2


class FakeVectorStore:
    def __init__(self, name):
        self._collection = name
        self.promoted: list[str] = []
        self.deleted_ids: list[list[str]] = []
        self.metadata_updates: list[tuple] = []

    def clone_for_candidate(self, generation):
        return FakeVectorStore(f"{self._collection}::cand:{generation}")

    def promote_collection(self, source_collection):
        self.promoted.append(source_collection)
        return 1

    def delete(self, ids=None, where=None):
        if ids:
            self.deleted_ids.append(list(ids))
        return len(ids or [])

    def update_metadata_where(self, where, metadata_update):
        self.metadata_updates.append((where, metadata_update))
        return 1

    def drop_collection(self, collection):
        return 0


class FakeBM25Main:
    def __init__(self):
        self.switched = []

    def adopt_snapshot(self, cand_store, generation):
        self.switched.append(generation)
        return {"generation": generation}


class FakeChunkStoreReal:
    def __init__(self):
        self.flushed: dict[str, int] = {}
        self.deleted: list[str] = []

    def delete_by_doc_id(self, doc_id):
        self.deleted.append(doc_id)
        return 0

    def insert_batch(self, doc_id, rows):
        self.flushed[doc_id] = len(rows)
        return len(rows)


def _stores(gen="g1", with_bm25_artifacts=False, tmp_path=None):
    main_v = FakeVectorStore("chroma")
    main_d = FakeVectorStore("doc_db")
    cand_v = FakeVectorStore("chroma::cand:" + gen)
    cand_d = FakeVectorStore("doc_db::cand:" + gen)
    bm25_main = FakeBM25Main()
    buffer = BufferingChunkStore()
    buffer.insert_batch("doc-1", [{"chunk_index": 0, "content": "x"}])
    index_result = {
        "chunk_ids": ["c1", "c2"],
        "doc_db_id": "doc::new",
        "registry_metadata": {"doc_type": "general"},
        "staging_path": "",
    }
    stores = CandidateStores(
        generation=gen, vectordb=cand_v, doc_db=cand_d,
        bm25_store=None, bm25_dir=(tmp_path or ".") , chunk_store=buffer,
        main_vectordb=main_v, main_doc_db=main_d, bm25_source=None)
    return stores, main_v, main_d, bm25_main, index_result


def _publish(tmp_path, stores, index_result, registry, run_store,
             old_row=None, old_chunk_ids=None, main_bm25=None):
    # 发布步骤 e 需要「暂存或正式至少一个存在」+ os.replace 目标父目录可落盘
    # （生产里由 acquire_index_lock 负责建父目录；单测直接落 tmp）
    final = tmp_path / "docs" / "kb" / "dept" / "a.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = tmp_path / "staging-src.md"
    if not index_result.get("staging_path"):
        staging.write_text("staged", encoding="utf-8")
        index_result["staging_path"] = str(staging)
    return publish_candidate(
        run_store=run_store, upload_id="u1", registry=registry,
        stores=stores, final_path=str(final), doc_id="doc-1",
        kb_id="kb", file_hash="h" * 64, base_generation="",
        index_result=index_result, old_row=old_row,
        old_chunk_ids=old_chunk_ids or [], old_doc_db_id="",
        main_bm25_store=main_bm25 or FakeBM25Main(),
    )


def _patch_switch(monkeypatch):
    """把 BM25 文件切换替换为 adopt 协议桩（协议本体另有 C 阶段测试覆盖）。"""
    monkeypatch.setattr(
        "backend.rag.indexing.publish.switch_bm25_snapshot",
        lambda cand, main, gen: {"generation": gen},
    )


# ── 协议行为 ──


def test_publish_happy_path_commits_and_cleans_old(tmp_path, monkeypatch):
    _patch_switch(monkeypatch)
    stores, main_v, main_d, _, idx = _stores(tmp_path=tmp_path)
    registry, run_store = FakeRegistry(), FakeRunStore()
    chunk_real = FakeChunkStoreReal()
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store", lambda: chunk_real)

    old_row = {"doc_id": "doc-1", "chunk_ids": "[\"old1\",\"old2\"]",
               "doc_db_id": "doc::old", "doc_type": "general"}

    result = _publish(tmp_path, stores, idx, registry, run_store,
                      old_row=old_row, old_chunk_ids=["old1", "old2"])

    assert result == "published"
    assert run_store.status["u1"] == "published"
    # CAS 提交点：registry 行指向本代次
    row = registry.get_by_path(str(tmp_path / "docs" / "kb" / "dept" / "a.md"))
    assert row["active_generation"] == "g1"
    # 候选向量晋级主 collection
    assert main_v.promoted == ["chroma::cand:g1"]
    assert main_d.promoted == ["doc_db::cand:g1"]
    # 旧版清理：精确按旧 chunk id，不按 doc_id 条件删
    assert main_v.deleted_ids == [["old1", "old2"]]
    assert main_d.deleted_ids == [["doc::old"]]
    # chunk_store 按文档翻新
    assert chunk_real.flushed == {"doc-1": 1}
    assert registry.register_calls == 1


def test_publish_cas_loser_is_superseded_and_nothing_promoted(tmp_path, monkeypatch):
    """并发发布：后到者 CAS 失败 → superseded，主 collection 零改动。"""
    _patch_switch(monkeypatch)
    stores, main_v, main_d, _, idx = _stores(gen="g2", tmp_path=tmp_path)
    registry, run_store = FakeRegistry(), FakeRunStore()
    # 已有更新发布者（base 不是本候选认领的 ""）
    registry.rows[str(tmp_path / "docs" / "kb" / "dept" / "a.md")] = {"active_generation": "g1-new"}

    with pytest.raises(SupersededCandidate):
        _publish(tmp_path, stores, idx, registry, run_store)

    # publish 只负责抛出；superseded 终态由 Worker 侧 except 标记
    # （_do_index_sync 的 SupersededCandidate 分支）
    assert run_store.status["u1"] == "publishing"
    assert main_v.promoted == [] and main_d.promoted == []
    assert main_v.deleted_ids == []


def test_publish_resume_after_commit_keeps_expected_base(tmp_path, monkeypatch):
    """提交点后续跑（publishing 重入）：守卫基准切换为本代次，幂等重发。"""
    _patch_switch(monkeypatch)
    stores, main_v, main_d, _, idx = _stores(gen="g1", tmp_path=tmp_path)
    registry, run_store = FakeRegistry(), FakeRunStore()
    chunk_real = FakeChunkStoreReal()
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store", lambda: chunk_real)
    registry.rows[str(tmp_path / "docs" / "kb" / "dept" / "a.md")] = {
        "active_generation": "g1", "doc_id": "doc-1",
        "chunk_ids": "[\"c1\",\"c2\"]", "doc_db_id": "doc::new",
        "doc_type": "general"}

    result = _publish(tmp_path, stores, idx, registry, run_store,
                      old_row=registry.rows[str(tmp_path / "docs" / "kb" / "dept" / "a.md")],
                      old_chunk_ids=["c1", "c2"])
    assert result == "published"


def test_publish_zero_chunks_rejected(tmp_path, monkeypatch):
    _patch_switch(monkeypatch)
    stores, main_v, _, _, idx = _stores(tmp_path=tmp_path)
    idx["chunk_ids"] = []
    registry, run_store = FakeRegistry(), FakeRunStore()
    with pytest.raises(CandidatePublishError):
        _publish(tmp_path, stores, idx, registry, run_store)
    # 零 chunk 在 mark publishing 之前就拒绝（failed 终态由外层 settle 落）
    assert "u1" not in run_store.status
    assert registry.rows == {}


def test_financial_snapshot_keeps_old_vectors(tmp_path, monkeypatch):
    """财务版本快照：旧 chunk 不删除，只批量翻 is_latest=False。"""
    _patch_switch(monkeypatch)
    from backend.rag.indexing.indexer import IncrementalIndexer
    monkeypatch.setattr(
        IncrementalIndexer, "_should_use_version_snapshot",
        staticmethod(lambda doc_type, path: doc_type == "financial"))
    stores, main_v, main_d, _, idx = _stores(tmp_path=tmp_path)
    registry, run_store = FakeRegistry(), FakeRunStore()
    chunk_real = FakeChunkStoreReal()
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store", lambda: chunk_real)

    old_row = {"doc_id": "doc-1", "chunk_ids": "[\"old1\"]",
               "doc_db_id": "doc::old", "doc_type": "financial"}

    result = _publish(tmp_path, stores, idx, registry, run_store,
                      old_row=old_row, old_chunk_ids=["old1"])
    assert result == "published"
    # 旧版向量保留（版本快照语义）
    assert main_v.deleted_ids == []
    # is_latest 批量翻转按 doc_id 发生
    assert main_v.metadata_updates == [
        ({"doc_id": "doc-1"}, {"is_latest": False})]


def test_buffering_chunk_store_flush_semantics(tmp_path):
    real = FakeChunkStoreReal()
    buf = BufferingChunkStore()
    buf.delete_by_doc_id("doc-9")
    buf.insert_batch("doc-9", [{"chunk_index": 0}, {"chunk_index": 1}])
    assert buf.flush(real) == 2
    assert real.deleted == ["doc-9"]  # flush 先清旧（同文档去重一次）再插
    assert real.flushed == {"doc-9": 2}
    buf.discard()
    assert buf.flush(real) == 0


def test_composite_view_concatenates_main_and_candidate():
    class S:
        def __init__(self, ids, texts, metas):
            self._d = {"ids": ids, "documents": texts, "metadatas": metas}
        def get(self, where=None):
            return self._d
    comp = CompositeVectorStore([S(["a"], ["t1"], [{}]), S(["b"], ["t2"], [{}])],
                                collection_label="chroma")
    out = comp.get()
    assert out["ids"] == ["a", "b"] and out["documents"] == ["t1", "t2"]
    with pytest.raises(NotImplementedError):
        comp.add_documents([])
