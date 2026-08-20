"""P2 embedding 批量化 — IncrementalIndexer._embed_with_retry 行为测试。

覆盖四条语义：
- 批路径：chunks 按 EMBED_BATCH_SIZE 分批调 embed_documents
- 批重试耗尽 → 降级逐条 embed_query（隔离单点失败，保留逐 chunk span 语义）
- embedding 实现无 embed_documents（如 FakeEmbedding）→ 直接逐条路径
- 单 chunk 失败不影响同批其他 chunk（失败向量不出现在返回值）
"""
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

import backend.rag.indexing.indexer as indexer_mod
from backend.rag.indexing.indexer import IncrementalIndexer


def _mk_indexer(embedding):
    """跳过完整构造链，只注入 _embed_with_retry 所需依赖。"""
    idx = IncrementalIndexer.__new__(IncrementalIndexer)
    idx.embedding = embedding
    return idx


def _chunks(n):
    return [Document(page_content=f"text {i}", metadata={"doc_id": f"d{i}"})
            for i in range(n)]


@pytest.fixture
def patched(monkeypatch):
    """小批大小 + mock trace_collector，避免真实 span 落库。"""
    monkeypatch.setattr(indexer_mod, "EMBED_BATCH_SIZE", 2)
    monkeypatch.setattr(indexer_mod, "trace_collector", MagicMock())
    return monkeypatch


class TestBatchPath:

    def test_batch_embed_called_with_batch_splits(self, patched):
        calls = []

        def fake_docs(texts):
            calls.append(list(texts))
            return [[float(len(t))] for t in texts]

        emb = MagicMock()
        emb.embed_documents.side_effect = fake_docs
        idx = _mk_indexer(emb)

        vecs = idx._embed_with_retry(_chunks(5), parent_span=None)
        assert len(vecs) == 5
        # 5 chunks 按 batch=2 切分为 2/2/1 三批
        assert [len(c) for c in calls] == [2, 2, 1]

    def test_simulated_question_prefix_assembled(self, patched):
        """Document Expansion：带 simulated_questions 的 chunk 拼前缀后入 embedding。"""
        seen = []

        def capture(texts):
            seen.extend(texts)
            return [[0.0]] * len(texts)

        emb = MagicMock()
        emb.embed_documents.side_effect = capture
        idx = _mk_indexer(emb)

        chunk = Document(page_content="正文",
                         metadata={"simulated_questions": ["Q1", "Q2"]})
        idx._embed_with_retry([chunk], parent_span=None)
        assert seen[0].startswith("【相关问题】Q1 | Q2")
        assert seen[0].endswith("正文")

    def test_empty_chunks_returns_empty(self, patched):
        idx = _mk_indexer(MagicMock())
        assert idx._embed_with_retry([], parent_span=None) == []


class TestBatchFallback:

    def test_exhausted_batch_degrades_to_single(self, patched):
        """embed_documents 持续返回非法长度 → 批重试耗尽 → 降级逐条全部成功。"""
        emb = MagicMock()
        emb.embed_documents.return_value = [[0.0]]  # 长度恒不匹配 → ValueError
        emb.embed_query.side_effect = lambda t: [1.0, 2.0]
        idx = _mk_indexer(emb)

        vecs = idx._embed_with_retry(_chunks(2), parent_span=None)
        assert len(vecs) == 2
        assert emb.embed_documents.call_count == indexer_mod.EMBED_RETRY_MAX
        assert emb.embed_query.call_count == 2

    def test_single_chunk_failure_isolated(self, patched):
        """批降级后单点失败只丢该 chunk，其余照常入库，且失败 span 被记录。"""
        emb = MagicMock()
        emb.embed_documents.side_effect = RuntimeError("batch down")

        def single(text):
            if text == "text 0":
                raise RuntimeError("bad chunk")
            return [9.9]

        emb.embed_query.side_effect = single
        idx = _mk_indexer(emb)

        vecs = idx._embed_with_retry(_chunks(2), parent_span=None)
        assert vecs == [[9.9]], "失败 chunk 的向量不能混入成功列表"
        # 失败 chunk 记录了 error span（trace_collector 已 mock）
        tc = indexer_mod.trace_collector
        assert tc.start_span.called
        assert tc.end_span.called


class TestNoBatchApi:

    def test_embedding_without_embed_documents_uses_single_path(self, patched):
        """仅有 embed_query 的实现（如 e2e FakeEmbedding）→ 逐条路径。"""

        class OnlyQuery:
            def __init__(self):
                self.n = 0

            def embed_query(self, text):
                self.n += 1
                return [1.0]

        emb = OnlyQuery()
        idx = _mk_indexer(emb)
        vecs = idx._embed_with_retry(_chunks(3), parent_span=None)
        assert len(vecs) == 3
        assert emb.n == 3
