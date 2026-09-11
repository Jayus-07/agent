"""索引链路可靠性修复的回归测试。

覆盖三块新语义：
1. register_in_progress 占位行（任务状态持久化）——新文件插入 parsing 行，
   重索引场景只改状态、保留 chunk_ids 等历史元数据
2. reindex_file "先写后删"——失败保留旧数据；成功后按旧 ID 精确清理
3. sync() 的中断恢复扫描——文件在 → 重索引；文件丢 → 清理悬空行；
   恢复失败 → 标 failed
"""
import json
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer, INTERRUPTED_STATUSES


@pytest.fixture
def registry(tmp_path):
    return DocumentRegistry(str(tmp_path / "reg.db"))


def _mk_indexer(tmp_path, registry, vectordb=None, doc_db=None):
    idx = IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=vectordb if vectordb is not None else MagicMock(),
        doc_db=doc_db if doc_db is not None else MagicMock(),
        embedding=MagicMock(),
        registry=registry,
    )
    return idx


# ============ register_in_progress 占位行 ============

class TestRegisterInProgress:

    def test_new_file_inserts_parsing_row(self, tmp_path, registry):
        registry.register_in_progress(
            "/fake/new.md", doc_id="did_new", file_hash="h1",
            kb_id="kb1", department="dept1",
        )
        row = registry.get_by_path("/fake/new.md")
        assert row is not None
        assert row["status"] == "parsing"
        assert row["doc_id"] == "did_new"

    def test_existing_active_row_keeps_metadata(self, tmp_path, registry):
        """重索引场景：只改状态，chunk_ids/doc_version 必须保留
        （启动恢复与先写后删清理都靠旧 chunk_ids 定位旧向量）。"""
        registry.register(
            file_path="/fake/doc.md", doc_id="did1", file_hash="old_hash",
            kb_id="kb1", chunk_ids=["c1", "c2"], doc_db_id="ddb-old",
            metadata={"doc_version": 4},
        )
        registry.register_in_progress(
            "/fake/doc.md", doc_id="did1", file_hash="new_hash",
            kb_id="kb1", department="general",
        )
        row = registry.get_by_path("/fake/doc.md")
        assert row["status"] == "parsing"
        assert row["file_hash"] == "new_hash"
        assert json.loads(row["chunk_ids"]) == ["c1", "c2"]
        assert row["doc_db_id"] == "ddb-old"
        assert row["doc_version"] == 4

    def test_list_by_statuses_finds_interrupted(self, tmp_path, registry):
        registry.register_in_progress(
            "/fake/a.md", doc_id="d1", file_hash="h", kb_id="kb")
        registry.register(
            file_path="/fake/b.md", doc_id="d2", file_hash="h",
            kb_id="kb", chunk_ids=["x"], doc_db_id="dd",
        )
        stuck = registry.list_by_statuses(INTERRUPTED_STATUSES)
        assert [r["doc_id"] for r in stuck] == ["d1"]


# ============ reindex_file 先写后删 ============

class TestReindexWriteThenDelete:

    def _register_old(self, registry, path="/fake/doc.md"):
        registry.register(
            file_path=path, doc_id="did1", file_hash="old_hash",
            kb_id="kb1", chunk_ids=["c1", "c2"], doc_db_id="ddb-old",
            metadata={"doc_version": 1},
        )

    def test_failure_preserves_old_data(self, tmp_path, registry):
        """索引失败 → 旧向量原样保留（旧实现先删后写，失败即丢旧版本）。"""
        target = tmp_path / "doc.md"
        target.write_text("body", encoding="utf-8")
        self._register_old(registry, str(target))

        idx = _mk_indexer(tmp_path, registry)
        idx._index_file = MagicMock(side_effect=RuntimeError("embed down"))

        with pytest.raises(RuntimeError):
            idx.reindex_file(str(target))

        idx.vectordb.delete.assert_not_called()
        idx.doc_db.delete.assert_not_called()
        row = registry.get_by_path(str(target))
        assert row["status"] == "active", "失败的索引不能动旧 active 记录"

    def test_success_cleans_superseded_by_ids(self, tmp_path, registry):
        """索引成功 → 按旧 chunk_ids/doc_db_id 精确清理，不按 doc_id 条件删
        （新旧 chunk 共享 doc_id，条件删会误删新向量）。"""
        target = tmp_path / "doc.md"
        target.write_text("body", encoding="utf-8")
        self._register_old(registry, str(target))

        idx = _mk_indexer(tmp_path, registry)
        idx._index_file = MagicMock(return_value={
            "trace_id": "t", "doc_id": "did1", "chunk_count": 3,
            "doc_db_id": "ddb-new", "file_hash": "new_hash", "status": "active",
        })

        idx.reindex_file(str(target))

        idx.vectordb.delete.assert_called_once_with(ids=["c1", "c2"])
        idx.doc_db.delete.assert_called_once_with(ids=["ddb-old"])
        # _remove_document 的 doc_id 条件删不应出现在先写后删路径
        for c in idx.vectordb.delete.call_args_list:
            assert "where" not in (c.kwargs or {})

    def test_success_without_old_row_skips_cleanup(self, tmp_path, registry):
        """首次索引（无旧记录）不应触发任何删除。"""
        target = tmp_path / "fresh.md"
        target.write_text("body", encoding="utf-8")

        idx = _mk_indexer(tmp_path, registry)
        idx._index_file = MagicMock(return_value={
            "trace_id": "t", "doc_id": "d_new", "chunk_count": 1,
            "doc_db_id": "ddb1", "file_hash": "h", "status": "active",
        })

        idx.reindex_file(str(target))
        idx.vectordb.delete.assert_not_called()
        idx.doc_db.delete.assert_not_called()


# ============ sync() 中断恢复 ============

class TestRecoverInterrupted:

    def test_existing_file_reindexed(self, tmp_path, registry):
        f = tmp_path / "stuck.md"
        f.write_text("body", encoding="utf-8")
        registry.register_in_progress(
            str(f), doc_id="d1", file_hash="h", kb_id="kb")

        idx = _mk_indexer(tmp_path, registry)
        idx._index_file = MagicMock(return_value={})

        counts = idx._recover_interrupted({})
        assert counts["recovered"] == 1
        idx._index_file.assert_called_once()

    def test_missing_file_cleaned(self, tmp_path, registry):
        registry.register_in_progress(
            str(tmp_path / "gone.md"), doc_id="d1", file_hash="h", kb_id="kb")

        idx = _mk_indexer(tmp_path, registry)
        counts = idx._recover_interrupted({})
        assert counts["cleaned"] == 1
        row = registry.get_by_path(str(tmp_path / "gone.md"))
        assert row["status"] == "deleted"

    def test_failed_recovery_marked_failed(self, tmp_path, registry):
        f = tmp_path / "bad.md"
        f.write_text("body", encoding="utf-8")
        registry.register_in_progress(
            str(f), doc_id="d1", file_hash="h", kb_id="kb")

        idx = _mk_indexer(tmp_path, registry)
        idx._index_file = MagicMock(side_effect=RuntimeError("boom"))

        counts = idx._recover_interrupted({})
        assert counts["failed"] == 1
        assert registry.get_by_path(str(f))["status"] == "failed"

    def test_registry_without_list_by_statuses_is_noop(self, tmp_path):
        """自定义 registry（测试 fake）无 list_by_statuses 时软跳过。"""
        idx = _mk_indexer(tmp_path, registry=MagicMock())
        counts = idx._recover_interrupted({})
        assert counts == {"recovered": 0, "cleaned": 0, "failed": 0}
