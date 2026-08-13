"""chunking.py — 文档类型感知切分（消费 Normalized AST）。

Strategy 只负责「既然知道结构，怎么切」，不再重新检测标题/章节。
结构检测在 parser + structure_analyzer 完成。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
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


# ── Semantic 语义切分辅助函数 ──────────────────────────

_SENTENCE_SPLIT_RE = re.compile(r"[。！？；]+")


def _split_sentences(text: str) -> list[str]:
    """按中文句子边界切分，过滤空句。

    PDF 提取的文本常含硬换行（非语义边界），先统一为空格，
    避免误切「接口」「流程」这类被换行拆开的词。
    """
    normalized = text.replace("\r", " ").replace("\n", " ")
    return [p.strip() for p in _SENTENCE_SPLIT_RE.split(normalized) if p.strip()]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度，零向量返回 0.0。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _detect_boundaries(
    sentences: list[str], vecs: list[list[float]], threshold: float,
) -> list[bool]:
    """检测语义边界：相邻句子相似度骤降处标记为新 chunk 起点。

    Returns:
        starts: starts[i]=True 表示 sentences[i] 是新 chunk 的起点（starts[0] 恒 True）。
    """
    starts = [True] + [False] * (len(sentences) - 1)
    for i in range(1, len(sentences)):
        if _cosine_similarity(vecs[i - 1], vecs[i]) < threshold:
            starts[i] = True
    return starts


# 中文编号章节标题（一、二、三、），用于 SOP 文档的步骤切分
_SECTION_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")


def _is_section_heading(text: str) -> bool:
    """判断文本是否中文编号章节标题（一、二、三、）。"""
    return bool(_SECTION_HEADING_RE.match(text.strip()))


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


class FixedSizeChunkStrategy:
    """固定长度切分：直接按 token 上限硬切 + overlap，不查分隔符。

    适用「普通文本、兜底」——无结构文本，无需递归查找分隔符。
    与 RecursiveChunkStrategy 的区别：递归优先在分隔符处切（保持句子完整），
    固定长度直接按 token 切（可能切在句子中间），更快更简单。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        # separators=[""] → 不查分隔符，直接按 chunk_size 字符级硬切
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=50,
            length_function=count_tokens, separators=[""],
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
    """步骤切分（SOP/培训）：按中文编号章节标题（一、二、三、）切分。

    章节标题作为 chunk 边界，章节下的步骤/说明文本合并成一个 chunk，
    保证「一个章节一个语义单元」，替代 Recursive 的逐段硬切。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: List[Document] = []
        counter = 0
        current: list[str] = []

        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            if _is_section_heading(n.text) and current:
                # 新章节 → flush 之前的 chunk
                chunks.append(self._chunk(current, file_path, counter))
                counter += 1
                current = []
            current.append(n.text)

        if current:
            chunks.append(self._chunk(current, file_path, counter))

        return _enrich(chunks, file_path)

    @staticmethod
    def _chunk(texts: list[str], file_path: str, index: int) -> Document:
        text = "\n".join(texts)
        return _make_doc(text, {
            "granularity": "leaf",
            "chunk_id": _chunk_id(file_path, str(index)),
            "parent_chunk_id": "",
            "section_path": [],
            "section_title": "",
            "section_level": 0,
            "chunk_tokens": count_tokens(text),
        })


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


class SemanticChunkStrategy:
    """语义切分：基于 embedding 相似度检测主题边界。

    适用于无结构长文档——RecursiveChunkStrategy 按固定 token 硬切 + overlap
    会把完整信息单元切碎、稀释。本策略在相邻句子的语义相似度骤降处切分，
    保持 chunk 语义连贯。

    可靠性：embedding 调用失败时降级 RecursiveChunkStrategy，不静默吞异常。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        from backend.rag.embedding_singleton import get_embedding
        return self._split_with_embedding(ast, file_path, get_embedding())

    def _split_with_embedding(
        self, ast: DocumentAST, file_path: str, embedding,
    ) -> List[Document]:
        from backend.config import SEMANTIC_SIMILARITY_THRESHOLD

        chunks: List[Document] = []
        counter = 0
        for n in walk(ast.root):
            if n.type not in LEAF_TYPES:
                continue
            sentences = _split_sentences(n.text)
            if len(sentences) <= 1:
                chunks.append(_make_doc(
                    n.text, self._leaf_meta(file_path, counter, n.text),
                ))
                counter += 1
                continue
            try:
                vecs = embedding.embed_documents(sentences)
            except Exception as e:
                logger.warning(
                    f"[SemanticChunk] embedding 失败({type(e).__name__})，"
                    f"降级递归切分: {e}"
                )
                return RecursiveChunkStrategy().split(ast, file_path)
            starts = _detect_boundaries(sentences, vecs, SEMANTIC_SIMILARITY_THRESHOLD)
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=LEAF_CHUNK_TOKENS, chunk_overlap=50,
                length_function=count_tokens, separators=_SEPARATORS,
            )
            for i, start in enumerate(starts):
                if not start:
                    continue
                end = next(
                    (j for j in range(i + 1, len(sentences)) if starts[j]),
                    len(sentences),
                )
                text = "。".join(sentences[i:end])
                # 超长 chunk（无边界可切时）兜底递归切分，避免超大 chunk
                if count_tokens(text) > LEAF_CHUNK_TOKENS:
                    for sub in splitter.split_text(text):
                        chunks.append(_make_doc(sub, self._leaf_meta(file_path, counter, sub)))
                        counter += 1
                else:
                    chunks.append(_make_doc(text, self._leaf_meta(file_path, counter, text)))
                    counter += 1

        logger.info(
            f"[SemanticChunk] {file_path} 语义切分产出 {len(chunks)} 个 chunk"
        )
        return _enrich(chunks, file_path)

    @staticmethod
    def _leaf_meta(file_path: str, index: int, text: str) -> dict:
        return {
            "granularity": "leaf",
            "chunk_id": _chunk_id(file_path, str(index)),
            "parent_chunk_id": "",
            "section_path": [],
            "section_title": "",
            "section_level": 0,
            "chunk_tokens": count_tokens(text),
        }


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
    "ad_policy": FixedSizeChunkStrategy,
}


class ChunkStrategyRouter:
    """双轴路由：文档类型 × 结构完整度 → 策略。优先级 Structure > LLM > Semantic > Recursive。"""

    def route(self, doc_type: str, report: StructureReport):
        from backend.config import (
            ENABLE_LLM_CHUNKING, ENABLE_SEMANTIC_CHUNKING, SEMANTIC_CHUNK_MIN_TOKENS,
        )

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

        # Phase 3：Semantic 语义切分（无结构长文档，基于 embedding 主题边界）
        if ENABLE_SEMANTIC_CHUNKING and count_tokens(report.ast.raw_text) >= SEMANTIC_CHUNK_MIN_TOKENS:
            logger.info("[Router] 无结构长文档 → Semantic 语义切分")
            return SemanticChunkStrategy()

        # Phase 2：LLM 高价值特殊处理（默认关闭，暂不触发）
        if report.is_high_value_and_chaotic and ENABLE_LLM_CHUNKING:
            logger.info("[Router] 高价值混乱文档 → LLM Assisted（Phase 2）")

        return RecursiveChunkStrategy()
