"""test_parent_child.py — Parent-Child 检索端上下文增强。"""
from langchain_core.documents import Document

from backend.rag.retrieval.retrievers import (
    attach_parent_context,
    attach_referenced_context,
)


def _leaf(chunk_id, parent_chunk_id):
    return Document(
        page_content=f"小 chunk {chunk_id}",
        metadata={"chunk_id": chunk_id, "granularity": "leaf", "parent_chunk_id": parent_chunk_id},
    )


def _parent(chunk_id):
    return Document(
        page_content=f"大 chunk {chunk_id} 完整上下文",
        metadata={"chunk_id": chunk_id, "granularity": "parent"},
    )


def test_attach_parent_fetches_missing_parent():
    """leaf 命中且 parent 不在结果中 → 拉取 parent 追加。"""
    leaf = _leaf("leaf1", "parent1")
    parent = _parent("parent1")
    lookup = lambda ids: [parent] if "parent1" in ids else []
    result = attach_parent_context([leaf], lookup)
    assert len(result) == 2
    assert any(d.metadata.get("chunk_id") == "parent1" for d in result)


def test_attach_parent_no_duplicate_when_parent_present():
    """parent 已在结果中 → 不重复拉取。"""
    leaf = _leaf("leaf1", "parent1")
    parent = _parent("parent1")
    result = attach_parent_context([leaf, parent], lambda ids: [])
    assert len(result) == 2


def test_attach_parent_skips_non_leaf():
    """非 leaf chunk（无 parent_chunk_id）→ 不处理。"""
    parent = _parent("parent1")
    result = attach_parent_context([parent], lambda ids: [])
    assert len(result) == 1


def test_attach_parent_lookup_failure_returns_original():
    """parent 拉取抛异常 → 降级返回原 docs（不丢检索结果）。"""
    leaf = _leaf("leaf1", "parent1")

    def boom(ids):
        raise RuntimeError("vectordb 查询失败")

    result = attach_parent_context([leaf], boom)
    assert len(result) == 1
    assert result[0].metadata.get("chunk_id") == "leaf1"


def test_attach_referenced_table_context():
    """条款引用费用表时，加载同文档内对应的完整表格 Leaf。"""
    payment = Document(
        page_content="上表所列费用按季度结算。",
        metadata={
            "chunk_id": "payment",
            "doc_id": "doc-1",
            "granularity": "leaf",
            "references": ["table_1"],
        },
    )
    fee_table = Document(
        page_content="项目：基础运维；小计：6294",
        metadata={"chunk_id": "fees", "doc_id": "doc-1", "table_id": "table_1"},
    )
    calls = []

    def lookup(doc_id, table_ids):
        calls.append((doc_id, table_ids))
        return [fee_table]

    result = attach_referenced_context([payment], lookup)
    assert calls == [("doc-1", ["table_1"])]
    assert [doc.metadata["chunk_id"] for doc in result] == ["payment", "fees"]
