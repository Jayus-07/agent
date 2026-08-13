"""test_bm25_sync.py — BM25 索引与文档目录一致性检测。"""
from langchain_core.documents import Document

from backend.rag.retrieval.bm25_store import source_files_out_of_sync


def _d(name):
    return Document(page_content="x", metadata={"source_file": name})


def test_source_files_out_of_sync_detects_residual():
    """索引里有已删除文档（残留）→ 需重建。"""
    indexed = [_d("数据治理规范.md"), _d("售后流程.md")]
    current = [_d("售后流程.md")]
    assert source_files_out_of_sync(indexed, current) is True


def test_source_files_out_of_sync_detects_missing():
    """索引缺失当前文档 → 需重建。"""
    indexed = [_d("售后流程.md")]
    current = [_d("售后流程.md"), _d("01_FAQ.md")]
    assert source_files_out_of_sync(indexed, current) is True


def test_source_files_in_sync():
    """索引与当前文档集合一致 → 不重建。"""
    indexed = [_d("a.md"), _d("b.md")]
    current = [_d("b.md"), _d("a.md")]  # 顺序无关
    assert source_files_out_of_sync(indexed, current) is False
