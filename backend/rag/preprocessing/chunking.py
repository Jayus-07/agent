"""chunking.py — 文档类型感知切分门面层（Facade）。

保留全部历史公共函数、策略类与常量的导入契约，底层委托给模块化的 chunkers 包。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from langchain_core.documents import Document

from backend.config import CHUNK_OVERLAP, LEAF_CHUNK_TOKENS, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.chunkers.audit import coverage_audit
from backend.rag.preprocessing.chunkers.base import (
    _SEPARATORS,
    _ast_has_sections,
    _chunk_id,
    _cosine_similarity,
    _detect_boundaries,
    _doc_title,
    _doc_title_node,
    _enrich,
    _hard_split_by_budget,
    _has_legal_clauses,
    _has_qa_nodes,
    _has_table_nodes,
    _is_legal_clause,
    _is_section_heading,
    _iter_leaf_nodes_with_section_path,
    _iter_leaves_with_section,
    _make_doc,
    _make_leaf_chunk,
    _make_leaf_chunks_bounded,
    _merge_small_texts,
    _merge_small_with_section,
    _section_ancestor_titles,
    _split_sentences,
    _split_sentences_keep_punct,
    _split_to_budget,
    count_tokens,
)
from backend.rag.preprocessing.chunkers.router import (
    ChunkRouteDecision,
    ChunkStrategyRouter,
    ContentUnitDispatchStrategy,
    _local_strategy_class,
    _recursive_content_units,
    _top_level_content_units,
)
from backend.rag.preprocessing.chunkers.stamper import (
    _MAX_PAGE_SPAN_FOR_STAMP,
    _merge_source_locations,
    _namespace_content_unit_chunks,
    _node_source_location,
    _source_location_metadata,
    ensure_unique_chunk_ids,
    stamp_chunk_pages,
    stamp_chunk_structure,
)
from backend.rag.preprocessing.chunkers.strategies import (
    STRUCTURE_STRATEGIES,
    FinancialTableChunkStrategy,
    FixedSizeChunkStrategy,
    LegalChunkStrategy,
    QAChunkStrategy,
    RecursiveChunkStrategy,
    SemanticChunkStrategy,
    StepChunkStrategy,
    StructureChunkStrategy,
    _split_unstructured,
)
from backend.rag.preprocessing.chunkers.table_handler import (
    _attach_virtual_parents,
    _split_table_node,
    is_table_chunk,
)

if TYPE_CHECKING:
    from backend.rag.preprocessing.structure_analyzer import StructureReport

# 表格 chunk 类型标识单事实源定义（供契约测试与调用方使用）
CHUNK_TYPE_TABLE_ROW = "table_row"
CHUNK_TYPE_TABLE_SUMMARY = "table_summary"
CHUNK_TYPE_TABLE_FALLBACK = "table_fallback"
CHUNK_TYPE_TABLE_HEADER = "table_header"
TABLE_CHUNK_TYPES: tuple[str, ...] = (
    CHUNK_TYPE_TABLE_ROW, CHUNK_TYPE_TABLE_SUMMARY, CHUNK_TYPE_TABLE_FALLBACK,
    CHUNK_TYPE_TABLE_HEADER,
)
TABLE_CHUNK_PREFIX = "table_"

__all__ = [
    # 配置与常量
    "LEAF_CHUNK_TOKENS",
    "PARENT_CHUNK_TOKENS",
    "CHUNK_OVERLAP",
    "_SEPARATORS",
    "_MAX_PAGE_SPAN_FOR_STAMP",
    "CHUNK_TYPE_TABLE_ROW",
    "CHUNK_TYPE_TABLE_SUMMARY",
    "CHUNK_TYPE_TABLE_FALLBACK",
    "CHUNK_TYPE_TABLE_HEADER",
    "TABLE_CHUNK_TYPES",
    "TABLE_CHUNK_PREFIX",
    # 策略类与字典
    "FixedSizeChunkStrategy",
    "RecursiveChunkStrategy",
    "StructureChunkStrategy",
    "StepChunkStrategy",
    "LegalChunkStrategy",
    "QAChunkStrategy",
    "SemanticChunkStrategy",
    "FinancialTableChunkStrategy",
    "ContentUnitDispatchStrategy",
    "STRUCTURE_STRATEGIES",
    # 路由与决策
    "ChunkStrategyRouter",
    "ChunkRouteDecision",
    # 审计与打标
    "coverage_audit",
    "stamp_chunk_pages",
    "stamp_chunk_structure",
    "ensure_unique_chunk_ids",
    "is_table_chunk",
    # 内部工具与算法
    "_split_unstructured",
    "_split_table_node",
    "_attach_virtual_parents",
    "_hard_split_by_budget",
    "_split_to_budget",
    "_chunk_id",
    "_enrich",
    "_make_doc",
    "_split_sentences",
    "_split_sentences_keep_punct",
    "_cosine_similarity",
    "_detect_boundaries",
    "_doc_title_node",
    "_doc_title",
    "_section_ancestor_titles",
    "_iter_leaf_nodes_with_section_path",
    "_is_section_heading",
    "_is_legal_clause",
    "_has_legal_clauses",
    "_has_table_nodes",
    "_has_qa_nodes",
    "_ast_has_sections",
    "_make_leaf_chunk",
    "_make_leaf_chunks_bounded",
    "_merge_small_texts",
    "_merge_source_locations",
    "_node_source_location",
    "_source_location_metadata",
    "_namespace_content_unit_chunks",
    "_iter_leaves_with_section",
    "_merge_small_with_section",
    "_recursive_content_units",
    "_top_level_content_units",
    "_local_strategy_class",
    "count_tokens",
    "DocumentAST",
    "DocumentNode",
    "LEAF_TYPES",
    "walk",
]
