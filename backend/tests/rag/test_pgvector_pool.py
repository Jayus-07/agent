"""PGVector 连接复用与 HNSW 查询参数回归测试。"""

from types import SimpleNamespace

import numpy as np


class FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()
        self.getconn_count = 0
        self.putconn_count = 0
        self.putconn_close = []

    def getconn(self):
        self.getconn_count += 1
        return self.connection

    def putconn(self, connection, close=False):
        self.putconn_count += 1
        self.putconn_close.append(close)


def _store_with_pool(pool):
    from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore

    store = object.__new__(PgVectorKnowledgeStore)
    store._pool = pool
    store._collection = "test"
    store._table = "rag_vectors"
    store.embedding_function = SimpleNamespace(
        embed_query=lambda query: np.zeros(3, dtype=np.float32).tolist()
    )
    # 本文件只验连接池/事务语义；d886f7c 起的索引身份守卫（FakeCursor 无
    # fetchone 会走「元数据暂不可校验」fail-closed）由 test_index_embedding_guard
    # 专门覆盖，这里短路避免喧宾夺主
    store._check_index_compat = lambda conn, identity=None: None
    return store


def test_connection_is_returned_to_pool(monkeypatch):
    import pgvector.psycopg2

    monkeypatch.setattr(pgvector.psycopg2, "register_vector", lambda conn: None)
    pool = FakePool()
    store = _store_with_pool(pool)

    with store._conn() as connection:
        assert connection is pool.connection

    assert pool.getconn_count == 1
    assert pool.putconn_count == 1
    assert pool.putconn_close == [False]
    assert pool.connection.commit_count == 1


def test_similarity_search_sets_transaction_local_hnsw_ef(monkeypatch):
    import pgvector.psycopg2
    import backend.rag.vectorstore.pgvector_store as store_module

    pool = FakePool()
    store = _store_with_pool(pool)
    monkeypatch.setattr(store_module, "VECTOR_HNSW_EF_SEARCH", 80)
    monkeypatch.setattr(pgvector.psycopg2, "register_vector", lambda conn: None)

    store.similarity_search_with_score("问题", k=5)

    assert any(
        "SET LOCAL hnsw.ef_search" in sql
        for sql, _ in pool.connection.cursor_obj.executed
    )


def test_vector_hnsw_ef_search_default_at_least_90():
    """出厂默认 ef_search ≥90 的回归锁（发票问召回丢失根因，trace 33153ba607db）。

    ef≤80 时 HNSW 图遍历会漏掉精确排名前 10 的块：本机 5440 块 chroma 库上
    发票问目标块（真 sim=0.62、精确 #6）不在 HNSW top9 → dense 3.0 权重名额
    被无关块占满 → 融合落选 → rerank 全截 → no_evidence 拒答；ef≥90 起
    top9 与精确检索一致。基线测试环境不设 VECTOR_HNSW_EF_SEARCH env；
    若因环境显式覆盖导致失败，属环境配置问题而非回归。
    """
    from backend.config.rag import VECTOR_HNSW_EF_SEARCH

    assert VECTOR_HNSW_EF_SEARCH >= 90


def test_rag_doc_candidate_k_default_at_least_100():
    """出厂默认 doc 级候选池 ≥100 的回归锁（发票问召回丢失根因）。

    Stage1 doc 级候选是 Stage2 chunk 检索与 BM25 路唯一的 doc_id 白名单
    来源（$in 过滤）：发票问在 227 文档库上目标 FAQ 文档 doc 级精确排名
    #69（sim 0.459，被 68 个报销主题文档压住），50 深度下落选 → 其全部
    chunk 被 $in 排除 → 融合池全是报销块 → rerank 全截 → no_evidence 拒答
    （trace 049a6872caba 打点实锤）。基线测试环境不设 RAG_DOC_CANDIDATE_K
    env；若因环境显式覆盖导致失败，属环境配置问题而非回归。
    """
    from backend.config.rag import RAG_DOC_CANDIDATE_K

    assert RAG_DOC_CANDIDATE_K >= 100
