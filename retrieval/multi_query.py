"""Multi Query Retrieval — 自动触发 + LLM改写 + 质量过滤

独立于现有 Retriever，可插拔。
"""

import re
from typing import Any, List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from config import (
    MULTI_QUERY_MODE as _DEFAULT_MODE, MULTI_QUERY_COUNT,
    MULTI_QUERY_TOP_K_PER, MULTI_QUERY_DEDUP,
    MULTI_QUERY_SIMILARITY, MULTI_QUERY_MIN_LENGTH,
)
from utils.logger import logger

# 运行时模式（可通过 API POST /llm/multiquery 动态切换）
_mq_mode: str = _DEFAULT_MODE


def get_mq_mode() -> str:
    return _mq_mode


def set_mq_mode(mode: str):
    global _mq_mode
    _mq_mode = mode


# =====================================================
# 复杂度检测
# =====================================================

COMPLEX_PATTERNS = [
    "分析", "对比", "比较", "总结", "汇总", "概述",
    "全部", "所有", "区别", "差异", "不同",
    "流程", "步骤", "怎么做", "如何", "怎么",
    "原因", "为什么", "优缺点", "利弊", "优劣",
    "关系", "影响", "作用", "意义", "方法",
]

SIMPLE_PREFIXES = ("什么是", "多少", "几点", "几号", "谁", "哪个", "哪里")


def _is_complex(query: str) -> tuple[bool, str]:
    q = query.strip()
    for pat in COMPLEX_PATTERNS:
        if pat in q:
            return True, f"关键词: {pat}"
    if q.startswith(SIMPLE_PREFIXES):
        return False, "简单事实问句"
    if len(q) < 5:
        return False, f"过短({len(q)}字)"
    if len(q) > 15:
        return True, f"较长({len(q)}字)"
    return False, "默认简单"


# =====================================================
# Query Rewrite
# =====================================================

QUERY_REWRITE_PROMPT = """将用户问题改写为 {count} 个语义等价但表达不同的检索查询。

规则：
1. 保留原始查询作为第 1 个
2. 不改变原意，不扩展问题
3. 不回答问题，不解释
4. 只输出查询文本，每行一个，不要编号

用户问题：{question}

改写结果："""


def _rewrite(question: str) -> list[str]:
    try:
        from retrieval.tracer import trace_collector
        trace_collector._start("LLM改写")
        from llm.llm_factory import llm
        from langchain_core.messages import HumanMessage

        prompt = QUERY_REWRITE_PROMPT.format(count=MULTI_QUERY_COUNT, question=question)
        result = llm.invoke([HumanMessage(content=prompt)])
        llm_tokens = trace_collector._extract_tokens(result)
        text = result.content if hasattr(result, "content") else str(result)

        lines = [re.sub(r"^\d+[\.\)、]\s*", "", l.strip())
                 for l in text.strip().split("\n") if l.strip()]

        cleaned, seen = [question], {question}
        for line in lines:
            if line not in seen and len(cleaned) < MULTI_QUERY_COUNT:
                seen.add(line)
                cleaned.append(line)

        trace_collector._end("LLM改写", hits=f"{len(cleaned)}变体 | {llm_tokens}")
        logger.info(f"[MultiQuery] Rewrite: {question[:40]} → {len(cleaned)} 变体")
        return cleaned
    except Exception as e:
        trace_collector._end("LLM改写", hits="失败")
        logger.warning(f"[MultiQuery] Rewrite 失败: {e}，回退")
        return [question]


# =====================================================
# Quality Filter
# =====================================================

def _filter(queries: list[str]) -> list[str]:
    if not MULTI_QUERY_DEDUP or len(queries) <= 1:
        return queries
    result = [queries[0]]
    orig_chars = set(queries[0])
    for q in queries[1:]:
        if len(q) < MULTI_QUERY_MIN_LENGTH:
            logger.debug(f"[MultiQuery] 过短: {q}")
            continue
        qc = set(q)
        jaccard = len(orig_chars & qc) / len(orig_chars | qc) if orig_chars | qc else 0
        if jaccard > MULTI_QUERY_SIMILARITY:
            logger.debug(f"[MultiQuery] 相似(Jaccard={jaccard:.2f}): {q}")
            continue
        result.append(q)
    return result


# =====================================================
# LangChain Retriever
# =====================================================

class MultiQueryRetriever(BaseRetriever):
    """替换旧 ParallelMultiQueryRetriever，根据问题复杂度自动触发多查询。"""

    base_retriever: Any = Field(description="底层 ChunkLevelRetriever")

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, base_retriever):
        super().__init__(base_retriever=base_retriever)
        self._last_triggered = False
        self._last_reason = ""
        self._last_variants = 1
        self._last_filtered = 1

    def _should_use_multi(self, query: str) -> tuple[bool, str]:
        mode = _mq_mode
        if mode == "off":
            return False, "off"
        if mode == "on":
            return True, "on"
        return _is_complex(query)

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        use, reason = self._should_use_multi(query)
        self._last_triggered = use
        self._last_reason = reason
        self._last_variants = 1
        self._last_filtered = 1

        if not use:
            logger.info(f"[MultiQuery] {reason}: {query[:30]}")
            return self.base_retriever.invoke(query)

        queries = _filter(_rewrite(query))
        self._last_variants = len(queries)
        self._last_filtered = len(queries)

        from concurrent.futures import ThreadPoolExecutor, as_completed
        docs, seen = [], set()
        with ThreadPoolExecutor(max_workers=min(3, len(queries))) as ex:
            for future in as_completed(
                {ex.submit(self.base_retriever.invoke, q): q for q in queries}
            ):
                try:
                    for d in future.result():
                        cid = d.metadata.get("chunk_id", d.metadata.get("doc_id", "?"))
                        if cid not in seen:
                            seen.add(cid)
                            docs.append(d)
                except Exception as e:
                    logger.warning(f"[MultiQuery] 检索失败: {e}")

        logger.info(
            f"[MultiQuery] {reason}: {query[:30]} → "
            f"{len(queries)}变体 → {len(docs)}docs"
        )
        return docs
