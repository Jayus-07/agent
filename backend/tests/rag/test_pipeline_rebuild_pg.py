"""pipeline 全量重建判定 pgvector 语义回归（2026-09-18 Chroma→pgvector 收口）。

背景：原 Chroma 实现按磁盘目录 + .version 指纹判定重建（_need_rebuild 查
目录、_rebuild_db rmtree）。pgvector 轨下向量库在 rag_vectors 表、目录恒
不存在，回退路径一旦触发 rmtree 必抛 FileNotFoundError。本文件锁定改造后
契约：

  1. _need_rebuild：collection 空 → True；有向量行 → False
  2. _rebuild_db：按 collection 精确清空（不碰同表其他 collection）
  3. _load_or_create_db：空 collection 走 create_fn 重建；非空直接复用

PG 不可达时自动 skip（与 test_doc_registry_version_fields 同约定）。
"""
from __future__ import annotations

import psycopg2
import pytest

from backend.config.database import VECTOR_PG_CONFIG
from backend.rag.pipeline import RAGPipeline
from backend.rag.vectorstore import pgvector_store as pvs_mod
from backend.rag.vectorstore.pgvector_store import EMBEDDING_DIM, PgVectorKnowledgeStore
from backend.tests.fixtures.pg_env import TEST_TABLE_PREFIX, drop_pgtest_tables


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**VECTOR_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_alive(), reason="No PostgreSQL reachable")


class _FakeEmbedding:
    """确定性假嵌入：维度与 DDL 一致，避免加载真实模型。"""

    model_name = "fake-rebuild-test"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        vecs = []
        for t in texts:
            digest = hashlib.md5(t.encode("utf-8")).digest()
            # md5 只有 16 字节，平铺复制到 DDL 维度（EMBEDDING_DIM 可被 env 覆盖）
            tiled = (digest * (EMBEDDING_DIM // len(digest) + 1))[:EMBEDDING_DIM]
            vecs.append([float(b) / 255.0 for b in tiled])
        return vecs


@pytest.fixture(autouse=True)
def _pg_iso(monkeypatch):
    """pgtest_biz_ 前缀隔离 + teardown 清表。

    表名在 store __init__ 逐实例读 env，无需重载模块；但模块级 _DDL_DONE
    缓存了建表标记，清表后必须同步 discard，否则后续用例跳过 DDL 直查报错。
    """
    monkeypatch.setenv("VECTOR_PG_TABLE_PREFIX", TEST_TABLE_PREFIX)
    yield
    pvs_mod._DDL_DONE.discard(TEST_TABLE_PREFIX + "rag_vectors")
    drop_pgtest_tables()


# ============ 1. _need_rebuild ============

class TestNeedRebuild:

    def test_empty_collection_needs_rebuild(self):
        assert RAGPipeline._need_rebuild("x/pgtest_rebuild_a") is True

    def test_nonempty_collection_skips_rebuild(self):
        store = PgVectorKnowledgeStore(
            persist_directory="x/pgtest_rebuild_a", embedding_function=_FakeEmbedding(),
        )
        store.add_texts(["hello world"], [{"doc_id": "d1"}])
        assert RAGPipeline._need_rebuild("x/pgtest_rebuild_a") is False


# ============ 2. _rebuild_db ============

class TestRebuildDb:

    def test_clears_collection_rows(self):
        store = PgVectorKnowledgeStore(
            persist_directory="x/pgtest_rebuild_a", embedding_function=_FakeEmbedding(),
        )
        store.add_texts(["a", "b"], [{"doc_id": "d1"}, {"doc_id": "d2"}])
        assert store.count() == 2

        RAGPipeline._rebuild_db("x/pgtest_rebuild_a")

        assert store.count() == 0
        assert RAGPipeline._need_rebuild("x/pgtest_rebuild_a") is True

    def test_only_targets_its_own_collection(self):
        """chunk 级重建不得误删同表内 doc_db 等其他 collection。"""
        store_a = PgVectorKnowledgeStore(
            persist_directory="x/pgtest_rebuild_a", embedding_function=_FakeEmbedding(),
        )
        store_b = PgVectorKnowledgeStore(
            persist_directory="x/pgtest_rebuild_b", embedding_function=_FakeEmbedding(),
        )
        store_a.add_texts(["a-content"], [{"doc_id": "da"}])
        store_b.add_texts(["b-content"], [{"doc_id": "db"}])

        RAGPipeline._rebuild_db("x/pgtest_rebuild_a")

        assert store_a.count() == 0
        assert store_b.count() == 1


# ============ 3. _load_or_create_db 分发 ============

class TestLoadOrCreateDispatch:

    @staticmethod
    def _bare_pipeline() -> RAGPipeline:
        """绕过重量级 __init__（加载语料+嵌入模型），只供三个静态语义方法使用。"""
        inst = object.__new__(RAGPipeline)
        inst.embedding = _FakeEmbedding()
        return inst

    def test_empty_collection_creates_via_create_fn(self):
        sentinel = object()
        calls: list[int] = []
        db = RAGPipeline._load_or_create_db(
            self._bare_pipeline(), "x/pgtest_rebuild_a",
            create_fn=lambda: (calls.append(1), sentinel)[1],
            db_type="测试",
        )
        assert db is sentinel and calls == [1]

    def test_nonempty_collection_reuses_existing_without_create(self):
        store = PgVectorKnowledgeStore(
            persist_directory="x/pgtest_rebuild_a", embedding_function=_FakeEmbedding(),
        )
        store.add_texts(["seed"], [{"doc_id": "d1"}])

        def _must_not_create():
            raise AssertionError("非空 collection 不应走 create_fn 重建")

        db = RAGPipeline._load_or_create_db(
            self._bare_pipeline(), "x/pgtest_rebuild_a",
            create_fn=_must_not_create, db_type="测试",
        )
        assert isinstance(db, PgVectorKnowledgeStore)
        assert db.count() == 1
