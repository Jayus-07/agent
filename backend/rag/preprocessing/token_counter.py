"""token 计数 — 用 tiktoken 真实 tokenizer，替代 len 字符数。"""
from __future__ import annotations

import functools

import tiktoken

from backend.shared.logger import logger

_enc = None


def _get_encoder():
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


# 生产加固：同一文本在切分链路中会被反复计数（splitter.length_function、
# leaf_meta、section 分组等），每次 tiktoken encode 开销不小。
# lru_cache 缓存结果（线程安全，自带锁），大文档重复计数命中缓存。
# maxsize 限制缓存内存；文本本身仍参与 hash（O(n)），远快于 encode。
@functools.lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    """返回文本 token 数（cl100k_base，DeepSeek 近似）。"""
    if not text:
        return 0
    try:
        return len(_get_encoder().encode(text))
    except Exception as e:
        # tiktoken 异常时降级字符估算，记录日志不静默吞掉
        logger.warning(f"[token_counter] tiktoken 失败({type(e).__name__})，降级字符估算")
        return _char_estimate(text)


def _char_estimate(text: str) -> int:
    """字符估算兜底：CJK 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return cjk + other // 4
