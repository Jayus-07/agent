"""混合文档局部切分的 Chunk、Parent 与表格 ID 命名空间回归。"""

from langchain_core.documents import Document

from backend.rag.preprocessing.chunking import (
    _namespace_content_unit_chunks,
    ensure_unique_chunk_ids,
)
from backend.rag.preprocessing.quality_gate import audit_chunk_integrity


def _unit_chunks() -> list[Document]:
    """模拟局部策略重置章节/表格编号后生成的重复 ID。"""
    return [
        Document("表格摘要", metadata={
            "chunk_id": "same-parent", "granularity": "parent",
            "section_path": ["重复章节"], "table_id": "same-parent",
        }),
        Document("相同表头", metadata={
            "chunk_id": "same-leaf", "granularity": "leaf",
            "parent_chunk_id": "same-parent", "section_path": ["重复章节"],
            "table_id": "same-parent",
        }),
        Document("表格摘要", metadata={
            "chunk_id": "same-parent", "granularity": "parent",
            "section_path": ["重复章节"], "table_id": "same-parent",
        }),
        Document("相同表头", metadata={
            "chunk_id": "same-leaf", "granularity": "leaf",
            "parent_chunk_id": "same-parent", "section_path": ["重复章节"],
            "table_id": "same-parent",
        }),
    ]


def test_content_unit_namespaces_duplicate_chunk_parent_and_table_ids():
    unit_a = _unit_chunks()
    unit_b = _unit_chunks()

    _namespace_content_unit_chunks(unit_a, "report.pdf", "1:重复章节")
    _namespace_content_unit_chunks(unit_b, "report.pdf", "2:重复章节")
    combined = [*unit_a, *unit_b]

    integrity = audit_chunk_integrity(combined)

    assert integrity["duplicate_chunk_id_count"] == 0
    assert integrity["invalid_parent_reference_count"] == 0
    for unit in (unit_a, unit_b):
        parent_ids = {
            chunk.metadata["chunk_id"]
            for chunk in unit
            if chunk.metadata["granularity"] == "parent"
        }
        leaves = [
            chunk for chunk in unit
            if chunk.metadata["granularity"] == "leaf"
        ]
        assert len({chunk.metadata["table_id"] for chunk in unit}) == 2
        assert all(chunk.metadata["parent_chunk_id"] in parent_ids for chunk in leaves)
        assert all(
            chunk.metadata["table_id"] == chunk.metadata["parent_chunk_id"]
            for chunk in leaves
        )


def test_final_integrity_repair_marks_and_disambiguates_duplicate_ids():
    chunks = _unit_chunks()

    repaired = ensure_unique_chunk_ids(chunks, "report.pdf")

    assert repaired is True
    integrity = audit_chunk_integrity(chunks)
    assert integrity["duplicate_chunk_id_count"] == 0
    assert integrity["invalid_parent_reference_count"] == 0
    assert all(chunk.metadata.get("chunk_id_collision_resolved") == "true" for chunk in chunks)
    assert all("content_unit_id" not in chunk.metadata for chunk in chunks)
