"""Multi Query Retrieval 控制器

自动触发、Query Rewrite、质量过滤、融合排序、Rerank。
独立于现有 Retriever，可插拔。
"""

from __future__ import annotations

import re
import time
from typing import List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field
from typing import Any

from config import (
    MULTI_QUERY_MODE,
    MULTI_QUERY_COUNT,
    MULTI_QUERY_TEMPERATURE,
    MULTI_QUERY_MAX_TOKENS,
    MULTI_QUERY_TOP_K_PER,
    MULTI_QUERY_DEDUP,
    MULTI_QUERY_SIMILARITY,
    MULTI_QUERY_MIN_LENGTH,
)
from utils.logger import logger


# =====================================================
# 复杂度检测 — 纯规则，不调 LLM
# =====================================================

COMPLEX_PATTERNS = [
    "分析", "对比", "比较", "总结", "汇总", "概述",
    "全部", "所有", "区别", "差异", "不同",
    "流程", "步骤", "怎么做", "如何", "怎么",
    "原因", "为什么", "优缺点", "利弊", "优劣",
    "关系", "影响", "作用", "意义", "方法",
]

SIMPLE_PREFIXES = ("什么是", "什么是", "多少", "几点", "几号", "谁", "哪个", "哪里")


def is_complex_query(query: str) -> tuple[bool, str]:
    """判断问题是否需要 MultiQuery，返回 (是否复杂, 原因)。

    规则（按优先级）：
    1. 包含分析/对比/流程等关键词 → 复杂
    2. 简单事实问句（什么是X/谁/哪个） → 简单
    3. 过短 (<5字) → 简单
    4. 过长 (>15字) → 复杂
    5. 兜底 → 简单
    """
    q = query.strip()
    qlen = len(q)

    # 规则 1: 关键词匹配
    for pat in COMPLEX_PATTERNS:
        if pat in q:
            return True, f"包含复杂意图关键词: {pat}"

    # 规则 2: 简单事实问句
    if q.startswith(SIMPLE_PREFIXES):
        return False, "简单事实问句"

    # 规则 3: 过短
    if qlen < 5:
        return False, f"查询过短 ({qlen}字)"

    # 规则 4: 过长
    if qlen > 15:
        return True, f"查询较长 ({qlen}字)，可能需要多角度检索"

    # 规则 5: 兜底
    return False, "默认简单"


# =====================================================
# Query Rewrite — LLM 生成中文变体
# =====================================================

QUERY_REWRITE_PROMPT = """将用户问题改写为 {count} 个语义等价但表达不同的检索查询。

规则：
1. 保留原始查询作为第 1 个
2. 不改变原意，不扩展问题
3. 不回答问题，不解释
4. 只输出查询文本，每行一个，不要编号

用户问题：{question}

改写结果："""


def rewrite_queries(question: str, count: int = None) -> list[str]:
    """调用 LLM 生成多角度查询变体。

    Args:
        question: 原始问题
        count: 期望的查询变体数量

    Returns:
        查询变体列表（包含原始查询）
    """
    if count is None:
        count = MULTI_QUERY_COUNT

    try:
        from llm.llm_factory import llm
        from langchain_core.messages import HumanMessage

        prompt = QUERY_REWRITE_PROMPT.format(count=count, question=question)
        result = llm.invoke(
            [HumanMessage(content=prompt)],
            temperature=MULTI_QUERY_TEMPERATURE,
            max_tokens=MULTI_QUERY_MAX_TOKENS,
        )
        text = result.content if hasattr(result, "content") else str(result)
        text = text.strip()

        # 解析每行
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        # 去掉编号前缀如 "1. " "1) "
        lines = [re.sub(r"^\d+[\.\)、]\s*", "", l) for l in lines]

        # 确保原始查询在第一位
        cleaned = []
        seen = {question}
        cleaned.append(question)
        for line in lines:
            if line not in seen and len(cleaned) < count:
                seen.add(line)
                cleaned.append(line)

        logger.info(f"[MultiQuery] Rewrite: {question[:40]} → {len(cleaned)} 个变体")
        return cleaned

    except Exception as e:
        logger.warning(f"[MultiQuery] Rewrite 失败: {e}，回退原始查询")
        return [question]


# =====================================================
# Query 质量过滤
# =====================================================

def filter_queries(queries: list[str]) -> list[str]:
    """过滤低质量查询变体。

    过滤规则：
    1. 去重
    2. 过短 (< min_length)
    3. 与原查询高度相似 (Jaccard > threshold)
    """
    if not MULTI_QUERY_DEDUP or len(queries) <= 1:
        return queries

    original = queries[0]
    result = [original]
    original_chars = set(original)

    for q in queries[1:]:
        # 最小长度
        if len(q) < MULTI_QUERY_MIN_LENGTH:
            logger.debug(f"[MultiQuery] 过滤过短: {q}")
            continue

        # Jaccard 相似度
        if original_chars:
            q_chars = set(q)
            intersection = original_chars & q_chars
            union = original_chars | q_chars
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard > MULTI_QUERY_SIMILARITY:
                logger.debug(f"[MultiQuery] 过滤高度相似 (Jaccard={jaccard:.2f}): {q}")
                continue

        result.append(q)

    return result


# =====================================================
# 控制器
# =====================================================

# =====================================================
# LangChain Retriever Wrapper
# =====================================================

