"""P1-5 测试：BM25 增量同步验证。

之前 review 认为 BM25 没随上传同步更新是错的 —— 实际 indexer.py:_index_file_inner
在 bm25_store 不为 None 且 chunks 非空时调用
`bm25_store.replace_documents(chunks, k=..., doc_id=..., file_path=...)`。

本测试真实调用 _index_file_inner 验证契约（只 mock 外部边界：
解析入口 parse_and_chunk_full / LLM 元数据 / embedding / chunk_store / 各存储）：
  1. bm25_store 配置时 → 从向量库重建 BM25，并携带替换旧 chunk 的排除集
  2. BM25 重建失败 → 清理本次新向量并中断索引主流程
  3. bm25_store=None（启动期 sync 场景）→ 跳过 BM25，索引正常完成
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

from backend.rag.indexing.indexer import IncrementalIndexer

# 足够长且全中文，避免被 ChunkFilter 以 too_short / 低中文占比拒绝
_BODY = "这是用于验证索引流水线与 BM25 增量同步契约的正文内容。" * 12


def _make_indexer(tmp_path, bm25_store) -> IncrementalIndexer:
    indexer = IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=MagicMock(),
        doc_db=MagicMock(),
        embedding=MagicMock(),
        registry=MagicMock(),
        bm25_store=bm25_store,
    )
    # dedup 检查必须不命中缓存（MagicMock 默认真值会被当成已存在记录）
    indexer.registry.get_by_path.return_value = None
    # 向量库必须产出 chunk_id，否则 ⑨ 阶段按"0 chunk 假成功"抛 ChunkingEmptyError
    indexer.vectordb.add_documents.return_value = ["chunk_0"]
    indexer.doc_db.add_texts.return_value = ["docdb_1"]
    return indexer


def _run_inner(indexer: IncrementalIndexer, tmp_path) -> dict:
    """真实执行 _index_file_inner；仅对外部边界打桩（解析/LLM/embedding/chunk_store）。"""
    target = tmp_path / "doc.md"
    target.write_text(f"# 标题\n{_BODY}", encoding="utf-8")

    fake_chunk = Document(page_content=_BODY, metadata={"source": str(target)})
    with patch("backend.rag.preprocessing.pipeline.parse_and_chunk",
               return_value=[fake_chunk]), \
         patch("backend.rag.indexing.chunk_store.get_chunk_store",
               return_value=MagicMock()), \
         patch.object(IncrementalIndexer, "_build_doc_metadata",
                      new=AsyncMock(return_value={})), \
         patch.object(IncrementalIndexer, "_embed_with_retry",
                      return_value=[[0.0, 0.0, 0.0]]):
        return indexer._index_file_inner(
            str(target), kb_id="policy_general", doc_id="doc1", file_hash="h1")


class TestBM25IncrementalSync:

    def test_rebuild_from_vectorstore_called_when_store_present(self, tmp_path):
        """有 bm25_store + chunks 时，必须从向量库重建 BM25。"""
        from backend.config.rag import BM25_CANDIDATE_K

        bm25_store = MagicMock()
        bm25_store.rebuild_from_vectorstore = MagicMock()
        indexer = _make_indexer(tmp_path, bm25_store)

        result = _run_inner(indexer, tmp_path)

        bm25_store.rebuild_from_vectorstore.assert_called_once()
        args, kwargs = bm25_store.rebuild_from_vectorstore.call_args
        assert args[0] is indexer.vectordb
        assert kwargs["k"] == BM25_CANDIDATE_K
        assert kwargs["exclude_ids"] == set()
        assert result["chunk_count"] == 1

    def test_bm25_sync_failure_cleans_up_and_breaks_indexing(self, tmp_path):
        """BM25 重建失败时不得把向量或 registry 标记为成功。"""
        bm25_store = MagicMock()
        bm25_store.rebuild_from_vectorstore.side_effect = RuntimeError("BM25 disk full")
        indexer = _make_indexer(tmp_path, bm25_store)

        with pytest.raises(RuntimeError, match="BM25 disk full"):
            _run_inner(indexer, tmp_path)

        indexer.registry.register.assert_not_called()
        indexer.vectordb.delete.assert_called()

    def test_bm25_skipped_when_store_none(self, tmp_path):
        """bm25_store=None（启动期 sync）时跳过 BM25 阶段，索引正常完成。"""
        indexer = _make_indexer(tmp_path, None)

        result = _run_inner(indexer, tmp_path)

        assert result["chunk_count"] == 1
        indexer.registry.register.assert_called_once()
