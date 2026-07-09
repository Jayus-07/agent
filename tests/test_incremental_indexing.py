"""增量索引系统测试 — DocumentRegistry + IncrementalIndexer + KnowledgeStore.delete。"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.doc_registry import DocumentRegistry
from retrieval.indexer import (
    IncrementalIndexer, SyncResult, Delta, _run_async,
)


# ======================= Fixtures =======================

@pytest.fixture
def registry():
    """临时 SQLite 注册表。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_registry.db")
        reg = DocumentRegistry(db_path)
        yield reg


@pytest.fixture
def temp_docs_dir():
    """创建临时文档目录，包含 3 个测试文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # kb_id = "test_kb" 子目录
        kb_dir = os.path.join(tmpdir, "test_kb")
        os.makedirs(kb_dir, exist_ok=True)

        # 文件 1
        f1 = os.path.join(kb_dir, "doc1.txt")
        with open(f1, "w", encoding="utf-8") as f:
            f.write("这是第一篇测试文档。\n\n包含两个段落。\n\n用于增量索引测试。")

        # 文件 2
        f2 = os.path.join(kb_dir, "doc2.md")
        with open(f2, "w", encoding="utf-8") as f:
            f.write("# 测试文档二\n\n这是第二篇文档，Markdown 格式。\n\n## 第二节\n内容。")

        # 文件 3 (root level → default kb)
        f3 = os.path.join(tmpdir, "doc3.txt")
        with open(f3, "w", encoding="utf-8") as f:
            f.write("根目录文档。Default KB。")

        yield tmpdir


# ======================= TestDocumentRegistry =======================

class TestDocumentRegistry:
    def test_register_and_retrieve(self, registry):
        registry.register(
            "/tmp/test.txt", "abc123", "sha256_xxx", "hr",
            chunk_ids=["id1", "id2"], doc_db_id="did1",
        )
        row = registry.get_by_path("/tmp/test.txt")
        assert row is not None
        assert row["doc_id"] == "abc123"
        assert row["kb_id"] == "hr"
        assert row["status"] == "active"
        assert len(row["chunk_ids"]) > 0  # JSON string

    def test_get_nonexistent(self, registry):
        assert registry.get_by_path("/nonexistent.txt") is None

    def test_list_all(self, registry):
        registry.register("/tmp/a.txt", "a1", "h1", "hr", ["c1"], "d1")
        registry.register("/tmp/b.txt", "b1", "h2", "tech", ["c2"], "d2")
        all_rows = registry.list_all()
        assert len(all_rows) == 2

    def test_list_active(self, registry):
        registry.register("/tmp/a.txt", "a1", "h1", "hr", ["c1"], "d1")
        registry.register("/tmp/b.txt", "b1", "h2", "tech", ["c2"], "d2")
        registry.mark_deleted("/tmp/b.txt")
        active = registry.list_active()
        assert len(active) == 1
        assert active[0]["file_path"] == "/tmp/a.txt"

    def test_mark_deleted(self, registry):
        registry.register("/tmp/x.txt", "x1", "hx", "hr", ["c1"], "d1")
        registry.mark_deleted("/tmp/x.txt")
        row = registry.get_by_path("/tmp/x.txt")
        assert row["status"] == "deleted"

    def test_count(self, registry):
        assert registry.count() == 0
        registry.register("/tmp/a.txt", "a1", "h1", "hr", ["c1"], "d1")
        assert registry.count() == 1

    def test_clear(self, registry):
        registry.register("/tmp/a.txt", "a1", "h1", "hr", ["c1"], "d1")
        registry.clear()
        assert registry.count() == 0


# ======================= TestSHA256 =======================

class TestSHA256:
    def test_hash_deterministic(self, temp_docs_dir):
        """同一文件两次 SHA256 一致。"""
        f1 = os.path.join(temp_docs_dir, "test_kb", "doc1.txt")
        h1 = IncrementalIndexer._sha256(f1)
        h2 = IncrementalIndexer._sha256(f1)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 是 64 字符 hex

    def test_hash_different_for_different_content(self, temp_docs_dir):
        f1 = os.path.join(temp_docs_dir, "test_kb", "doc1.txt")
        f2 = os.path.join(temp_docs_dir, "test_kb", "doc2.md")
        assert IncrementalIndexer._sha256(f1) != IncrementalIndexer._sha256(f2)


# ======================= TestScanDisk =======================

class TestScanDisk:
    def test_scan_finds_all_files(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        disk = indexer._scan_disk()
        assert len(disk) == 3
        for path in disk:
            assert os.path.exists(path)
            h, size, mtime = disk[path]
            assert len(h) == 64
            assert size > 0

    def test_scan_ignores_unsupported(self, temp_docs_dir):
        # 创建不支持的文件
        bad = os.path.join(temp_docs_dir, "image.png")
        with open(bad, "w") as f:
            f.write("not an image")
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        disk = indexer._scan_disk()
        # .png 不应出现
        for path in disk:
            assert not path.endswith(".png")


# ======================= TestDelta =======================

class TestDelta:
    def test_all_added_when_registry_empty(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        disk = indexer._scan_disk()
        delta = indexer._compute_delta(disk, {})
        assert len(delta.added) == 3
        assert len(delta.unchanged) == 0
        assert len(delta.modified) == 0
        assert len(delta.deleted) == 0

    def test_all_unchanged_when_same_hash(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        disk = indexer._scan_disk()
        # 模拟注册表（与磁盘完全一致）
        registry = {
            p: {"file_hash": h, "status": "active"}
            for p, (h, _, _) in disk.items()
        }
        delta = indexer._compute_delta(disk, registry)
        assert len(delta.unchanged) == 3
        assert len(delta.added) == 0
        assert len(delta.modified) == 0

    def test_modified_detected(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        disk = indexer._scan_disk()
        # 修改一个文件的 hash
        registry = {}
        for p, (h, s, m) in disk.items():
            if "doc1" in p:
                registry[p] = {"file_hash": "different_hash", "status": "active"}
            else:
                registry[p] = {"file_hash": h, "status": "active"}
        delta = indexer._compute_delta(disk, registry)
        assert len(delta.modified) == 1
        assert "doc1" in list(delta.modified)[0]

    def test_deleted_detected(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        disk = indexer._scan_disk()
        # registry 多一个不存在的文件
        registry = {
            p: {"file_hash": h, "status": "active"}
            for p, (h, _, _) in disk.items()
        }
        registry["/tmp/deleted_file.txt"] = {
            "file_hash": "xxx", "status": "active",
        }
        delta = indexer._compute_delta(disk, registry)
        assert len(delta.deleted) == 1


# ======================= TestKBIdDerivation =======================

class TestKBId:
    def test_kb_from_subdir(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        kb = indexer._derive_kb_id(
            os.path.join(temp_docs_dir, "test_kb", "doc1.txt")
        )
        assert kb == "test_kb"

    def test_default_kb_for_root(self, temp_docs_dir):
        indexer = IncrementalIndexer(
            docs_dir=temp_docs_dir,
            vectordb=None, doc_db=None, embedding=None, registry=None,
        )
        kb = indexer._derive_kb_id(
            os.path.join(temp_docs_dir, "doc3.txt")
        )
        assert kb == "default"


# ======================= TestSyncResult =======================

class TestSyncResult:
    def test_defaults(self):
        r = SyncResult()
        assert r.added == 0
        assert r.total_changed == 0

    def test_total_changed(self):
        r = SyncResult(added=2, modified=1, deleted=3, skipped=10)
        assert r.total_changed == 6


# ======================= TestEndToEndIncremental =======================

class TestEndToEndIncremental:
    """端到端集成测试：完整增量索引流程，使用临时目录 + FakeEmbeddings。

    不碰真实 data/chroma/，不加载真实 Embedding 模型。
    """

    @pytest.fixture
    def e2e_env(self):
        """创建完整的临时环境：docs 目录 + ChromaDB + registry。

        模拟 3 个文档，覆盖 2 个 kb_id。
        """
        from langchain_community.embeddings import FakeEmbeddings
        from retrieval.knowledge_store import ChromaKnowledgeStore

        tmpdir = tempfile.mkdtemp()

        # 文档目录
        docs_dir = os.path.join(tmpdir, "docs")
        kb_dir = os.path.join(docs_dir, "hr")
        os.makedirs(kb_dir)

        # 3 个测试文档
        self._write_file(os.path.join(kb_dir, "policy.txt"),
                         "公司考勤制度\n\n第一条 工作时间\n\n第二条 请假流程")
        self._write_file(os.path.join(kb_dir, "training.txt"),
                         "新员工培训手册\n\n第一天 公司介绍\n\n第二天 系统操作")
        self._write_file(os.path.join(docs_dir, "readme.txt"),
                         "知识库说明文档\n\n这是默认知识库的根文档。")

        # 向量库目录
        chroma_dir = os.path.join(tmpdir, "chroma")
        doc_db_dir = os.path.join(tmpdir, "doc_db")
        os.makedirs(chroma_dir)
        os.makedirs(doc_db_dir)

        # FakeEmbeddings（384 维，确定性）
        embedding = FakeEmbeddings(size=384)

        # 创建空向量库
        vectordb = ChromaKnowledgeStore(
            persist_directory=chroma_dir, embedding_function=embedding,
        )
        doc_db = ChromaKnowledgeStore(
            persist_directory=doc_db_dir, embedding_function=embedding,
        )

        # 注册表
        registry_path = os.path.join(tmpdir, "registry.db")
        registry = DocumentRegistry(registry_path)

        yield {
            "tmpdir": tmpdir,
            "docs_dir": docs_dir,
            "chroma_dir": chroma_dir,
            "doc_db_dir": doc_db_dir,
            "vectordb": vectordb,
            "doc_db": doc_db,
            "embedding": embedding,
            "registry": registry,
        }

        # 清理
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    @staticmethod
    def _write_file(path: str, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _make_indexer(self, env):
        return IncrementalIndexer(
            docs_dir=env["docs_dir"],
            vectordb=env["vectordb"],
            doc_db=env["doc_db"],
            embedding=env["embedding"],
            registry=env["registry"],
        )

    # ---- 测试用例 ----

    def test_first_sync_all_added(self, e2e_env):
        """首次同步：所有文件标记为 ADDED，写入向量库，注册 registry。"""
        indexer = self._make_indexer(e2e_env)
        result = indexer.sync()

        assert result.added == 3
        assert result.modified == 0
        assert result.deleted == 0
        assert result.skipped == 0

        # Registry 应有 3 条 active 记录
        assert e2e_env["registry"].count() == 3
        active = e2e_env["registry"].list_active()
        assert len(active) == 3

        # 向量库应有数据
        chunk_data = e2e_env["vectordb"].get()
        assert len(chunk_data["ids"]) > 0, "Chunk 级向量库应有数据"

        doc_data = e2e_env["doc_db"].get()
        assert len(doc_data["ids"]) == 3, "Doc 级向量库应有 3 条"

    def test_second_sync_all_skipped(self, e2e_env):
        """二次同步：文件未变，全部跳过。"""
        indexer = self._make_indexer(e2e_env)

        # 首次
        indexer.sync()
        chunk_count_before = len(e2e_env["vectordb"].get()["ids"])

        # 二次
        result = indexer.sync()

        assert result.added == 0
        assert result.modified == 0
        assert result.deleted == 0
        assert result.skipped == 3

        # 向量库不变
        chunk_count_after = len(e2e_env["vectordb"].get()["ids"])
        assert chunk_count_after == chunk_count_before

    def test_modify_file_reindexes_one(self, e2e_env):
        """修改一个文件：只有它被重新索引，其余跳过。"""
        indexer = self._make_indexer(e2e_env)
        indexer.sync()

        # 修改一个文件
        modified_path = os.path.join(e2e_env["docs_dir"], "hr", "policy.txt")
        with open(modified_path, "a", encoding="utf-8") as f:
            f.write("\n\n第三条 加班规定")

        result = indexer.sync()

        assert result.modified == 1
        assert result.skipped == 2
        assert result.added == 0

    def test_delete_file_cleans_vectors(self, e2e_env):
        """删除一个文件：向量被删除，registry 标记 deleted。"""
        indexer = self._make_indexer(e2e_env)
        indexer.sync()

        doc_count_before = len(e2e_env["doc_db"].get()["ids"])

        # 删除文件
        deleted_path = os.path.join(e2e_env["docs_dir"], "readme.txt")
        os.remove(deleted_path)

        result = indexer.sync()

        assert result.deleted == 1
        assert result.skipped == 2

        # Registry 中该文件标记为 deleted
        row = e2e_env["registry"].get_by_path(deleted_path)
        assert row is not None
        assert row["status"] == "deleted"

        # Active 记录减少
        active = e2e_env["registry"].list_active()
        assert len(active) == 2

        # Doc 级向量减少
        doc_count_after = len(e2e_env["doc_db"].get()["ids"])
        assert doc_count_after == doc_count_before - 1

    def test_add_then_modify_then_delete(self, e2e_env):
        """完整生命周期：新增 → 修改 → 删除。"""
        indexer = self._make_indexer(e2e_env)

        # 1. 首次: 3 added
        r1 = indexer.sync()
        assert r1.added == 3

        # 2. 新增一个文件
        new_path = os.path.join(e2e_env["docs_dir"], "new_doc.txt")
        self._write_file(new_path, "新文档内容。")
        r2 = indexer.sync()
        assert r2.added == 1
        assert r2.skipped == 3
        assert e2e_env["registry"].count() == 4

        # 3. 修改新文件
        with open(new_path, "a", encoding="utf-8") as f:
            f.write("\n追加内容。")
        r3 = indexer.sync()
        assert r3.modified == 1
        assert r3.skipped == 3

        # 4. 删除新文件
        os.remove(new_path)
        r4 = indexer.sync()
        assert r4.deleted == 1
        assert r4.skipped == 3
        assert len(e2e_env["registry"].list_active()) == 3

    def test_registry_persists_across_indexer_instances(self, e2e_env):
        """Registry 数据在 Indexer 重建后仍然存在。"""
        # 首次 Indexer
        idx1 = self._make_indexer(e2e_env)
        idx1.sync()
        assert e2e_env["registry"].count() == 3

        # 重建 Indexer（模拟重启）
        idx2 = self._make_indexer(e2e_env)
        result = idx2.sync()
        # 文件未变，全部跳过
        assert result.skipped == 3
        assert result.added == 0

    def test_vectors_are_searchable_after_sync(self, e2e_env):
        """同步后向量可检索。"""
        indexer = self._make_indexer(e2e_env)
        indexer.sync()

        # Chunk 级检索
        results = e2e_env["vectordb"].similarity_search("考勤制度", k=3)
        assert len(results) > 0, "应能检索到相关 chunk"

        # 验证 metadata 包含 kb_id
        for doc in results:
            assert "kb_id" in doc.metadata
            assert "doc_id" in doc.metadata

    def test_delete_then_readd_same_file(self, e2e_env):
        """删除后再创建同名文件：视为 ADDED（因为之前是 deleted 状态）。"""
        indexer = self._make_indexer(e2e_env)
        indexer.sync()

        file_path = os.path.join(e2e_env["docs_dir"], "readme.txt")
        original_content = open(file_path, encoding="utf-8").read()

        # 删除
        os.remove(file_path)
        indexer.sync()
        assert e2e_env["registry"].get_by_path(file_path)["status"] == "deleted"

        # 重新创建（相同内容）
        self._write_file(file_path, original_content)
        result = indexer.sync()

        # 应视为 ADDED（因为 registry 中该文件是 deleted 状态，不在 active 中）
        assert result.added == 1
        active = e2e_env["registry"].list_active()
        assert len(active) == 3
