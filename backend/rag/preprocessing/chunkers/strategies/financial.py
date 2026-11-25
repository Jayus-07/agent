"""financial.py — 财务表格专用切分策略（FinancialTableChunkStrategy）。

表级摘要(parent) + 行级 kv(leaf) 双层索引，并补充 reporting_period/fiscal_year/is_latest 属性。
非表格正文叶子遵循 Structure 有界 Parent/Leaf 机制。
"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import (
    DocumentAST, DocumentNode, LEAF_TYPES,
)
from backend.rag.preprocessing.chunkers.base import (
    _chunk_id, _doc_title, _doc_title_node, _enrich,
    _hard_split_by_budget, _make_doc, count_tokens,
    get_leaf_chunk_tokens, get_parent_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.stamper import (
    _node_source_location, _source_location_metadata,
)
from backend.rag.preprocessing.chunkers.strategies.structure import StructureChunkStrategy
from backend.rag.preprocessing.chunkers.table_handler import (
    _split_table_node, is_table_chunk,
)


class FinancialTableChunkStrategy:
    """财务表格专用切分：表级摘要(parent) + 行级 kv(leaf) 双层索引。

    替代 StructureChunkStrategy 对 financial 文档的处理。原策略把整张表
    当作一个 leaf chunk，表格超过 LEAF_CHUNK_TOKENS 时被
    RecursiveCharacterTextSplitter 按字符硬切，会把一行财务数据从中间切断。

    本策略按行自然边界拆分：
      - Layer 1: 表级摘要(parent) — NL 描述表名/行列数/列名，供语义检索
      - Layer 2: 行级 kv(leaf) — 每行一个 chunk，kv 格式供精确数值检索
    行级 chunk 通过 parent_chunk_id 关联表级摘要，metadata 含
    numeric_values（规范化数值）支持按数值范围过滤。

    非表格 leaf（段落等）走 StructureChunkStrategy 逻辑。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        from backend.config import FINANCIAL_TABLE_ROWS_PER_CHUNK

        chunks: List[Document] = []
        # 根引导块（P0-02 同构，2026-10-13）：首个标题前的直接子叶
        # （含 PDF 表格-标题混排时挂 root 的表格）不再静默丢失
        root_leaves = [n for n in ast.root.children if n.type in LEAF_TYPES]
        if root_leaves:
            root_pseudo = DocumentNode(
                type="section", text=_doc_title(ast), level=0,
                children=list(root_leaves))
            has_table = any(
                n.type == "table" and n.rows for n in root_leaves)
            if has_table:
                chunks.extend(self._split_with_tables(
                    root_pseudo, [], file_path, FINANCIAL_TABLE_ROWS_PER_CHUNK,
                ))
                non_table = [
                    n for n in root_leaves
                    if not (n.type == "table" and n.rows)
                ]
            else:
                non_table = root_leaves
            if non_table:
                self._emit_plain_group(
                    chunks, file_path, root_pseudo, [], non_table,
                )
        for sec, path in self._sections(ast):
            leaves = list(self._leaves(sec))
            section_anchor = ".".join(path)
            has_table = any(n.type == "table" and n.rows for n in leaves)

            if not has_table:
                # 无表格 → 走原 StructureChunkStrategy 逻辑
                sec_text = StructureChunkStrategy._section_text(sec)
                parent_budget = get_parent_chunk_tokens()
                if count_tokens(sec_text) <= parent_budget:
                    parent_id = _chunk_id(file_path, f"parent:{section_anchor}", sec_text)
                    StructureChunkStrategy._emit_parent(chunks, file_path, sec, path, sec_text, parent_id)
                    for leaf in leaves:
                        StructureChunkStrategy._emit_leaf(chunks, file_path, sec, path, leaf,
                            _chunk_id(file_path, f"leaf:{section_anchor}", leaf.text), parent_id)
                else:
                    groups = StructureChunkStrategy._group_leaves_by_tokens(leaves, parent_budget)
                    for gi, group in enumerate(groups):
                        group_text = "\n".join([sec.text] + [leaf.text for leaf in group])
                        parent_id = _chunk_id(file_path, f"parent:{section_anchor}:{gi}", group_text)
                        StructureChunkStrategy._emit_parent(chunks, file_path, sec, path, group_text, parent_id)
                        for leaf in group:
                            StructureChunkStrategy._emit_leaf(chunks, file_path, sec, path, leaf,
                                _chunk_id(file_path, f"leaf:{section_anchor}:{gi}", leaf.text), parent_id)
                continue

            # 有表格 → 财务表格专用切分
            chunks.extend(self._split_with_tables(
                sec, path, file_path, FINANCIAL_TABLE_ROWS_PER_CHUNK,
            ))
        # 财务行级切分产出多，走独立上限（此前 FINANCIAL_MAX_CHUNKS_PER_DOC
        # 定义了但从未被使用，财务文档被通用 5000 上限截断，与配置意图相悖）
        from backend.config import FINANCIAL_MAX_CHUNKS_PER_DOC
        return _enrich(chunks, file_path, max_chunks=FINANCIAL_MAX_CHUNKS_PER_DOC)

    @staticmethod
    def _emit_plain_group(chunks: list, file_path: str, sec, path: list,
                          leaves: list) -> None:
        """无表格叶子组 → Structure 语义的 parent + leaf（根引导块用）。"""
        sec_text = "\n".join(
            ([sec.text] if sec.text else []) + [l.text for l in leaves])
        anchor = ".".join(path)
        parent_budget = get_parent_chunk_tokens()
        leaf_budget = get_leaf_chunk_tokens()
        if count_tokens(sec_text) <= parent_budget:
            parent_id = _chunk_id(file_path, f"parent:{anchor}", sec_text)
            StructureChunkStrategy._emit_parent(
                chunks, file_path, sec, path, sec_text, parent_id)
            for leaf in leaves:
                StructureChunkStrategy._emit_leaf(
                    chunks, file_path, sec, path, leaf,
                    _chunk_id(file_path, f"leaf:{anchor}", leaf.text), parent_id)
        else:
            split_group_id = _chunk_id(
                file_path, f"split:financial:{anchor}", sec_text,
            )
            source_spans: list[tuple[int, int, DocumentNode]] = []
            if sec.text:
                source_spans.append((0, len(sec.text), sec))
            source_cursor = len(sec.text) + 1 if sec.text else 0
            for leaf_index, leaf in enumerate(leaves):
                leaf_start = source_cursor
                leaf_end = leaf_start + len(leaf.text)
                source_spans.append((leaf_start, leaf_end, leaf))
                source_cursor = leaf_end + (1 if leaf_index + 1 < len(leaves) else 0)

            def _source_nodes_for_span(start: int, end: int) -> list[DocumentNode]:
                return [
                    leaf for leaf_start, leaf_end, leaf in source_spans
                    if leaf_start < end and leaf_end > start
                ]

            parent_groups = []
            parent_cursor = 0
            for gi, sub in enumerate(
                _hard_split_by_budget(sec_text, parent_budget),
            ):
                sub_start = sec_text.find(sub, parent_cursor)
                if sub_start < 0:
                    sub_start = parent_cursor
                parent_cursor = sub_start + len(sub)
                parent_id = _chunk_id(
                    file_path, f"parent:{anchor}:{gi}", sub)
                leaf_texts = _hard_split_by_budget(sub, leaf_budget)
                parent_groups.append((
                    gi, sub, sub_start, parent_id, leaf_texts,
                ))

            split_total = sum(len(group[4]) for group in parent_groups)
            split_part = 0
            for gi, sub, sub_start, parent_id, leaf_texts in parent_groups:
                parent_source_nodes = _source_nodes_for_span(
                    sub_start, sub_start + len(sub),
                )
                StructureChunkStrategy._emit_parent(
                    chunks, file_path, sec, path, sub, parent_id,
                    source_nodes=parent_source_nodes,
                )
                # 根正文超 Parent 预算时也必须保留可检索 Leaf；每个 Leaf
                # 归属当前 Parent，并再次按 Leaf 预算切分，避免 Parent 被
                # 写入全文但实际索引侧无内容。
                leaf_cursor = 0
                for li, leaf_text in enumerate(leaf_texts):
                    leaf_start = sub.find(leaf_text, leaf_cursor)
                    if leaf_start < 0:
                        leaf_start = leaf_cursor
                    leaf_cursor = leaf_start + len(leaf_text)
                    leaf_source_nodes = _source_nodes_for_span(
                        sub_start + leaf_start,
                        sub_start + leaf_start + len(leaf_text),
                    )
                    chunks.append(_make_doc(leaf_text, {
                        "granularity": "leaf",
                        "chunk_id": _chunk_id(
                            file_path, f"leaf:{anchor}:{gi}:{li}", leaf_text,
                        ),
                        "parent_chunk_id": parent_id,
                        "section_path": path,
                        "section_title": sec.text,
                        "section_level": sec.level,
                        "chunk_tokens": count_tokens(leaf_text),
                        "split_group_id": split_group_id,
                        "split_part": split_part,
                        "split_total": split_total,
                        **_source_location_metadata([
                            _node_source_location(node)
                            for node in leaf_source_nodes
                        ]),
                    }))
                    split_part += 1

    def _split_with_tables(
        self, sec, path: list, file_path: str, rows_per_chunk: int,
    ) -> List[Document]:
        """处理含表格的 section：表格走双层切分（共用 helper），非表格走原逻辑。

        C2 重构：表格双层切分逻辑抽至模块级 _split_table_node，与
        StructureChunkStrategy 共用（消除重复，行为不变）。本方法仅保留
        financial 特有的 reporting_period/fiscal_year/is_latest 元数据补丁。
        """
        from backend.rag.preprocessing.financial_normalizer import (
            extract_reporting_period,
        )

        chunks: List[Document] = []
        section_anchor = ".".join(path)

        # 提取报告期用于版本快照（financial 文档保留历史版本向量）
        reporting_period, fiscal_year = extract_reporting_period(
            file_path, sec.text,
        )

        for leaf in self._leaves(sec):
            if leaf.type == "table" and leaf.rows:
                # ── 表格双层切分（共享 helper）──
                table_chunks = _split_table_node(leaf, sec, path, file_path)
                if reporting_period:
                    for c in table_chunks:
                        if is_table_chunk(c.metadata.get("chunk_type")):
                            c.metadata["reporting_period"] = reporting_period
                            c.metadata["fiscal_year"] = fiscal_year
                            c.metadata["is_latest"] = True
                chunks.extend(table_chunks)
            else:
                # ── 混合 section 中的正文继续使用结构策略的有界 Parent/Leaf。 ──
                self._emit_plain_group(
                    chunks, file_path, sec, path, [leaf],
                )
        return chunks

    @staticmethod
    def _sections(ast: DocumentAST):
        document_title_node = _doc_title_node(ast)

        def _dfs(node: DocumentNode, path: list):
            for child in node.children:
                if child.type == "section":
                    if child is document_title_node:
                        yield from _dfs(child, path)
                        continue
                    yield child, path + [child.text]
                    yield from _dfs(child, path + [child.text])
                else:
                    yield from _dfs(child, path)
        yield from _dfs(ast.root, [])

    @staticmethod
    def _leaves(section: DocumentNode):
        for n in section.children:
            if n.type in LEAF_TYPES:
                yield n
