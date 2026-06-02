import re
from functools import lru_cache
from typing import List, Set

import jieba.analyse

from config import DEFAULT_KEYWORDS, DOMAIN_RULES, SIGNAL_RULES, blacklist
from llm.llm_factory import llm
from utils.logger import logger

# =====================================================
# 预编译正则 — 单次扫描替代 O(n) 逐条循环
# =====================================================
_re_keywords = re.compile(
    '|'.join(re.escape(kw) for kw in DEFAULT_KEYWORDS),
    re.IGNORECASE
)

_re_domain_kw = re.compile(
    '|'.join(re.escape(kw) for rules in DOMAIN_RULES.values() for kw in rules),
    re.IGNORECASE
)


@lru_cache(maxsize=512)
def extract_chunk_keywords_cached(text: str, top_k: int = 6) -> List[str]:
    """提取 chunk 级别关键词（带缓存，预编译正则加速）"""
    keywords: Set[str] = set()
    text_lower = text.lower()

    # 1. 规则词匹配（编译正则单次扫描）
    keywords.update(_re_keywords.findall(text_lower))

    # 2. 领域词匹配
    keywords.update(_re_domain_kw.findall(text_lower))

    # 3. 信号词检测（配置驱动，可适配不同业务领域）
    for signal_name, signal_kws in SIGNAL_RULES.items():
        if any(kw.lower() in text_lower for kw in signal_kws):
            keywords.add(signal_name)

    # 4. jieba 补充
    try:
        extra = jieba.analyse.extract_tags(text, topK=top_k)
        keywords.update(extra)
    except Exception as e:
        logger.debug(f"jieba关键词提取异常: {e}")

    # 黑名单过滤 + 单字过滤
    result = [
        k for k in keywords
        if k not in blacklist and len(k) > 1
    ]

    return result[:top_k]


def extract_chunk_keywords(text: str, top_k: int = 6) -> List[str]:
    """提取 chunk 级别关键词（入口函数）"""
    return extract_chunk_keywords_cached(text, top_k)


def extract_doc_keywords(text: str, top_k: int = 10) -> List[str]:
    """提取文档级别关键词（增强版）"""
    keywords: Set[str] = set()

    # 1. 复用 chunk 逻辑
    keywords.update(extract_chunk_keywords(text, top_k=top_k))

    # 2. LLM 增强（只在关键词不足时调用）
    if len(keywords) < 3:
        llm_kws = llm_extract_keywords(text, top_k)
        keywords.update(llm_kws)

    return list(keywords)[:top_k]


@lru_cache(maxsize=128)
def llm_extract_keywords_cached(text: str, top_k: int = 8) -> List[str]:
    """使用 LLM 提取关键词（带缓存）"""
    try:
        truncated_text = text[:1500]

        resp = llm.invoke(f"""
你是企业知识库关键词提取助手。

任务：从以下文本中提取 {top_k} 个最重要的可检索关键词。

要求：
1. 只输出关键词，用逗号分隔
2. 不要解释、不要编号
3. 优先提取：人名、产品名称、业务术语、品类、流程节点
4. 每个关键词 2-10 个字

文本：
{truncated_text}

关键词：
""")

        keywords = [x.strip() for x in resp.content.split(",") if x.strip() and len(x.strip()) > 1]
        logger.debug(f"LLM关键词提取: {keywords}")
        return keywords[:top_k]
    except Exception as e:
        logger.warning(f"LLM关键词提取失败: {e}")
        return []


def llm_extract_keywords(text: str, top_k: int = 8) -> List[str]:
    """使用 LLM 提取关键词（入口函数）"""
    return llm_extract_keywords_cached(text, top_k)
