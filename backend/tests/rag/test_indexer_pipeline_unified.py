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


def test_indexer_supported_exts_includes_pdf_docx():
    """indexer 扫描白名单必须包含 pdf/docx（否则增量索引跳过 PDF/DOCX 文档）。

    缺陷 F 根因：_scan_disk 用 SUPPORTED_EXTS 过滤，若漏 .pdf/.docx，
    增量索引时 PDF/DOCX 被跳过，Phase 2 的 PDF/DOCX 入库在 indexer 层失效。
    """
    assert ".pdf" in IncrementalIndexer.SUPPORTED_EXTS
    assert ".docx" in IncrementalIndexer.SUPPORTED_EXTS


def test_derive_kb_id_from_subdirectory():
    """_derive_kb_id 应按第一级子目录名派生 kb_id（kb 隔离的基础）。

    缺陷 G 根因：RAGPipeline 实例化 indexer 用默认 kb_id="policy_general"，
    导致 _index_file 不走 _derive_kb_id，所有文档被标成同一个 kb。
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "rag_test_kb"))
        f = os.path.join(td, "rag_test_kb", "doc.pdf")
        # 用 __new__ 绕过 __init__（无需 vectordb/doc_db 等）
        indexer = IncrementalIndexer.__new__(IncrementalIndexer)
        indexer.docs_dir = td
        assert indexer._derive_kb_id(f) == "rag_test_kb"


def test_pipeline_indexer_passes_derive_kb_id():
    """RAGPipeline 实例化 indexer 必须传 kb_id='default'（触发按路径派生）。"""
    from backend.rag.pipeline import RAGPipeline

    source = inspect.getsource(RAGPipeline._init_vector_dbs_incremental)
    assert 'kb_id="default"' in source or "kb_id='default'" in source, (
        "RAGPipeline 实例化 indexer 未传 kb_id='default'，"
        "所有文档会被标成默认 policy_general，kb 隔离失效"
    )


def test_index_file_inner_uses_kb_id_param_not_self():
    """_index_file_inner 的 doc_meta 必须用 kb_id 参数，而非 self.kb_id。

    缺陷 H 根因：doc_meta["kb_id"] 用了 self.kb_id（默认 "default"），
    覆盖了 _index_file 派生的 kb_id，导致所有文档 kb_id 变成 "default"。
    """
    source = inspect.getsource(IncrementalIndexer._index_file_inner)
    assert '"kb_id": kb_id' in source, (
        "_index_file_inner 的 doc_meta 未用 kb_id 参数，kb 隔离会失效"
    )
    assert '"kb_id": self.kb_id' not in source, (
        "_index_file_inner 仍用 self.kb_id，覆盖了派生值"
    )
