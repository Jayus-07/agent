"""
LangChain BaseRetriever 封装层
把现有的 CustomRetriever + BM25 + RRF + Reranker 包装为标准检索器接口
"""
from typing import List, Optional

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from pydantic import Field

from rag.hybrid_search import rrf_fusion_docs, hybrid_retrieve
from rag.reranker import rerank
from tools.Entity_recognition import extract_person_names
from tools.keyword import extract_chunk_keywords
from config import RERANK_TOP_K, HYBRID_SEARCH_K
from utils.logger import logger


# =====================================================
# 关键词重叠评分（从 rag.py 搬出，保持独立）
# =====================================================

def _score_by_keyword_overlap(question: str, docs: list, fallback_k: int = 3) -> list:
    query_kw = set(extract_chunk_keywords(question))
    if not query_kw:
        return docs

    scored = []
    for doc in docs:
        doc_kw = set(doc.metadata.get("keywords", []))
        overlap = len(query_kw & doc_kw)
        scored.append((doc, overlap))

    scored.sort(key=lambda x: x[1], reverse=True)
    filtered = [doc for doc, score in scored if score > 0]
    if not filtered:
        logger.info(f"关键词过滤无命中(query_kw={query_kw})，回退前{fallback_k}个")
        filtered = [doc for doc, _ in scored[:fallback_k]]

    logger.info(f"关键词过滤: query_kw={query_kw}, 命中 {len(filtered)}/{len(docs)}")
    return filtered


# =====================================================
# Doc-Level Retriever
# =====================================================

class DocLevelRetriever(BaseRetriever):
    """文档级检索器：人名倒排索引 → hybrid search → RRF → rerank"""

    doc_db: object = Field(description="文档级 ChromaDB")
    bm25: object = Field(description="BM25 检索器")
    person_index: dict = Field(default_factory=dict, description="人名 → doc_ids 倒排索引")
    k: int = 5
    rerank_top_k: int = RERANK_TOP_K

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        # 1. 人名过滤
        person_names = extract_person_names(query)
        docs = []

        if person_names:
            person_name = person_names[0] if isinstance(person_names, list) else person_names
            matched_ids = self.person_index.get(person_name, [])
            if matched_ids:
                logger.info(f"DocLevelRetriever: 人名 '{person_name}' 命中 {len(matched_ids)} 个文档")
                try:
                    results = self.doc_db.get(
                        where={"doc_id": {"$in": list(matched_ids)}}
                    )
                    for i, content in enumerate(results["documents"]):
                        doc = Document(page_content=content, metadata=results["metadatas"][i])
                        docs.append(doc)
                except Exception as e:
                    logger.error(f"人名过滤检索失败: {e}")

        # 2. Hybrid search 回退
        if not docs:
            vector_docs = self.doc_db.similarity_search(query, k=3)
            bm25_docs = self.bm25.invoke(query)

            vector_docs = _score_by_keyword_overlap(query, vector_docs)
            bm25_docs = _score_by_keyword_overlap(query, bm25_docs)

            docs = rrf_fusion_docs(vector_docs, bm25_docs, k=self.k)
            logger.info(f"DocLevelRetriever: hybrid 检索 → {len(docs)} 个融合结果")

        # 3. Rerank
        if docs:
            scored = rerank(query, docs, top_k=self.rerank_top_k)
            docs = [doc for doc, _ in scored]

        return docs[: self.k]


# =====================================================
# Chunk-Level Retriever
# =====================================================

class ChunkLevelRetriever(BaseRetriever):
    """片段级检索器：两阶段检索（Doc → Chunk）+ multi-query + hybrid + rerank"""

    doc_db: object = Field(description="文档级 ChromaDB")
    vectordb: object = Field(description="片段级 ChromaDB")
    chunk_retriever: object = Field(description="CustomRetriever (vector)")
    bm25: object = Field(description="BM25 检索器")
    person_index: dict = Field(default_factory=dict, description="人名 → doc_ids 倒排索引")
    need_global_search: bool = False
    queries: List[str] = Field(default_factory=list, description="改写后的多查询")
    k: int = HYBRID_SEARCH_K
    rerank_top_k: int = RERANK_TOP_K

    class Config:
        arbitrary_types_allowed = True

    @staticmethod
    def _filter_docs_by_keywords(question: str, doc_results: list, fallback_k: int = 3) -> list:
        filtered = _score_by_keyword_overlap(question, doc_results, fallback_k)
        return list(set([
            doc.metadata.get("doc_id")
            for doc in filtered
            if doc.metadata.get("doc_id")
        ]))

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        # — Stage 1: Doc 级检索 —
        person_names = extract_person_names(query)
        doc_ids = None

        if self.need_global_search:
            logger.info("ChunkLevelRetriever: 全局检索模式")
        else:
            if person_names:
                person_name = person_names[0] if isinstance(person_names, list) else person_names
                matched_ids = self.person_index.get(person_name, [])
                if matched_ids:
                    doc_ids = matched_ids
                    logger.info(f"ChunkLevelRetriever: 人名匹配到 {len(doc_ids)} 个文档")
                else:
                    doc_results = self.doc_db.similarity_search(query, k=5)
                    doc_ids = self._filter_docs_by_keywords(query, doc_results)
            else:
                doc_results = self.doc_db.similarity_search(query, k=5)
                if doc_results:
                    doc_ids = self._filter_docs_by_keywords(query, doc_results)

            if doc_ids:
                logger.info(f"ChunkLevelRetriever Stage 1: 召回 {len(doc_ids)} 个相关文档")

        # — Stage 2: Chunk 级检索 —
        queries = self.queries if self.queries else [query]

        all_docs = []
        seen = set()
        for q in queries:
            res = hybrid_retrieve(
                q, self.chunk_retriever, self.bm25,
                k=self.k, doc_ids=doc_ids,
            )
            for d in res:
                cid = d.metadata["chunk_id"]
                if cid not in seen:
                    seen.add(cid)
                    all_docs.append(d)

        if not all_docs:
            logger.warning(f"ChunkLevelRetriever: 未找到相关内容")
            return []

        logger.info(f"ChunkLevelRetriever Stage 2: 召回 {len(all_docs)} 个 chunks")

        # — Rerank —
        scored = rerank(query, all_docs, top_k=self.rerank_top_k)
        return [doc for doc, _ in scored]
