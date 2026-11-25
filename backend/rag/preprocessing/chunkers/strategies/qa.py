"""qa.py — FAQ / 问答切分策略（QAChunkStrategy）。

合并 qa_question/qa_answer 为单一语义 chunk（Q：...\nA：...），
超长答案带 Q 前缀切分；混合文档中保留非 QA 章节内容。
"""
from __future__ import annotations

import copy
from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, walk
from backend.rag.preprocessing.chunkers.base import (
    _chunk_id, _enrich, _iter_leaves_with_section, _make_doc,
    _make_leaf_chunks_bounded, _merge_small_with_section,
    _split_to_budget, count_tokens, get_leaf_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.strategies.structure import StructureChunkStrategy


class QAChunkStrategy:
    """FAQ 切分：每个 qa_question/qa_answer 对 → 一个 chunk。

    P1-6: 原实现将 question 与 answer 各自独立成 chunk，导致问答分离
    （检索命中问题 chunk 时答案缺失）。现合并为"Q：...\nA：..."单一 chunk，
    保证一个 FAQ 条目完整表达语义。

    P0-02（2026-10-13）：parser 改局部 QA 标注后，AST 中会同时存在
    qa 节点与普通章节/段落——本策略不再丢弃非 QA 内容：普通叶子按
    Structure 语义切分（section → parent + leaf），QA 对照常成 chunk。
    P0-05：长答案不再产单一巨型 chunk——A 部分超预算时继续切分，
    每个子块保留 Q 前缀（FAQ 条目语义完整）。

    FAQ 叶子本身包含完整问答，不生成只列问题、不含答案的文档级 parent；
    这类 parent 会在命中任一 FAQ 后被附加进上下文，带入大量无关问题。
    """

    def __init__(self, doc_type: str = "general"):
        self.doc_type = doc_type

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = self._qa_chunks(ast, file_path)
        # 混合文档：非 QA 的 section/叶子照常产出（P0-02 不丢内容）。
        # 纯 FAQ 文档（全树只有 qa 节点）无普通叶子，跳过。
        if any(n.type not in ("qa_question", "qa_answer") for n in walk(ast.root)):
            chunks.extend(self._non_qa_chunks(ast, file_path))
        return _enrich(chunks, file_path)

    def _qa_chunks(self, ast: DocumentAST, file_path: str) -> List[Document]:
        leaf_tokens = get_leaf_chunk_tokens()
        nodes = [
            n for n in walk(ast.root)
            if n.type in ("qa_question", "qa_answer")
        ]
        chunks: List[Document] = []
        i = 0
        while i < len(nodes):
            n = nodes[i]
            if n.type == "qa_question":
                q_text = n.text
                a_text = ""
                # 合并紧跟的 qa_answer（parser 按 Q、A 顺序产出节点）
                if i + 1 < len(nodes) and nodes[i + 1].type == "qa_answer":
                    a_text = nodes[i + 1].text
                    i += 2
                else:
                    i += 1
                full = f"Q：{q_text}\nA：{a_text}" if a_text else q_text
                # P0-05：超预算 → A 部分继续切，每个子块带 Q 前缀
                if count_tokens(full) > leaf_tokens and a_text:
                    chunks.extend(self._split_long_qa(
                        file_path, q_text, a_text,
                        _chunk_id(file_path, "faq", q_text),
                    ))
                else:
                    chunks.append(_make_doc(full, {
                        "granularity": "leaf",
                        "chunk_id": _chunk_id(file_path, "leaf", full),
                        "parent_chunk_id": "",
                        "chunk_type": "faq",
                        "faq_id": _chunk_id(file_path, "faq", q_text),
                        "question": q_text,
                        "section_path": [],
                        "section_title": q_text[:40],
                        "section_level": 0,
                        "chunk_tokens": count_tokens(full),
                    }))
            else:
                # 孤立的 qa_answer（无配对问题）也保留，不丢内容
                chunks.extend(_make_leaf_chunks_bounded(
                    [n.text], file_path,
                    section_title=n.text[:40], section_level=0,
                ))
                i += 1
        return chunks

    @staticmethod
    def _split_long_qa(
        file_path: str, q_text: str, a_text: str, faq_id: str,
    ) -> List[Document]:
        leaf_tokens = get_leaf_chunk_tokens()
        prefix = f"Q：{q_text}\nA："
        budget = leaf_tokens - count_tokens(prefix)
        if budget < 50:
            # 问题本身超长：整条按预算切（退化为普通文本切分）
            fallback = _make_leaf_chunks_bounded(
                [f"Q：{q_text}\nA：{a_text}"], file_path,
                section_title=q_text[:40], section_level=0,
            )
            for chunk in fallback:
                chunk.metadata.update({
                    "chunk_type": "faq", "faq_id": faq_id, "question": q_text,
                })
            return fallback
        subs = _split_to_budget(a_text, budget)
        chunks: List[Document] = []
        split_group_id = _chunk_id(file_path, f"split:qa:{q_text}", a_text)
        for si, sub in enumerate(subs):
            text = prefix + sub
            chunks.append(_make_doc(text, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, f"leaf:qa:{si}", text),
                "parent_chunk_id": "",
                "chunk_type": "faq",
                "faq_id": faq_id,
                "question": q_text,
                "section_path": [],
                "section_title": q_text[:40],
                "section_level": 0,
                "chunk_tokens": count_tokens(text),
                "split_group_id": split_group_id,
                "split_part": si,
            }))
        return chunks

    def _non_qa_chunks(self, ast: DocumentAST, file_path: str) -> List[Document]:
        """混合文档的普通部分：有 section 结构走 Structure，否则按叶子合并。

        qa 节点已由 _qa_chunks 产出：有 section 时 exclude_qa=True 复用
        Structure；无结构时按文本排除 qa 叶子后再合并，避免重复入索引。
        """
        has_sections = any(
            n.type == "section" and n.level > 0 for n in walk(ast.root)
        )
        if has_sections:
            non_qa_ast = copy.deepcopy(ast)

            def _strip_qa(parent: DocumentNode) -> None:
                parent.children = [
                    child for child in parent.children
                    if child.type not in ("qa_question", "qa_answer")
                ]
                for child in parent.children:
                    _strip_qa(child)

            _strip_qa(non_qa_ast.root)
            from backend.rag.preprocessing.chunkers.router import (
                _local_strategy_class, _top_level_content_units, ContentUnitDispatchStrategy,
            )
            units = _top_level_content_units(non_qa_ast)
            local_strategies = [
                _local_strategy_class(unit, self.doc_type) for unit in units
            ]
            if any(strategy is not StructureChunkStrategy
                   for strategy in local_strategies):
                # QA 主路由下，制度条款、SOP 等普通内容仍交给本地专用处理器；
                # 问答叶子已经由 _qa_chunks 单独输出，不会重复入库。
                return ContentUnitDispatchStrategy(self.doc_type).split(
                    non_qa_ast, file_path,
                )
            return StructureChunkStrategy().split(ast, file_path, exclude_qa=True)
        qa_texts = {
            n.text for n in walk(ast.root)
            if n.type in ("qa_question", "qa_answer")
        }
        leaf_items = [
            (text, title)
            for text, title in _iter_leaves_with_section(ast)
            if text not in qa_texts
        ]
        leaf_tokens = get_leaf_chunk_tokens()
        chunks: List[Document] = []
        for text, titles in _merge_small_with_section(leaf_items, leaf_tokens):
            chunks.extend(_make_leaf_chunks_bounded(
                [text], file_path,
                section_title=titles[0] if titles else "",
                section_level=1 if titles else 0,
                section_path=[titles[0]] if titles else [],
            ))
        return chunks
