"""token 计数 — 用 tiktoken 真实 tokenizer，替代 len 字符数。"""
from __future__ import annotations

import functools

import tiktoken

from backend.shared.logger import logger

_enc = None
_enc_failed = False


def _get_encoder():
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


# 生产加固：同一文本在切分链路中会被反复计数（splitter.length_function、
# leaf_meta、section 分组等），每次 tiktoken encode 开销不小。
# lru_cache 缓存结果（线程安全，自带锁），大文档重复计数命中缓存。
# maxsize 限制缓存内存；文本本身仍参与 hash（O(n)），远快于 encode。
#
# 口径一致性（P0-05，2026-10-13）：tiktoken BPE 文件在受限网络下会下载
# 失败（ProxyError），且 get_encoding 每次失败后 _enc 仍为 None——同一
# 文本「这次真实编码、下次字符估算」抖动，会让 splitter 的 length_function
# 与超限校验使用不同口径，任何 token 预算切分都无法稳定达标。
# 故首次失败后进程内永久降级（口径恒定），不再逐次重试。
@functools.lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    """返回文本 token 数（cl100k_base，DeepSeek 近似）。"""
    global _enc_failed
    if not text:
        return 0
    if not _enc_failed:
        try:
            return len(_get_encoder().encode(text))
        except Exception as e:
            _enc_failed = True
            logger.warning(
                f"[token_counter] tiktoken 不可用({type(e).__name__})，"
                f"进程内降级字符估算（口径恒定）")
    return _char_estimate(text)


def _char_estimate(text: str) -> int:
    """字符估算兜底：CJK 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return cjk + other // 4