class MultiQueryRetriever(BaseRetriever):
    """LangChain BaseRetriever wrapper — 替换旧 ParallelMultiQueryRetriever。

    在 _get_relevant_documents 中根据问题复杂度决定是否启用多查询。
    """

    base_retriever: Any = Field(description="底层 ChunkLevelRetriever")

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, base_retriever):
        super().__init__(base_retriever=base_retriever)
        self._controller = MultiQueryController()

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        use_multi, reason = self._controller.should_use_multi_query(query)

        if not use_multi:
            logger.info(f"[MultiQuery] 跳过 ({reason}): {query[:30]}")
            return self.base_retriever.invoke(query)

        # Step 1: Rewrite
        queries = rewrite_queries(query)
        # Step 2: Quality filter
        queries = filter_queries(queries)

        # Step 3: 并发检索
        from concurrent.futures import ThreadPoolExecutor, as_completed
        all_docs = []
        seen_ids = set()
        with ThreadPoolExecutor(max_workers=min(3, len(queries))) as ex:
            futures = {ex.submit(self.base_retriever.invoke, q): q for q in queries}
            for future in as_completed(futures):
                try:
                    docs = future.result()
                    for d in docs:
                        cid = d.metadata.get("chunk_id") or d.metadata.get("doc_id", "?")
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            all_docs.append(d)
                except Exception as e:
                    logger.warning(f"[MultiQuery] 检索失败: {e}")

        logger.info(
            f"[MultiQuery] {reason}: {query[:30]} → "
            f"{len(queries)} variants → {len(all_docs)} docs"
        )
        return all_docs


class MultiQueryController:
    """MultiQuery 检索控制器。

    用法:
        ctrl = MultiQueryController()
        docs = ctrl.retrieve(query, base_retriever, bm25_retriever)
    """

    def __init__(self):
        self.last_log: dict = {}

    def should_use_multi_query(self, query: str) -> tuple[bool, str]:
        """判断是否应该使用 MultiQuery"""
        if MULTI_QUERY_MODE == "off":
            return False, "mode=off"
        if MULTI_QUERY_MODE == "on":
            return True, "mode=on"
        return is_complex_query(query)

    def retrieve(
        self,
        query: str,
        vector_retriever,
        bm25_retriever,
        k: int = 8,
    ) -> tuple[list[Document], dict]:
        """执行 MultiQuery 检索（或简单检索）。

        Returns:
            (documents, log_dict)
        """
        t0 = time.time()
        log_data = {"original_query": query, "triggered": False}

        use_multi, reason = self.should_use_multi_query(query)
        log_data["trigger_reason"] = reason

        if not use_multi:
            # 简单检索
            from retrieval.hybrid import hybrid_retrieve
            docs = hybrid_retrieve(query, vector_retriever, bm25_retriever, k=k)
            log_data["triggered"] = False
            log_data["rewritten_queries"] = [query]
            log_data["filtered_queries"] = [query]
            log_data["candidate_count"] = len(docs)
            log_data["elapsed_ms"] = int((time.time() - t0) * 1000)
            self.last_log = log_data
            logger.info(
                f"[MultiQuery] 跳过 (原因={reason}) → {len(docs)} docs, "
                f"{log_data['elapsed_ms']}ms"
            )
            return docs, log_data

        # MultiQuery 路径
        log_data["triggered"] = True

        # Step 1: Rewrite
        queries = rewrite_queries(query)
        log_data["rewritten_queries"] = queries

        # Step 2: Quality filter
        queries = filter_queries(queries)
        log_data["filtered_queries"] = queries

        # Step 3: 并发检索
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from retrieval.hybrid import hybrid_retrieve

        all_docs = []
        seen_ids = set()
        query_doc_counts = []

        with ThreadPoolExecutor(max_workers=min(3, len(queries))) as ex:
            futures = {ex.submit(
                hybrid_retrieve, q, vector_retriever, bm25_retriever,
                k=MULTI_QUERY_TOP_K_PER,
            ): q for q in queries}
            for future in as_completed(futures):
                try:
                    docs = future.result()
                    query_doc_counts.append(len(docs))
                    for d in docs:
                        cid = d.metadata.get("chunk_id") or d.metadata.get("doc_id", "?")
                        if cid not in seen_ids:
                            seen_ids.add(cid)
                            all_docs.append(d)
                except Exception as e:
                    logger.warning(f"[MultiQuery] 检索失败: {e}")

        # Step 4: RRF fusion (if >1 queries, otherwise single result)
        if len(queries) > 1 and len(all_docs) > MULTI_QUERY_TOP_K_PER:
            from retrieval.hybrid import rrf_fusion_docs
            # 收集每个查询的 docs 用于 RRF 融合
            # 简化：用已有 deduped docs + 已有的 retrieval 融合
            # hybrid_retrieve 内部已做了 vector+BM25 RRF
            pass  # hybrid_retrieve already does RRF internally

        log_data["candidate_count"] = len(all_docs)
        log_data["query_doc_counts"] = query_doc_counts
        log_data["elapsed_ms"] = int((time.time() - t0) * 1000)
        self.last_log = log_data

        logger.info(
            f"[MultiQuery] 触发 (原因={reason}) → {len(queries)} 变体 → "
            f"{len(all_docs)} docs, {log_data['elapsed_ms']}ms"
        )

        return all_docs, log_data
