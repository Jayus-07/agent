"""recursive.py — 固定长度与递归字符切分策略。

用于无明显结构（无标题/小节/步骤/表格）文档的降级切分。
底层调用 _split_unstructured 循环，保留叶子所属章节上下文与虚拟 Parent 挂载。
"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.rag.preprocessing.ast import DocumentAST
from backend.rag.preprocessing.chunkers.base import (
    _SEPARATORS, count_tokens, get_chunk_overlap, get_leaf_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.strategies.structure import _split_unstructured


class FixedSizeChunkStrategy:
    """固定长度切分：直接按 token 上限硬切 + overlap，不查分隔符。

    适用「普通文本、兜底」——无结构文本，无需递归查找分隔符。
    与 RecursiveChunkStrategy 的区别：递归优先在分隔符处切（保持句子完整），
    固定长度直接按 token 切（可能切在句子中间），更快更简单。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        leaf_tokens = get_leaf_chunk_tokens()
        overlap = get_chunk_overlap()
        # separators=[""] → 不查分隔符，直接按 chunk_size 字符级硬切
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=leaf_tokens, chunk_overlap=overlap,
            length_function=count_tokens, separators=[""],
        )
        return _split_unstructured(ast, file_path, splitter)


class RecursiveChunkStrategy:
    """递归切分兜底：把叶子文本按 token 上限递归切分（不做结构检测）。"""

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        leaf_tokens = get_leaf_chunk_tokens()
        overlap = get_chunk_overlap()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=leaf_tokens, chunk_overlap=overlap,
            length_function=count_tokens, separators=_SEPARATORS,
        )
        return _split_unstructured(ast, file_path, splitter)
