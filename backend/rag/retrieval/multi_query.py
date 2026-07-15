"""Multi Query Retrieval — 自动触发 + LLM改写 + 质量过滤

need_multi_query() 是唯一入口，所有 MultiQuery 判断必须经过此函数。
"""

import re
from typing import Any, List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from backend.config import (
    MULTI_QUERY_MODE as _DEFAULT_MODE, MULTI_QUERY_COUNT,
    MULTI_QUERY_TOP_K_PER, MULTI_QUERY_DEDUP,
    MULTI_QUERY_SIMILARITY, MULTI_QUERY_MIN_LENGTH,
)
from backend.shared.logger import logger

# 运行时模式（可通过 API POST /llm/multiquery 动态切换）
_mq_mode: str = _DEFAULT_MODE


def get_mq_mode() -> str:
    return _mq_mode


def set_mq_mode(mode: str):
    global _mq_mode
    _mq_mode = mode


# =====================================================
# 唯一入口 — 所有 MultiQuery 判断必须走这里
# =====================================================

def need_multi_query(query: str) -> tuple[bool, str]:
    """判断是否需要 MultiQuery。所有入口统一经过此函数。

    mode=off     → 直接关闭
    mode=always  → 直接开启
    mode=on      → 兼容旧写法，同 always
    mode=auto    → 调用 _is_complex 判断
    """
    mode = _mq_mode

    if mode == "off":
        return False, "off"
    if mode in ("always", "on"):
        return True, mode
    # auto
    return _is_complex(query)


# =====================================================
# 复杂度检测（仅 auto 模式使用）
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
# Query Rewrite: Parse → Normalize → Deduplicate → Limit
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
    """LLM 改写 → Parse → Normalize → Dedup → Limit"""
    try:
        from backend.rag.tracer import trace_collector
        trace_collector._start("query_rewrite")
        from backend.llm.llm_factory import llm
        from langchain_core.messages import HumanMessage

        prompt = QUERY_REWRITE_PROMPT.format(count=MULTI_QUERY_COUNT, question=question)
        result = llm.invoke([HumanMessage(content=prompt)])
        tokens = trace_collector._parse_tokens(result)
        raw = result.content if hasattr(result, "content") else str(result)

        # Step 1: Parse
        lines = _parse(raw)

        # Step 2: Normalize
        lines = _normalize(lines)

        # Step 3: Deduplicate (含完全重复 + Jaccard)
        lines = _dedup(lines, question)

        # Step 4: Limit
        lines = _limit(lines)

        trace_collector._end("query_rewrite", "LLM改写",
                             metrics={**tokens, "variants": len(lines)})
        logger.info(f"[MultiQuery] Rewrite: {question[:40]} → {len(lines)} 变体")
        return lines
    except Exception as e:
        trace_collector._end("query_rewrite", "LLM改写", metrics={"variants": 0}, status="error")
        logger.warning(f"[MultiQuery] Rewrite 失败: {e}，回退")
        return [question]


# ── Parse ──────────────────────────────────────────

_RE_NUMBERING = re.compile(r"^\d+[\.\)、]\s*")           # 1. 2) 3、
_RE_BULLET = re.compile(r"^[-*•]\s*")                     # - * •
_RE_CJK_NUM = re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*")  # （一）（二）

def _parse(raw: str) -> list[str]:
    """解析 LLM 返回内容为查询列表"""
    lines = []
    for line in raw.strip().split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(stripped)
    return lines


# ── Normalize ──────────────────────────────────────

def _normalize(lines: list[str]) -> list[str]:
    """统一文本格式：去编号、去列表符号、去首尾空格、合并空格"""
    result = []
    for line in lines:
        # 去掉中文编号 (一) (二)
        line = _RE_CJK_NUM.sub("", line)
        # 去掉数字编号 1. 2) 3、
        line = _RE_NUMBERING.sub("", line)
        # 去掉列表符号 - * •
        line = _RE_BULLET.sub("", line)
        # 合并连续空格
        line = re.sub(r"\s+", " ", line)
        # 去首尾空格
        line = line.strip()
        if line:
            result.append(line)
    return result


# ── Deduplicate ────────────────────────────────────

def _dedup(lines: list[str], original: str = "") -> list[str]:
    """去重：完全重复 + Jaccard 相似度"""
    # 确保原始查询在第一位
    result = [original] if original else []
    seen = {original} if original else set()

    for line in lines:
        if not line:
            continue
        # 完全重复
        if line in seen:
            logger.debug(f"[MultiQuery] 完全重复: {line}")
            continue
        if not MULTI_QUERY_DEDUP:
            seen.add(line)
            result.append(line)
            continue
        # 最小长度
        if len(line) < MULTI_QUERY_MIN_LENGTH:
            logger.debug(f"[MultiQuery] 过短: {line}")
            continue
        # Jaccard 去重
        if original:
            o_set = set(original)
            l_set = set(line)
            union = o_set | l_set
            if union:
                jaccard = len(o_set & l_set) / len(union)
                if jaccard > MULTI_QUERY_SIMILARITY:
                    logger.debug(f"[MultiQuery] Jaccard重复({jaccard:.2f}): {line}")
                    continue
        seen.add(line)
        result.append(line)
    return result


# ── Limit ──────────────────────────────────────────

def _limit(lines: list[str]) -> list[str]:
    """限制最终查询数量"""
    return lines[:MULTI_QUERY_COUNT]


# =====================================================
# LangChain Retriever
# =====================================================

class MultiQueryRetriever(BaseRetriever):
    """根据 need_multi_query() 判断是否启用多查询。"""

    base_retriever: Any = Field(description="底层 ChunkLevelRetriever")

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, base_retriever):
        super().__init__(base_retriever=base_retriever)
        self._last_triggered = False
        self._last_reason = ""
        self._last_variants = 1
        self._last_filtered = 1

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        use, reason = need_multi_query(query)
        self._last_triggered = use
        self._last_reason = reason
        self._last_variants = 1
        self._last_filtered = 1

        if not use:
            logger.info(f"[MultiQuery] {reason}: {query[:30]}")
            return self.base_retriever.invoke(query)

        queries = _rewrite(query)
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
