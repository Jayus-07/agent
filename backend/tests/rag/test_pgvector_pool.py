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
