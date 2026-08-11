"""
LangChain BaseRetriever 封装层
把现有的 CustomRetriever + BM25 + RRF + Reranker 包装为标准检索器接口
"""
from typing import List

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from pydantic import Field

from collections import Counter

from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun

from backend.rag.retrieval.hybrid import hybrid_retrieve
from backend.rag.preprocessing.entity import extract_person_names
from backend.rag.preprocessing.keyword import extract_chunk_keywords
from backend.config import (
    HYBRID_SEARCH_K,
    ADAPTIVE_CLUSTER_THRESHOLD,
    ADAPTIVE_MAX_CLUSTER_DOCS,
)
from backend.shared.logger import logger


# =====================================================
# 关键词重叠评分
# =====================================================

def _score_by_keyword_overlap(question: str, docs: list, fallback_k: int = 3) -> list:
    query_kw = set(extract_chunk_keywords(question))
    if not query_kw:
        return docs

    scored = []
    for doc in docs:
        doc_kw = set(doc.metadata.get("chunk_keywords", "").split(", "))
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
# Chunk-Level Retriever
# =====================================================

class ChunkLevelRetriever(BaseRetriever):
    """片段级检索器：Doc→Chunk 两阶段检索（MultiQuery 由外部 MultiQueryRetriever 处理）"""

    doc_db: object = Field(description="文档级 ChromaDB")
    vectordb: object = Field(description="片段级 ChromaDB")
    chunk_retriever: object = Field(description="CustomRetriever (vector)")
    bm25: object = Field(description="BM25 检索器")
    person_index: dict = Field(default_factory=dict, description="人名 → doc_ids 倒排索引")
    k: int = HYBRID_SEARCH_K

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
        from backend.observability.tracer import trace_collector
        span = trace_collector.start_span("chunk_retrieval", name="检索")
        # — Stage 1: Doc 级检索 —
        # Check for request-scoped metadata_filter (set by RAGPipeline.search() via contextvars)
        request_metadata_filter = {}
        try:
            from backend.rag.context import get_context
            ctx = get_context()
            request_metadata_filter = ctx.metadata_filter
        except Exception:
            pass

        person_names = extract_person_names(query)
        doc_ids = None

        if request_metadata_filter:
            # MetadataFilter has already determined the scope — use it directly
            known_persons = request_metadata_filter.get("person_names")
            if known_persons:
                p = known_persons
                if isinstance(p, list):
                    p = p[0]
                matched_ids = self.person_index.get(p, [])
                if matched_ids:
                    doc_ids = matched_ids
                else:
                    doc_ids = []
            else:
                doc_ids = []  # No person filter — fall through to standard doc filter
            logger.info(
                f"ChunkLevelRetriever Stage 1: metadata_filter={request_metadata_filter} "
                f"→ doc_ids={len(doc_ids)} matched"
            )
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

        # ── Doc Filter event ──
        trace_collector.add_event(span, "doc_filter", "info",
            f"Stage1: metadata={request_metadata_filter}, persons={person_names}, → {len(doc_ids or [])} docs",
            data={"metadata_filter": request_metadata_filter,
                  "person_names": person_names,
                  "output_doc_count": len(doc_ids or [])})

        # 🟢 2026-08-10 新增：Stage 1 0 匹配 fallback
        # 解决 metadata_filter 推 business_domain 不准时丢文档的问题
        # （如问"差评怎么处理" → customer，但售后流程文档标 order）
        if request_metadata_filter and not doc_ids:
            fallback_filter = {k: v for k, v in request_metadata_filter.items() if k != "business_domain"}
            if fallback_filter != request_metadata_filter:
                logger.info(
                    f"ChunkLevelRetriever: metadata_filter {request_metadata_filter} 0 匹配, "
                    f"回退到放宽 business_domain 的检索"
                )
                request_metadata_filter = fallback_filter
                doc_ids = None  # 让 Stage 2 走完整向量检索

        # — Stage 2: Chunk 级检索 —
        all_docs = []
        seen = set()
        for q in [query]:
            res = hybrid_retrieve(
                q, self.chunk_retriever, self.bm25,
                k=self.k, doc_ids=doc_ids,
                metadata_filter=request_metadata_filter,
            )
            for d in res:
                cid = d.metadata.get("chunk_id") or f'{d.metadata.get("doc_id","?")}:{d.metadata.get("chunk_index",0)}'
                if cid not in seen:
                    seen.add(cid)
                    all_docs.append(d)

        # — 降级: Stage 2 无结果时回退到文档全文 —
        if not all_docs:
            logger.warning(f"ChunkLevelRetriever: Stage 2 无结果，尝试 Neighbor Expansion")
            fallback_docs = self._neighbor_expansion(query, doc_ids, request_metadata_filter)
            if fallback_docs:
                logger.info(f"ChunkLevelRetriever: Neighbor Expansion → {len(fallback_docs)} chunks")
                return fallback_docs[: self.k]
            logger.warning(f"ChunkLevelRetriever: 降级也无结果")
            return []

        logger.info(f"ChunkLevelRetriever Stage 2: 召回 {len(all_docs)} 个 chunks")
        trace_collector.end_span(span,
                             metrics={"retrieved_chunks": len(all_docs)})
        return all_docs[: self.k]

    def _neighbor_expansion(self, query: str, doc_ids: list | None,
                            metadata_filter: dict) -> List[Document]:
        """Stage 2 无结果时的 Context Expansion：用 doc 级 Chunk 替代。

        1. 有已知 doc_ids → 直接拉取这些文档的所有 chunk
        2. 否则 doc 级检索 → 拉取 chunk
        """
        from langchain_core.documents import Document

        if not doc_ids:
            # 没有已知 doc_ids，做一次 doc 级搜索
            try:
                doc_results = self.doc_db.similarity_search(
                    query, k=5,
                    filter=metadata_filter if metadata_filter else None,
                )
                doc_ids = [
                    d.metadata.get("doc_id") for d in doc_results
                    if d.metadata.get("doc_id")
                ]
            except Exception:
                doc_ids = []

        if not doc_ids:
            return []

        # 拉取文档全文
        try:
            results = self.doc_db.get(where={"doc_id": {"$in": doc_ids[:5]}})
            full_docs = []
            for i, content in enumerate(results.get("documents", [])):
                meta = results["metadatas"][i] if i < len(results.get("metadatas", [])) else {}
                full_docs.append(Document(page_content=content, metadata=meta))
            logger.info(f"ChunkLevelRetriever: Neighbor Expansion doc_ids={doc_ids[:5]} → {len(full_docs)} chunks")
            return full_docs
        except Exception as e:
            logger.error(f"ChunkLevelRetriever 降级失败: {e}")
            return []


