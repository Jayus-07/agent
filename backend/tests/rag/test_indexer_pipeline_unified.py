"""test_indexer_pipeline_unified.py — indexer 统一走新流水线验证。

覆盖：
1. indexer 不再 import PyPDFLoader / Docx2txtLoader / TextLoader
2. indexer 必须调 parse_and_chunk
3. indexer 不再持有 raw_docs 变量（避免反向构造）
"""
import inspect

from backend.rag.indexing.indexer import IncrementalIndexer


def test_indexer_does_not_use_langchain_pdf_loader():
    """indexer 不再 import PyPDFLoader / Docx2txtLoader / TextLoader。"""
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    assert "PyPDFLoader" not in source
    assert "Docx2txtLoader" not in source
    assert "TextLoader" not in source


def test_indexer_calls_parse_and_chunk():
    """indexer 必须调 parse_and_chunk 切分。"""
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    assert "parse_and_chunk" in source


def test_indexer_no_raw_docs_variable():
    """indexer 不再持有 raw_docs 变量（避免反向构造）。"""
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    assert "raw_docs" not in source, (
        "indexer 仍持有 raw_docs 变量，应统一改为 chunks"
    )
