"""semantic.py — 语义切分策略（SemanticChunkStrategy）。

基于 Embedding 向量相似度检测句子主题边界，保持 chunk 语义连贯；
调用失败或全部分批失败时降级 RecursiveChunkStrategy。
"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.rag.preprocessing.ast import DocumentAST
from backend.rag.preprocessing.chunkers.base import (
    _SEPARATORS, _chunk_id, _detect_boundaries, _enrich,
    _iter_leaves_with_section, _make_doc, _merge_small_with_section,
    _split_sentences_keep_punct, count_tokens, get_chunk_overlap,
    get_leaf_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.strategies.recursive import RecursiveChunkStrategy
from backend.shared.logger import logger


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

    @staticmethod
    def _embed_sentences_batched(
        sentences: list[str], embedding,
    ) -> list[list[float]] | None:
        """分批 embedding + 每批重试；全部批次失败返回 None（调用方降级）。

        生产加固：整篇句子一次性 embed_documents 会构造超大输入，
        且任一失败即整体降级丢失已算批次。分批后单批失败可重试
        （对齐 indexer 主路径 _embed_with_retry 的 EMBED_RETRY_MAX 模式），
        批次之间相互独立，避免可用句子的向量被一次失败拖垮。
        """
        from backend.config import SEMANTIC_EMBED_BATCH_SIZE, SEMANTIC_EMBED_RETRY
        # 批大小与 indexer 主路径同口径：云端 embedding 声明的
        # embed_batch_size（DashScope 单请求上限）取 min，
        # 避免外层大批在 OpenAIEmbeddings 内部再拆、白多 RTT
        _declared = getattr(embedding, "embed_batch_size", None)
        batch_size = (
            min(SEMANTIC_EMBED_BATCH_SIZE, _declared)
            if isinstance(_declared, int) and _declared > 0
            else SEMANTIC_EMBED_BATCH_SIZE
        )
        vecs: list[list[float]] = []
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            batch_no = i // batch_size
            for attempt in range(SEMANTIC_EMBED_RETRY):
                try:
                    vecs.extend(embedding.embed_documents(batch))
                    break
                except Exception as e:
                    if attempt == SEMANTIC_EMBED_RETRY - 1:
                        logger.warning(
                            f"[SemanticChunk] 句子批次 {batch_no} 嵌入失败 "
                            f"{SEMANTIC_EMBED_RETRY} 次({type(e).__name__})，"
                            f"整体降级递归切分: {e}"
                        )
                        return None
                    logger.debug(
                        f"[SemanticChunk] 句子批次 {batch_no} 重试 "
                        f"{attempt + 1}/{SEMANTIC_EMBED_RETRY}: {e}"
                    )
        return vecs

    def _split_with_embedding(
        self, ast: DocumentAST, file_path: str, embedding,
    ) -> List[Document]:
        from backend.config import SEMANTIC_SIMILARITY_THRESHOLD

        chunks: List[Document] = []
        leaf_tokens = get_leaf_chunk_tokens()
        overlap = get_chunk_overlap()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=leaf_tokens, chunk_overlap=overlap,
            length_function=count_tokens, separators=_SEPARATORS,
        )
        leaf_items = list(_iter_leaves_with_section(ast))
        for text, titles in _merge_small_with_section(leaf_items, leaf_tokens):
            sentences = _split_sentences_keep_punct(text)
            if len(sentences) <= 1:
                chunks.append(_make_doc(
                    text, self._leaf_meta(file_path, text, titles),
                ))
                continue
            try:
                vecs = self._embed_sentences_batched(sentences, embedding)
            except Exception as e:
                # 外层防御：批处理逻辑自身异常也降级，不静默吞掉
                logger.warning(
                    f"[SemanticChunk] embedding 调用异常({type(e).__name__})，"
                    f"降级递归切分: {e}"
                )
                return RecursiveChunkStrategy().split(ast, file_path)
            if vecs is None:
                # 全部分批失败 → 降级递归切分（可观测：上面已 warning）
                return RecursiveChunkStrategy().split(ast, file_path)
            starts = _detect_boundaries(sentences, vecs, SEMANTIC_SIMILARITY_THRESHOLD)

            # O(n) 预计算每个 start 的区间终点：next_start[i] = min{j>i: starts[j]}，无则 len
            n = len(sentences)
            next_start = [n] * n
            last = n
            for i in range(n - 1, -1, -1):
                next_start[i] = last
                if starts[i]:
                    last = i

            for i, is_start in enumerate(starts):
                if not is_start:
                    continue
                end = next_start[i]
                seg_text = "".join(sentences[i:end])
                # 超长 chunk（无边界可切时）兜底递归切分，避免超大 chunk
                if count_tokens(seg_text) > leaf_tokens:
                    for sub in splitter.split_text(seg_text):
                        chunks.append(_make_doc(sub, self._leaf_meta(file_path, sub, titles)))
                else:
                    chunks.append(_make_doc(seg_text, self._leaf_meta(file_path, seg_text, titles)))

        logger.info(
            f"[SemanticChunk] {file_path} 语义切分产出 {len(chunks)} 个 chunk"
        )
        return _enrich(chunks, file_path)

    @staticmethod
    def _leaf_meta(file_path: str, text: str, titles: list[str] | None = None) -> dict:
        title = (titles[0] if titles else "") or ""
        meta = {
            "granularity": "leaf",
            "chunk_id": _chunk_id(file_path, "leaf", text),
            "parent_chunk_id": "",
            "section_path": [title] if title else [],
            "section_title": title,
            "section_level": 1 if title else 0,
            "chunk_tokens": count_tokens(text),
        }
        if titles and len(titles) > 1:
            meta["section_mixed"] = "true"
        return meta
