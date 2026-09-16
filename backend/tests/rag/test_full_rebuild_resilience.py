"""全量重建连锁灾难加固的回归测试（2026-09-17 事故根治）。

覆盖三项加固：
1. 全量重建快照回填——重建前快照 registry，重建后 doc_id/status/minhash_sig
   原样恢复，离盘存量行（近重复隔离副本）不丢
2. sync 单文件异常 per-file 跳过——不再整轮崩溃回退全量重建
3. near-dup 检测跨 doc_type 比较——分类漂移不再造成漏检
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.indexing.models import SyncResult

SIG_A = json.dumps(list(range(128)))  # 任意 128 维签名


@pytest.fixture
def registry(tmp_path):
    return DocumentRegistry(str(tmp_path / "reg.db"))


def _mk_indexer(tmp_path, registry, vectordb=None, doc_db=None):
    return IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=vectordb if vectordb is not None else MagicMock(),
        doc_db=doc_db if doc_db is not None else MagicMock(),
        embedding=MagicMock(),
        registry=registry,
    )


# ============ 加固 2：per-file 跳过 ============

class TestPerFileSkip:

    def test_added_failure_does_not_crash_sync(self, tmp_path, registry):
        """ADDED 文件索引失败 → 记入 result.failed，sync 正常返回。"""
        (tmp_path / "a.md").write_text("doc a", encoding="utf-8")
        (tmp_path / "b.md").write_text("doc b", encoding="utf-8")
        idx = _mk_indexer(tmp_path, registry)

        def flaky(path, *a, **kw):
            if str(path).endswith("b.md"):
                raise RuntimeError("模拟坏文件（扫描件 0 文本）")
            # 模拟 a.md 索引成功落账
            registry.register(
                file_path=str(path), doc_id="d_a", file_hash="x",
                kb_id="kb1", chunk_ids=[], doc_db_id="",
            )

        with patch.object(idx, "_index_file", side_effect=flaky):
            result = idx.sync()

        assert isinstance(result, SyncResult)
        assert result.failed == 1
        assert len(result.failed_files) == 1 and "b.md" in result.failed_files[0]
        # 好文件照常入库
        assert registry.get_by_path(str((tmp_path / "a.md").resolve())) is not None

    def test_failed_added_retried_next_sync(self, tmp_path, registry):
        """失败的 ADDED 行没有 registry 记录 → 下轮 sync 仍按 ADDED 重试（自愈）。"""
        (tmp_path / "b.md").write_text("doc b", encoding="utf-8")
        idx = _mk_indexer(tmp_path, registry)

        with patch.object(idx, "_index_file", side_effect=RuntimeError("boom")):
            r1 = idx.sync()
        assert r1.failed == 1

        def ok(path, *a, **kw):
            registry.register(
                file_path=str(path), doc_id="d_b", file_hash="x",
                kb_id="kb1", chunk_ids=[], doc_db_id="",
            )
        with patch.object(idx, "_index_file", side_effect=ok):
            r2 = idx.sync()  # 第二轮重试成功
        assert r2.added == 1 and r2.failed == 0
        assert registry.get_by_path(str((tmp_path / "b.md").resolve())) is not None


# ============ 加固 3：near-dup 跨 doc_type ============

class TestCrossDocTypeNearDup:
    from backend.rag.indexing.stages.metadata_stage import MetadataStage

    def _seed(self, registry, doc_type):
        registry.register(
            file_path="/fake/existing.md", doc_id="existing_doc", file_hash="h",
            kb_id="kb1", chunk_ids=[], doc_db_id="",
            metadata={"doc_type": doc_type, "minhash_sig": SIG_A},
        )

    def test_detect_across_doc_types(self, registry):
        """query doc_type 与存量不同，sim=1.0 也必须检出（旧逻辑漏检）。"""
        from backend.rag.indexing.stages.metadata_stage import MetadataStage
        self._seed(registry, doc_type="sop")
        hit = MetadataStage.detect_near_dup(
            registry, list(range(128)), doc_type="general", exclude_doc_id="self")
        assert hit == "existing_doc"

    def test_exclude_self(self, registry):
        from backend.rag.indexing.stages.metadata_stage import MetadataStage
        self._seed(registry, doc_type="faq")
        hit = MetadataStage.detect_near_dup(
            registry, list(range(128)), doc_type="faq", exclude_doc_id="existing_doc")
        assert hit == ""


# ============ 加固 1：全量重建快照回填 ============

class TestFullRebuildSnapshotRestore:

    @pytest.fixture
    def rebuild_env(self, tmp_path, registry, monkeypatch):
        """构造最小全量重建环境：2 个盘上文档 + 1 个离盘隔离副本。"""
        docs = tmp_path / "docs" / "kb1"
        docs.mkdir(parents=True)
        f_a = docs / "a.md"; f_a.write_text("doc a", encoding="utf-8")
        f_b = docs / "b.md"; f_b.write_text("doc b", encoding="utf-8")

        # 预注册：A = 语义 slug + pending_review + 签名（近重复隔离场景）
        registry.register(
            file_path=str(f_a), doc_id="policy_semantic_a", file_hash="old_a",
            kb_id="kb1", chunk_ids=["c1"], doc_db_id="ddb_a",
            metadata={"doc_type": "policy", "minhash_sig": SIG_A},
        )
        registry.update_status(str(f_a), "pending_review")
        # C = 离盘行（副本已移入隔离区）
        registry.register(
            file_path="Q:/quarantine/c.md", doc_id="policy_semantic_c",
            file_hash="old_c", kb_id="kb1", chunk_ids=["c9"], doc_db_id="",
            metadata={"doc_type": "policy", "minhash_sig": SIG_A},
        )
        registry.update_status("Q:/quarantine/c.md", "pending_review")
        # B = 普通 md5 行，active
        registry.register(
            file_path=str(f_b), doc_id="3f2a1b9c8d", file_hash="old_b",
            kb_id="kb1", chunk_ids=["c2"], doc_db_id="ddb_b",
            metadata={"doc_type": "faq", "minhash_sig": json.dumps(list(range(128, 256)))},
        )

        snapshot = {p: dict(r) for p, r in registry.list_all().items()}

        from backend.rag import pipeline as pl
        monkeypatch.setattr(pl, "DOC_REGISTRY_PATH", str(tmp_path / "reg.db"))
        monkeypatch.setattr(pl, "DOCS_DIRECTORY", str(tmp_path / "docs"))

        # 打桩 chunk_store（全量重建路径会写真实全局库，测试隔离）
        cs_mock = MagicMock()
        with patch("backend.rag.indexing.chunk_store.get_chunk_store", return_value=cs_mock):
            yield pl, snapshot, {"a": str(f_a), "b": str(f_b), "c": "Q:/quarantine/c.md"}

    def _mk_pipeline(self, pl, snapshot):
        p = pl.RAGPipeline.__new__(pl.RAGPipeline)
        p._registry_snapshot = {k: dict(v) for k, v in snapshot.items()}
        vdb = MagicMock()
        vdb.get.return_value = {"ids": ["ck1"], "documents": ["t"], "metadatas": [{}]}
        ddb = MagicMock()
        ddb.get.return_value = {"ids": ["ddb_x"]}
        p.vectordb, p.doc_db = vdb, ddb
        p.embedding = MagicMock()
        return p

    def test_doc_id_status_sig_restored(self, tmp_path, rebuild_env):
        pl, snapshot, paths = rebuild_env
        p = self._mk_pipeline(pl, snapshot)
        p._sync_registry_after_full_rebuild()

        registry = DocumentRegistry(str(tmp_path / "reg.db"))
        row_a = registry.get_by_path(paths["a"])
        # 语义 slug 不被 md5 重派覆盖
        assert row_a["doc_id"] == "policy_semantic_a"
        # 非 active 状态 + 签名回填
        assert row_a["status"] == "pending_review"
        assert json.loads(row_a["minhash_sig"]) == list(range(128))

        row_b = registry.get_by_path(paths["b"])
        assert row_b["doc_id"] == "3f2a1b9c8d"  # 历史 hash ID 沿用
        assert row_b["status"] == "active"

    def test_off_disk_rows_survive_rebuild(self, tmp_path, rebuild_env):
        """离盘存量行（隔离副本）在 full rebuild 后仍在 registry 中。"""
        pl, snapshot, paths = rebuild_env
        p = self._mk_pipeline(pl, snapshot)
        p._sync_registry_after_full_rebuild()

        registry = DocumentRegistry(str(tmp_path / "reg.db"))
        row_c = registry.get_by_path(paths["c"])
        assert row_c is not None, "离盘 pending_review 行被全量重建抹掉"
        assert row_c["doc_id"] == "policy_semantic_c"
        assert row_c["status"] == "pending_review"
        assert json.loads(row_c["minhash_sig"]) == list(range(128))
