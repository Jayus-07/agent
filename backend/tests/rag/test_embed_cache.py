"""3.1 embedding 结果缓存 — EmbeddingCache + indexer 集成。

契约：
- 命中/未命中/损坏 JSON → 向量/None/None
- put_many 软失败（Redis 异常不抛出）
- indexer._embed_with_retry：同文本第二次调用零真实嵌入，向量与首次一致
"""
import pytest
from langchain_core.documents import Document
from unittest.mock import MagicMock

import backend.rag.indexing.indexer as indexer_mod
from backend.rag.indexing.embed_cache import EmbeddingCache
from backend.rag.indexing.indexer import IncrementalIndexer


class FakeRedis:
    """最小 Redis 桩：mget/setex/pipeline。"""

    def __init__(self):
        self.store: dict = {}

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def pipeline(self, transaction=False):
        return self

    def setex(self, key, ttl, value):
        self.store[key] = value

    def execute(self):
        return []


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    import backend.infra.redis.client as redis_client
    monkeypatch.setattr(redis_client, "get_redis", lambda: r)
    return r


class TestEmbeddingCache:

    def test_roundtrip(self, fake_redis):
        cache = EmbeddingCache("bge-large")
        assert cache.get_many(["hello"]) == [None]
        cache.put_many(["hello"], [[0.1, 0.2]])
        assert cache.get_many(["hello"]) == [[0.1, 0.2]]

    def test_disabled_returns_all_miss(self, fake_redis, monkeypatch):
        cache = EmbeddingCache("bge-large")
        cache.put_many(["hello"], [[0.1]])
        monkeypatch.setattr(cache, "enabled", False)
        assert cache.get_many(["hello"]) == [None]

    def test_corrupt_value_treated_as_miss(self, fake_redis):
        cache = EmbeddingCache("bge-large")
        cache.put_many(["hello"], [[0.1]])
        # 破坏存储值
        key = list(fake_redis.store.keys())[0]
        fake_redis.store[key] = "not-json"
        assert cache.get_many(["hello"]) == [None]

    def test_put_failure_silent(self, monkeypatch):
        cache = EmbeddingCache("bge-large")

        class BoomRedis:
            def pipeline(self, transaction=False):
                raise RuntimeError("redis down")

        import backend.infra.redis.client as redis_client
        monkeypatch.setattr(redis_client, "get_redis", lambda: BoomRedis())
        cache.put_many(["a"], [[1.0]])  # 不应抛出


def _mk_indexer(embedding):
    idx = IncrementalIndexer.__new__(IncrementalIndexer)
    idx.embedding = embedding
    return idx


class TestIndexerCacheIntegration:

    def test_second_call_zero_real_embedding(self, fake_redis):
        calls = []
        emb = MagicMock()
        emb.model_name = "fake-model"

        def fake_docs(texts):
            calls.append(list(texts))
            return [[float(len(t))] for t in texts]

        emb.embed_documents.side_effect = fake_docs
        idx = _mk_indexer(emb)

        chunks = [Document(page_content=f"内容{i}", metadata={}) for i in range(3)]
        v1 = idx._embed_with_retry(chunks, parent_span=None, doc_summary="")
        assert len(calls) == 1 and len(calls[0]) == 3

        v2 = idx._embed_with_retry(chunks, parent_span=None, doc_summary="")
        assert len(calls) == 1, "第二次调用应全命中缓存，零真实嵌入"
        assert v1 == v2

    def test_summary_change_invalidates_cache(self, fake_redis):
        """前缀（doc_summary）变化 → 新键 → 重新嵌入（无脏读）。"""
        calls = []
        emb = MagicMock()
        emb.model_name = "fake-model"
        emb.embed_documents.side_effect = lambda texts: (
            calls.append(1), [[0.0]] * len(texts))[1]
        idx = _mk_indexer(emb)

        chunks = [Document(page_content="正文", metadata={})]
        idx._embed_with_retry(chunks, parent_span=None, doc_summary="摘要A")
        idx._embed_with_retry(chunks, parent_span=None, doc_summary="摘要B")
        assert len(calls) == 2, "前缀变化必须重新嵌入"

    def test_cache_metrics_reported(self, fake_redis):
        emb = MagicMock()
        emb.model_name = "fake-model"
        emb.embed_documents.side_effect = lambda texts: [[0.0]] * len(texts)
        idx = _mk_indexer(emb)

        chunks = [Document(page_content="正文", metadata={})]
        span1 = MagicMock(); span1.metrics = {}
        idx._embed_with_retry(chunks, parent_span=span1, doc_summary="")
        assert "embedding_cache_hit" not in span1.metrics, "全 miss（hits=0）不写 metrics"

        span2 = MagicMock(); span2.metrics = {}
        idx._embed_with_retry(chunks, parent_span=span2, doc_summary="")
        assert span2.metrics.get("embedding_cache_hit") == 1
        assert span2.metrics.get("embedding_cache_miss") == 0
