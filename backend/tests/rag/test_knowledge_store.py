"""test_knowledge_store.py — ChromaKnowledgeStore metadata 清洗。

ChromaDB 约束：metadata 值只能是标量（str/int/float/bool），
list 必须非空，dict 和 None 都不允许。chunk 的 section_path 是 list，
Recursive/QA strategy 产空 list → 入库前必须清洗，否则 upsert 崩溃。
"""
from backend.rag.vectorstore.knowledge_store import _sanitize_metadata


def test_sanitize_empty_list_to_empty_string():
    """空 list → 空字符串（ChromaDB 拒绝空 list metadata）。"""
    cleaned = _sanitize_metadata({"section_path": [], "title": "x"})
    assert cleaned["section_path"] == ""
    assert cleaned["title"] == "x"


def test_sanitize_nested_to_json():
    """非空 list / dict → JSON 字符串（ChromaDB 只支持标量）。"""
    cleaned = _sanitize_metadata({
        "section_path": ["a", "b"],
        "complexity": {"score": 1},
    })
    assert cleaned["section_path"] == '["a", "b"]'
    assert cleaned["complexity"] == '{"score": 1}'


def test_sanitize_none_to_empty_string():
    """None → 空字符串（ChromaDB 拒绝 None）。"""
    cleaned = _sanitize_metadata({"tags": None, "title": "x"})
    assert cleaned["tags"] == ""
    assert cleaned["title"] == "x"


def test_sanitize_scalar_unchanged():
    """标量值原样保留。"""
    cleaned = _sanitize_metadata({"doc_id": "abc", "tokens": 10, "score": 0.9})
    assert cleaned == {"doc_id": "abc", "tokens": 10, "score": 0.9}
