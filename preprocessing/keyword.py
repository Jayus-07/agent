"""关键词提取 — 基于规则 + 领域词 + jieba

历史遗留说明:
  - 旧版有 `llm_extract_keywords*`（LLM 补全路径）已在 2026-07-02 清理：
    1. 仅在 `len(keywords) < 3` 时触发，规则+jieba 路径通常已返回 6 个关键词
    2. 无测试覆盖，0 生产路径命中
    3. 删除以减少 LLM 依赖 + 代码维护面
"""
import re
from functools import lru_cache
from typing import List, Set

import jieba.analyse

from config import DEFAULT_KEYWORDS, DOMAIN_RULES, SIGNAL_RULES, blacklist
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
    """提取文档级别关键词（复用 chunk 逻辑）

    历史: 旧版会调 LLM 补全，但 LLM 路径仅在 `len(keywords) < 3` 触发，
    实际几乎不会命中。LLM 路径已删除。
    """
    return extract_chunk_keywords(text, top_k=top_k)