# =====================================================
# Adaptive Retriever — 两阶段自适应检索
# =====================================================

class AdaptiveRetriever(BaseRetriever):
    """自适应检索器：chunk 检索 → Cluster 检测 → Context Expansion

    Stage 1: base_retriever 做 chunk 级检索
    Stage 2: 统计 doc_id 分布，检测命中是否集中在少数文档
            如果集中在 ≤ max_cluster_docs 个文档 → Context Expansion（邻近 Chunk / 同级 Heading Chunk）
            如果分散 → 仅用 chunks，避免上下文污染
    """

    base_retriever: BaseRetriever = Field(description="chunk 级检索器")
    doc_db: object = Field(description="文档级 ChromaDB，用于 Context Expansion")
    cluster_threshold: float = Field(default=ADAPTIVE_CLUSTER_THRESHOLD, description="单文档占比阈值")
    max_cluster_docs: int = Field(default=ADAPTIVE_MAX_CLUSTER_DOCS, description="触发 Expansion 的最大文档数")

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        chunks = self.base_retriever.invoke(query)
        if not chunks:
            return []

        doc_counter = Counter()
        for c in chunks:
            doc_id = c.metadata.get("doc_id")
            if doc_id:
                doc_counter[doc_id] += 1

        total = len(chunks)

        # 找出占比 ≥ threshold 的文档
        clustered = [
            doc_id for doc_id, count in doc_counter.items()
            if count / total >= self.cluster_threshold
        ]

        if clustered and len(clustered) <= self.max_cluster_docs:
            logger.info(f"AdaptiveRetriever: Cluster 检测 (docs={clustered}, {len(clustered)}/{len(doc_counter)}) → Context Expansion")
            try:
                results = self.doc_db.get(where={"doc_id": {"$in": clustered}})
                full_docs = [
                    Document(page_content=content, metadata=results["metadatas"][i])
                    for i, content in enumerate(results["documents"])
                ]
                # 全文文档放前面，chunks 补充在后
                return full_docs + chunks
            except Exception as e:
                logger.error(f"AdaptiveRetriever: Context Expansion 失败: {e}")

        logger.info(f"AdaptiveRetriever: 分散分布 ({len(doc_counter)} docs) → 跳过 Expansion")
        return chunks
