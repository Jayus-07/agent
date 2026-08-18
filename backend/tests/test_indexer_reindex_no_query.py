"""P2-2 + P2-3 测试：消除 reindex_file 反查 + 私有 cursor 访问。

旧行为:
  reindex_file() 调用 _index_file() 只拿到 trace_id,然后用 self.registry.get_by_path()
  反查 doc 详情（额外 1 次 SQLite round-trip）。之后还要用 self.registry._lock +
  self.registry._conn() 直接访问私有字段 UPDATE doc_version,违反封装。

新行为:
  - _index_file() 返回 dict 含 trace_id / chunk_count / doc_db_id
  - reindex_file() 直接消费返回值,不再反查
  - DocumentRegistry 提供公开 bump_doc_version(doc_id, delta=1) 方法
  - reindex_file 调它替代私有 cursor 操作
"""
from unittest.mock import MagicMock, call

import pytest

from backend.rag.indexing.doc_registry import DocumentRegistry


# ============ DocumentRegistry.bump_doc_version 测试 ============

class TestBumpDocVersion:
    """公开方法替代私有 cursor 访问。"""

    def test_bump_increments_version_by_one(self, tmp_path):
        db_path = str(tmp_path / "test_bump.db")
        reg = DocumentRegistry(db_path)
        reg.register(
            file_path="/fake/path.md", doc_id="did1",
            file_hash="hash1", kb_id="test_kb",
            chunk_ids=["c1", "c2"], doc_db_id="ddb1",
            metadata={"doc_version": 5},
        )

        new_version = reg.bump_doc_version("did1", delta=1)
        assert new_version == 6

    def test_bump_returns_new_version_or_minus_one_if_missing(self, tmp_path):
        db_path = str(tmp_path / "test_bump2.db")
        reg = DocumentRegistry(db_path)
        # doc_id 不存在
        assert reg.bump_doc_version("nonexistent", delta=1) == -1

    def test_bump_with_custom_delta(self, tmp_path):
        db_path = str(tmp_path / "test_bump3.db")
        reg = DocumentRegistry(db_path)
        reg.register(
            file_path="/fake/path.md", doc_id="did1",
            file_hash="hash1", kb_id="test_kb",
            chunk_ids=["c1"], doc_db_id="ddb1",
            metadata={"doc_version": 2},
        )
        assert reg.bump_doc_version("did1", delta=5) == 7

    def test_bump_does_not_affect_deleted_records(self, tmp_path):
        """doc_version 只对 active 记录 bump;deleted 记录保持。"""
        db_path = str(tmp_path / "test_bump4.db")
        reg = DocumentRegistry(db_path)
        reg.register(
            file_path="/fake/path.md", doc_id="did1",
            file_hash="hash1", kb_id="test_kb",
            chunk_ids=["c1"], doc_db_id="ddb1",
            metadata={"doc_version": 3},
        )
        reg.mark_deleted_by_doc_id("did1")

        # deleted 状态:不应该 bump
        assert reg.bump_doc_version("did1", delta=1) == -1


# ============ reindex_file 直接返回值测试 ============

class TestReindexFileNoExtraQuery:
    """reindex_file 应该从 _index_file() 返回值里拿 chunk_count,不再反查 registry。"""

    def test_reindex_does_not_call_get_by_path_after_index(self, tmp_path, monkeypatch):
        """reindex_file 完成后,registry.get_by_path() 不应被调用第二次。"""
        from backend.rag.indexing.indexer import IncrementalIndexer

        target = tmp_path / "doc.md"
        target.write_text("# title\nbody", encoding="utf-8")

        reg = DocumentRegistry(str(tmp_path / "reg.db"))
        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=reg,
        )

        # Mock 掉 _index_file,让它返回带 chunk_count 的 dict
        expected_result = {
            "trace_id": "trace-xyz",
            "chunk_count": 8,
            "doc_db_id": "ddb-1",
            "file_hash": "h",
            "status": "active",
        }
        indexer._index_file = MagicMock(return_value=expected_result)

        # Spy: 第一次 get_by_path（用于查旧版本）是允许的，第二次必须是 0 次
        original_get_by_path = reg.get_by_path
        call_count = [0]

        def counting_get_by_path(*args, **kwargs):
            call_count[0] += 1
            return {"doc_id": "did1", "doc_version": 1, "file_path": str(target)}

        monkeypatch.setattr(reg, "get_by_path", counting_get_by_path)

        indexer.reindex_file(str(target))

        # 应该最多 1 次（用于查 old_version），不应该第二次
        assert call_count[0] <= 1, (
            f"reindex_file 不应反复调用 get_by_path,实际 {call_count[0]} 次"
        )

    def test_reindex_returns_chunk_count_from_index_file(self, tmp_path):
        """reindex_file 返回的 chunk_count 应该来自 _index_file(),不是反查。"""
        from backend.rag.indexing.indexer import IncrementalIndexer

        target = tmp_path / "doc.md"
        target.write_text("# title\nbody", encoding="utf-8")

        reg = DocumentRegistry(str(tmp_path / "reg.db"))
        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=reg,
        )

        expected_chunk_count = 42
        indexer._index_file = MagicMock(return_value={
            "trace_id": "trace-xyz",
            "chunk_count": expected_chunk_count,
            "doc_db_id": "ddb-1",
            "file_hash": "h",
            "status": "active",
        })

        result = indexer.reindex_file(str(target))

        assert result["chunk_count"] == expected_chunk_count

    def test_reindex_uses_bump_doc_version_public_api(self, tmp_path):
        """reindex_file 应该调 bump_doc_version(),不再访问私有 cursor。"""
        from backend.rag.indexing.indexer import IncrementalIndexer

        target = tmp_path / "doc.md"
        target.write_text("# title\nbody", encoding="utf-8")

        reg = DocumentRegistry(str(tmp_path / "reg.db"))
        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=reg,
        )

        # Mock _index_file 和 get_by_path
        indexer._index_file = MagicMock(return_value={
            "trace_id": "t", "chunk_count": 5,
            "doc_db_id": "d", "file_hash": "h", "status": "active",
        })
        reg.get_by_path = MagicMock(return_value={
            "doc_id": "did1", "doc_version": 1, "file_path": str(target),
        })

        # Spy bump_doc_version
        bumped_versions = []
        original_bump = reg.bump_doc_version
        def counting_bump(doc_id, delta=1):
            result = original_bump(doc_id, delta)
            bumped_versions.append((doc_id, delta, result))
            return result
        reg.bump_doc_version = counting_bump

        indexer.reindex_file(str(target))

        # 关键断言:必须调 bump_doc_version
        assert len(bumped_versions) == 1, (
            f"reindex_file 必须调 1 次 bump_doc_version,实际 {len(bumped_versions)} 次"
        )
        doc_id, delta, _new_ver = bumped_versions[0]
        assert doc_id == "did1"
        assert delta == 1
        # new_ver 的值取决于 registry 实际状态(Mock 环境下会是 -1)
        # 但调用本身必须发生