"""chunking.py — 文档类型感知切分（消费 Normalized AST）。

Strategy 只负责「既然知道结构，怎么切」，不再重新检测标题/章节。
结构检测在 parser + structure_analyzer 完成。
"""
from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, List

from langchain_core.documents import Document

if TYPE_CHECKING:
    from backend.rag.preprocessing.structure_analyzer import StructureReport
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.config import LEAF_CHUNK_TOKENS, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.token_counter import count_tokens
from backend.shared.logger import logger

_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]


def _chunk_id(doc_id: str, index: str) -> str:
    return hashlib.md5(f"{doc_id}:{index}".encode()).hexdigest()[:12]


def _enrich(chunks: List[Document], file_path: str) -> List[Document]:
    """统一 metadata：parent_doc_id / chunk_index / source_file / file_path。"""
    parent_doc_id = hashlib.md5(file_path.encode()).hexdigest()[:10]
    source_file = os.path.basename(file_path)
    for i, c in enumerate(chunks):
        c.metadata.update({
            "parent_doc_id": parent_doc_id,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "source_file": source_file,
            "file_path": file_path,
        })
    return chunks


def _make_doc(text: str, meta: dict) -> Document:
    return Document(page_content=text, metadata=meta)


class StructureChunkStrategy:
    """结构化切分：每个 section → parent，section 内叶子 → leaf。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        counter = 0
        for sec, path in self._sections(ast):
            sec_text = self._section_text(sec)
            parent_meta = {
                "granularity": "parent",
                "chunk_id": _chunk_id(file_path, str(counter)),
                "section_path": path,
                "section_title": sec.text,
                "section_level": sec.level,
                "chunk_tokens": count_tokens(sec_text),
            }
            counter += 1
            parent_id = parent_meta["chunk_id"]
            chunks.append(_make_doc(sec_text, dict(parent_meta)))
            for leaf in self._leaves(sec):
                chunks.append(_make_doc(leaf.text, {
                    "granularity": "leaf",
                    "chunk_id": _chunk_id(file_path, str(counter)),
                    "parent_chunk_id": parent_id,
                    "section_path": path,
                    "section_title": sec.text,
                    "section_level": sec.level,
                    "chunk_tokens": count_tokens(leaf.text),
                }))
                counter += 1
        return _enrich(chunks, file_path)

    @staticmethod
    def _sections(ast: DocumentAST):
        def _dfs(node: DocumentNode, path: list):
            for child in node.children:
                if child.type == "section":
                    yield child, path + [child.text]
                    yield from _dfs(child, path + [child.text])
                else:
                    yield from _dfs(child, path)
        yield from _dfs(ast.root, [])

    @staticmethod
    def _leaves(section: DocumentNode):
        # 只产出直接子叶，避免嵌套层级下叶子在每个祖先 section 重复产出
        for n in section.children:
            if n.type in LEAF_TYPES:
                yield n

    @staticmethod
    def _section_text(section: DocumentNode) -> str:
        parts = [section.text] + [n.text for n in walk(section) if n is not section and n.text]
        return "\n".join(parts)


class RecursiveChunkStrategy:
    """递归切分兜底：把叶子文本按 token 上限递归切分（不做结构检测）。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=50,
            length_function=count_tokens, separators=_SEPARATORS,
        )
        chunks: List[Document] = []
        counter = 0
        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            texts = (splitter.split_text(n.text)
                     if count_tokens(n.text) > LEAF_CHUNK_TOKENS else [n.text])
            for sub in texts:
                chunks.append(_make_doc(sub, {
                    "granularity": "leaf",
                    "chunk_id": _chunk_id(file_path, str(counter)),
                    "parent_chunk_id": "",
                    "section_path": [],
                    "section_title": "",
                    "section_level": 0,
                    "chunk_tokens": count_tokens(sub),
                }))
                counter += 1
        return _enrich(chunks, file_path)


class StepChunkStrategy:
    """步骤切分（sop/training）：Phase 1 先复用结构切分，步骤级优化 Phase 2。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        return StructureChunkStrategy().split(ast, file_path)


class QAChunkStrategy:
    """FAQ 切分：每个 qa_question/qa_answer 对 → 一个 chunk。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        counter = 0
        for n in walk(ast.root):
            if n.type not in ("qa_question", "qa_answer"):
                continue
            chunks.append(_make_doc(n.text, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, str(counter)),
                "parent_chunk_id": "",
                "section_path": [],
                "section_title": n.text[:40],
                "section_level": 0,
                "chunk_tokens": count_tokens(n.text),
            }))
            counter += 1
        return _enrich(chunks, file_path)


STRUCTURE_STRATEGIES = {
    "policy": StructureChunkStrategy,
    "compliance": StructureChunkStrategy,
    "security": StructureChunkStrategy,
    "financial": StructureChunkStrategy,
    "customer_data": StructureChunkStrategy,
    "product_spec": StructureChunkStrategy,
    "listing": StructureChunkStrategy,
    "sop": StepChunkStrategy,
    "training": StepChunkStrategy,
    "legal": StructureChunkStrategy,      # 合同条款级结构 Phase 2 细化
    "contract_template": StructureChunkStrategy,
    "faq": QAChunkStrategy,
    "ad_policy": RecursiveChunkStrategy,
}


class ChunkStrategyRouter:
    """双轴路由：文档类型 × 结构完整度 → 策略。优先级 Structure > LLM > Semantic > Recursive。"""

    def route(self, doc_type: str, report: StructureReport):
        from backend.config import ENABLE_LLM_CHUNKING, ENABLE_SEMANTIC_CHUNKING

        if report.is_complete:
            cls = STRUCTURE_STRATEGIES.get(doc_type, RecursiveChunkStrategy)
            # faq 文档：只有 AST 里真有 qa_* 节点才走 QAChunkStrategy。
            # classify_doc_type（文件名/关键词）与 parser 的 QA 识别是两套独立
            # 逻辑，可能不一致——文件名含「FAQ」但内容无 Q/A 结构时，QAChunkStrategy
            # 会产 0 chunk 导致数据丢失，这里 fallback 递归切分兜底。
            if doc_type == "faq":
                has_qa = any(
                    n.type in ("qa_question", "qa_answer")
                    for n in walk(report.ast.root)
                )
                if not has_qa:
                    logger.info("[Router] faq 但 AST 无 qa 节点 → Recursive 兜底")
                    return RecursiveChunkStrategy()
            return cls()

        # Phase 2：LLM 高价值特殊处理、Semantic 高级处理（默认关闭，暂不触发）
        if report.is_high_value_and_chaotic and ENABLE_LLM_CHUNKING:
            logger.info("[Router] 高价值混乱文档 → LLM Assisted（Phase 2）")
        if report.topic_shift_detected and ENABLE_SEMANTIC_CHUNKING:
            logger.info("[Router] 主题变化 → Semantic（Phase 2）")

        return RecursiveChunkStrategy()
