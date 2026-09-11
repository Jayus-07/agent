"""chunking 可靠性修复的回归测试。

覆盖：
1. _enrich 截断留痕：超上限截断时保留 chunk 打 chunks_truncated 标记
   （indexer 据此写入 registry quality_issues），未截断不污染 metadata
2. _enrich 支持独立上限（财务文档 FINANCIAL_MAX_CHUNKS_PER_DOC 生效）
3. 语义切分句子分批尊重 embedding 声明的 embed_batch_size
   （云端 DashScope 单请求上限，与 indexer 主路径同口径）
"""
from langchain_core.documents import Document

from backend.rag.preprocessing.chunking import (
    _enrich,
    SemanticChunkStrategy,
)


def _chunks(n):
    return [Document(page_content=f"chunk {i}", metadata={})
            for i in range(n)]


class TestEnrichTruncation:

    def test_truncation_marks_all_kept_chunks(self):
        kept = _enrich(_chunks(5), "big.md", max_chunks=3)
        assert len(kept) == 3
        assert all(c.metadata.get("chunks_truncated") == "true" for c in kept)

    def test_no_truncation_no_marker(self):
        kept = _enrich(_chunks(3), "ok.md", max_chunks=5)
        assert all("chunks_truncated" not in c.metadata for c in kept)

    def test_chunk_index_renumbered_after_truncation(self):
        kept = _enrich(_chunks(5), "big.md", max_chunks=2)
        assert [c.metadata["chunk_index"] for c in kept] == [0, 1]

    def test_default_limit_is_max_chunks_per_doc(self, monkeypatch):
        import backend.config as config_pkg
        monkeypatch.setattr(config_pkg, "MAX_CHUNKS_PER_DOC", 2)
        kept = _enrich(_chunks(4), "big.md")
        assert len(kept) == 2
        assert all(c.metadata.get("chunks_truncated") == "true" for c in kept)


class _RecordingEmbedding:
    """记录每次 embed_documents 的批大小；embed_batch_size 模拟云端声明。"""

    embed_batch_size = 3

    def __init__(self):
        self.batch_sizes = []

    def embed_documents(self, texts):
        self.batch_sizes.append(len(texts))
        return [[float(len(t))] for t in texts]


class TestSemanticSentenceBatching:

    def test_respects_declared_embed_batch_size(self):
        emb = _RecordingEmbedding()
        sentences = [f"句子{i}。" for i in range(10)]
        vecs = SemanticChunkStrategy._embed_sentences_batched(sentences, emb)
        assert len(vecs) == 10
        # 10 句按声明的批大小 3 切分为 3/3/3/1
        assert emb.batch_sizes == [3, 3, 3, 1]

    def test_falls_back_to_config_batch_size(self):
        emb = _RecordingEmbedding()
        del emb.__class__.embed_batch_size  # 模拟未声明的实现
        emb.embed_batch_size = "invalid"    # 非法类型也要兜底
        sentences = [f"句子{i}。" for i in range(5)]
        SemanticChunkStrategy._embed_sentences_batched(sentences, emb)
        from backend.config import SEMANTIC_EMBED_BATCH_SIZE
        assert sum(emb.batch_sizes) == 5
        assert all(b <= SEMANTIC_EMBED_BATCH_SIZE for b in emb.batch_sizes)
