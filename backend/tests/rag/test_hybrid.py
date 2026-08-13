"""test_hybrid.py — 混合检索的 metadata_filter 过滤。"""
from langchain_core.documents import Document

from backend.rag.retrieval.hybrid import _filter_by_metadata


def _doc(meta):
    return Document(page_content="x", metadata=meta)


def test_filter_by_metadata_kb_id():
    """BM25 结果必须按 kb_id 过滤，隔离不同知识库的文档。"""
    docs = [
        _doc({"kb_id": "rag_test_kb", "doc_id": "a"}),
        _doc({"kb_id": "policy_general", "doc_id": "b"}),
        _doc({"kb_id": "rag_test_kb", "doc_id": "c"}),
    ]
    filtered = _filter_by_metadata(docs, {"kb_id": "rag_test_kb"})
    assert [d.metadata["doc_id"] for d in filtered] == ["a", "c"]


def test_filter_by_metadata_no_filter_keeps_all():
    """无 filter 时原样返回。"""
    docs = [_doc({"kb_id": "rag_test_kb"}), _doc({"kb_id": "policy_general"})]
    assert _filter_by_metadata(docs, None) == docs
    assert _filter_by_metadata(docs, {}) == docs


def test_filter_by_metadata_multi_kv():
    """多条件过滤：所有 kv 都需匹配。"""
    docs = [
        _doc({"kb_id": "rag_test_kb", "doc_type": "policy"}),
        _doc({"kb_id": "rag_test_kb", "doc_type": "faq"}),
    ]
    filtered = _filter_by_metadata(docs, {"kb_id": "rag_test_kb", "doc_type": "faq"})
    assert len(filtered) == 1
    assert filtered[0].metadata["doc_type"] == "faq"
