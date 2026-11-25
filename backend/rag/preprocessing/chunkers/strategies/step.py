"""step.py — 步骤切分策略（StepChunkStrategy）。

适用于 SOP、培训类文档，按章节切分，每个章节一个语义单元。
无 section 结构（flat 旧数据）时，回退按「一、二、三」文本切分。
挂文档级虚拟 parent。
"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.chunkers.base import (
    _doc_title_node, _enrich, _is_section_heading, _make_leaf_chunks_bounded,
)
from backend.rag.preprocessing.chunkers.table_handler import _attach_virtual_parents


class StepChunkStrategy:
    """步骤切分（SOP/培训）：按章节切分，每个章节一个语义单元。

    章节标题（一、二、三、）由 DocxParser 识别为 section 节点；
    本策略按 section 切分，章节标题 + 内容合并成一个 chunk。
    无 section 结构（flat 旧数据）时，回退按「一、二、三」文本切分。

    P0-02：section 之外的根直接叶子（章节之间的正文）不再丢失。
    P0-05：超长章节按 token 预算继续切分（子块保留章节身份），
    不再产出单一巨型 leaf。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        document_title_node = _doc_title_node(ast)
        sections = [
            n for n in walk(ast.root)
            if n.type == "section" and n.level > 0
            and n is not document_title_node
        ]
        if sections:
            return self._split_by_sections(sections, file_path, ast)
        return self._split_by_text(ast, file_path)

    def _split_by_sections(
        self, sections: list[DocumentNode], file_path: str,
        ast: DocumentAST | None = None,
    ) -> List[Document]:
        chunks: List[Document] = []
        # P0-02：章节之间的根直接叶子（引导段/插注）照常保留
        if ast is not None:
            root_leaves = [
                n for n in ast.root.children if n.type in LEAF_TYPES
            ]
            if root_leaves:
                chunks.extend(_make_leaf_chunks_bounded(
                    [n.text for n in root_leaves], file_path,
                    section_title="", section_level=0, section_path=[],
                ))
        for sec in sections:
            # P1-9: 章节标题继承到 chunk（section_title/section_level/section_path）
            # P0-05: 超长章节继续切分，子块保留章节身份
            chunks.extend(_make_leaf_chunks_bounded(
                [self._section_text(sec)], file_path,
                section_title=sec.text, section_level=sec.level,
                section_path=[sec.text], chunk_type="step",
            ))
        # C1: 文档级虚拟 parent，修复 SOP/培训 leaf 无 parent_chunk_id
        return _enrich(_attach_virtual_parents(chunks, file_path, "step"), file_path)

    def _split_by_text(self, ast: DocumentAST, file_path: str) -> List[Document]:
        """无 section 结构（flat）→ 按「一、二、三」文本切分。"""
        chunks: List[Document] = []
        current: list[str] = []
        current_title = ""
        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            if _is_section_heading(n.text) and current:
                chunks.extend(_make_leaf_chunks_bounded(
                    current, file_path,
                    section_title=current_title, section_level=1 if current_title else 0,
                    section_path=[current_title] if current_title else [],
                    chunk_type="step",
                ))
                current = []
            if _is_section_heading(n.text):
                current_title = n.text
            current.append(n.text)
        if current:
            chunks.extend(_make_leaf_chunks_bounded(
                current, file_path,
                section_title=current_title, section_level=1 if current_title else 0,
                section_path=[current_title] if current_title else [],
            ))
        # C1: 文档级虚拟 parent（flat 旧数据分支）
        return _enrich(_attach_virtual_parents(chunks, file_path, "step_flat"), file_path)

    @staticmethod
    def _section_text(section: DocumentNode) -> str:
        parts = [section.text] + [
            n.text for n in walk(section) if n is not section and n.text
        ]
        return "\n".join(parts)
