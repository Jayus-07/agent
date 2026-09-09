"""RAG Runner 共享工具 — 管线初始化、文本归一化、消融实验。

从 builtin.py 拆出，供 rag.py 及其他需要直接操作检索管线的模块使用。
"""
from __future__ import annotations

import os

from backend.shared.logger import logger

# ==================== 文本归一化 ====================


def normalize_snippet_text(text: str) -> str:
    """snippet 匹配归一化：去全部空白字符 + 全角转半角。

    避免 ground truth 关键词与文档原文仅因空格/全半角差异（如 "48 小时" vs "48小时"）
    导致假阴性。
    """
    text = text.translate(
        {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}
    ).replace("\u3000", " ")
    text = "".join(text.split())
    return text.replace(",", "")


# ==================== 拒答校准：查询实体存在性校验（V1.3） ====================

_QUERY_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "多久", "多少", "为什么",
    "请问", "你们", "我们", "贵公司", "公司", "需要", "应该", "可以", "是否",
    "有没有", "是什么", "进行", "相关", "具体", "一般", "规定", "要求",
    "时候", "目前", "现在", "支持", "采用", "包括", "属于", "关于", "一样",
}


def extract_query_entities(question: str) -> list[str]:
    """jieba 分词提取问题候选实体（长度 >=2、去停用词）。"""
    import jieba
    return [
        w for w in jieba.cut(question)
        if len(w) >= 2 and w not in _QUERY_STOPWORDS and not w.isdigit()
    ]


def entities_all_present(entities: list[str], details: list[dict], top_n: int = 3) -> bool:
    """全部实体均出现在 top_n 召回 chunk 文本中才视为证据存在。"""
    if not entities:
        return True
    text = normalize_snippet_text(
        "".join((d.get("page_content") or "") for d in details[:top_n])
    )
    return all(normalize_snippet_text(e) in text for e in entities)


def match_by_snippet(
    details: list[dict],
    expected_snippets: list[str],
) -> tuple[bool, float]:
    """V1.1: snippet 语义匹配 — 召回内容含所有 keywords -> hit=True。"""
    if not expected_snippets:
        return False, 0.0
    actual_text = " ".join(
        d.get("page_content") or d.get("snippet") or "" for d in details
    )
    normalized_actual = normalize_snippet_text(actual_text)
    matched = sum(
        1 for s in expected_snippets
        if normalize_snippet_text(s) in normalized_actual
    )
    recall = matched / len(expected_snippets)
    hit = matched == len(expected_snippets)
    return hit, recall


# ==================== RAG 管线初始化 ====================

_rag_pipeline = None
_rag_pipeline_error = None


def init_rag_pipeline():
    """初始化 RAG 检索管线（模块级单例）。"""
    global _rag_pipeline, _rag_pipeline_error
    if _rag_pipeline is not None:
        return _rag_pipeline
    if _rag_pipeline_error is not None:
        return None
    try:
        from backend.rag.pipeline import RAGPipeline
    except ImportError:
        _rag_pipeline_error = "RAG pipeline import failed"
        return None
    try:
        _rag_pipeline = RAGPipeline()
        return _rag_pipeline
    except Exception as e:
        _rag_pipeline_error = str(e)
        return None


# ==================== 完整检索链路 ====================

_full_retriever = None


def get_full_retriever(pipeline):
    """构建完整检索链路: ChunkLevelRetriever -> Adaptive -> CrossEncoder 精排。"""
    global _full_retriever
    if _full_retriever is not None:
        return _full_retriever

    from langchain_classic.retrievers import ContextualCompressionRetriever

    from backend.config import HYBRID_SEARCH_K
    from backend.rag.reranker import RerankCompressor
    from backend.rag.retrieval.retrievers import AdaptiveRetriever

    base = pipeline.lc_chain.chunk_retriever_base
    base.k = HYBRID_SEARCH_K

    adaptive = AdaptiveRetriever(
        base_retriever=base,
        doc_db=pipeline.doc_db,
    )

    full_retriever = ContextualCompressionRetriever(
        base_compressor=RerankCompressor(),
        base_retriever=adaptive,
    )

    _full_retriever = full_retriever
    return _full_retriever


class _ListRetriever:
    """将返回 list[Document] 的函数适配为 LangChain BaseRetriever 接口。"""

    def __init__(self, fn):
        self._fn = fn

    def invoke(self, question: str):
        return self._fn(question)


def build_ablation_retriever(
    pipeline,
    mode: str,
    kb_id: str,
    department: str,
):
    """构建消融实验检索器 — 隔离各组件贡献。"""
    if mode == "full":
        return get_full_retriever(pipeline)

    from backend.config import HYBRID_SEARCH_K

    mf = {}
    if kb_id and kb_id not in ("*", "default"):
        mf["kb_id"] = kb_id
    if department:
        mf["department"] = department

    if mode == "vector_only":
        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        def _vector_invoke(question: str):
            return base.chunk_retriever.retrieve(
                question, k=base.k,
                metadata_filter=mf or None,
            )

        return _ListRetriever(_vector_invoke)

    if mode == "bm25_only":
        bm25 = pipeline.bm25

        def _bm25_invoke(question: str):
            docs = bm25.invoke(question)
            if mf:
                filtered = []
                for d in docs:
                    meta = d.metadata or {}
                    if all(meta.get(k_) == v for k_, v in mf.items()):
                        filtered.append(d)
                return filtered
            return docs

        return _ListRetriever(_bm25_invoke)

    if mode == "hybrid":
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        def _hybrid_invoke(question: str):
            return hybrid_retrieve(
                question, base.chunk_retriever, pipeline.bm25,
                k=HYBRID_SEARCH_K,
                metadata_filter=mf or None,
            )

        return _ListRetriever(_hybrid_invoke)

    if mode == "hybrid_rerank":
        from langchain_classic.retrievers import ContextualCompressionRetriever

        from backend.rag.reranker import RerankCompressor
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        def _hybrid_base(question: str):
            return hybrid_retrieve(
                question, base.chunk_retriever, pipeline.bm25,
                k=HYBRID_SEARCH_K,
                metadata_filter=mf or None,
            )

        return ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=_ListRetriever(_hybrid_base),
        )

    if mode == "hybrid_adaptive":
        from langchain_classic.retrievers import ContextualCompressionRetriever

        from backend.rag.reranker import RerankCompressor
        from backend.rag.retrieval.retrievers import AdaptiveRetriever

        base = pipeline.lc_chain.chunk_retriever_base
        base.k = HYBRID_SEARCH_K

        adaptive = AdaptiveRetriever(
            base_retriever=base,
            doc_db=pipeline.doc_db,
        )
        return ContextualCompressionRetriever(
            base_compressor=RerankCompressor(),
            base_retriever=adaptive,
        )

    logger.warning(f"[RAG eval] 未知消融模式 '{mode}', 回退 full")
    return get_full_retriever(pipeline)


# ==================== Gate 模式 ====================


def gate_mode() -> str:
    """返回当前评估门控模式: legacy | shadow | semantic。"""
    mode = os.getenv("EVAL_GATE", "shadow")
    if mode in ("legacy", "shadow", "semantic"):
        return mode
    logger.warning(f"[RAG eval] 未知 EVAL_GATE={mode!r}，回退 shadow")
    return "shadow"
