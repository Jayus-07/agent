"""级联删除测试 — BM25 清理 + _purge_doc_vectors 助手（数据一致性 问题 1/2）。"""
from unittest.mock import Mock, patch

from backend.rag.pipeline import RAGPipeline
from backend.app.api.routes.rag_documents import _purge_doc_vectors


def _bare_pipeline():
    """用 __new__ 造一个不触发 _init()（不加载模型/向量库）的 pipeline 骨架。"""
    p = RAGPipeline.__new__(RAGPipeline)
    p.bm25_store = None
    p.bm25 = None
    p.vectordb = None
    p.doc_db = None
    return p


def test_remove_documents_from_bm25_updates_retriever():
    p = _bare_pipeline()
    fake_store = Mock()
    new_retriever = Mock()
    fake_store.remove_documents.return_value = new_retriever
    p.bm25_store = fake_store
    p.bm25 = object()

    p.remove_documents_from_bm25(["doc1"])

    fake_store.remove_documents.assert_called_once()
    assert fake_store.remove_documents.call_args[0][0] == ["doc1"]
    assert p.bm25 is new_retriever


def test_remove_documents_from_bm25_noop_when_no_store():
    p = _bare_pipeline()
    p.remove_documents_from_bm25(["doc1"])  # 不抛异常
    assert p.bm25 is None


def test_purge_doc_vectors_calls_all_cleanup(tmp_path):
    p = _bare_pipeline()
    p.vectordb = Mock()
    p.doc_db = Mock()
    p.remove_documents_from_bm25 = Mock()

    f = tmp_path / "doc.md"
    f.write_text("# 测试", encoding="utf-8")

    with patch("backend.rag.indexing.chunk_store.get_chunk_store") as mock_cs:
        mock_cs.return_value = Mock()
        warnings: list[str] = []
        _purge_doc_vectors("doc1", str(f), p, warnings)

    p.vectordb.delete.assert_called_once_with(where={"doc_id": "doc1"})
    p.doc_db.delete.assert_called_once_with(where={"doc_id": "doc1"})
    mock_cs.return_value.delete_by_doc_id.assert_called_once_with("doc1")
    # P0-2: 删除时传入 file_path 作为 BM25 第二过滤键（doc_id 协议分裂时按文件名兜底命中）
    p.remove_documents_from_bm25.assert_called_once_with(["doc1"], file_paths=[str(f)])
    assert not f.exists()  # 原文件已删
    assert warnings == []
