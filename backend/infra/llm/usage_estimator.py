"""usage_estimator — 流式调用量缺失时的本地 token 估算兜底。

企业做法（2026-10-02）：usage 尾帧拿不到时不再把预占推进待对账人工核查，
而是按本地估算直接结算（cost_status='estimated'）——能估算的不留疑，
真正无法估算的（调用失败且无任何输出）才进待对账。

估算口径（零依赖，中英混合字符统计启发式）：1 个中日韩字符 ≈ 0.6 token、
1 个英文/半角字符 ≈ 0.25 token。这是各家 tokenizer 对中英文的通用近似量级
——平台模型由 DB 注册表按角色配置（MiniMax/Qwen/豆包等多供应商并存），
本地没有也不缓存各模型 tokenizer，字符近似是唯一跨供应商可行的兜底；
换算误差不追求精确，由 estimated 标记显式暴露，系统性漂移由对账日报
与用量聚合监控。

只用字符构成计数、不缓存文本：流式增量 add() 零内存膨胀，长流安全。
"""
from __future__ import annotations

import math

# 中英混合字符近似换算率（token/字符）：中日韩 ≈0.6、英文/半角 ≈0.25。
# 跨供应商通用近似（模型清单来自 DB 注册表，不绑定任何一家）；
# 估算精度由 estimated 标记暴露，不在此处伪装精确。
_TOKENS_PER_CJK_CHAR = 0.6
_TOKENS_PER_OTHER_CHAR = 0.25


def _is_cjk(ch: str) -> bool:
    """中日韩统一表意文字及全角标点（含扩展 A/兼容区）。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF          # CJK 基本区
        or 0x3400 <= code <= 0x4DBF       # 扩展 A
        or 0xF900 <= code <= 0xFAFF       # 兼容表意
        or 0x3000 <= code <= 0x303F       # CJK 标点
        or 0xFF00 <= code <= 0xFFEF       # 全角形式
    )


def estimate_text_tokens(text: str) -> int:
    """按字符构成估算一段文本的 token 数；空文本为 0，非空向上取整。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return max(1, math.ceil(cjk * _TOKENS_PER_CJK_CHAR
                            + other * _TOKENS_PER_OTHER_CHAR))


def _content_text(content) -> str:
    """从 langchain content（str / 多模态 list / 其他）提取纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        )
    return str(content) if content else ""


def estimate_prompt_tokens(call_input) -> int:
    """估算一次模型调用的 prompt tokens（容错遍历 langchain 入参形态）。

    兼容：str、BaseMessage（含 .content）、{"role","content"} dict、
    {"messages": [...]}、以及它们的 list。识别不了的形态按 0 处理
    （宁少勿错——估算只用于结算兜底，缺失部分由 estimated 标记暴露）。
    """
    if call_input is None:
        return 0
    if isinstance(call_input, str):
        return estimate_text_tokens(call_input)
    if isinstance(call_input, list):
        return sum(estimate_prompt_tokens(item) for item in call_input)
    if isinstance(call_input, dict):
        if "messages" in call_input:
            return estimate_prompt_tokens(call_input["messages"])
        return estimate_text_tokens(_content_text(call_input.get("content")))
    content = getattr(call_input, "content", None)
    if content is not None:
        return estimate_text_tokens(_content_text(content))
    return 0


class StreamTextMeter:
    """流式输出增量计量：只累计字符构成，不缓存文本（长流零内存膨胀）。"""

    def __init__(self) -> None:
        self.cjk_chars = 0
        self.other_chars = 0

    def add(self, text: str) -> None:
        if not text:
            return
        for ch in text:
            if _is_cjk(ch):
                self.cjk_chars += 1
            else:
                self.other_chars += 1

    @property
    def chars(self) -> int:
        return self.cjk_chars + self.other_chars

    @property
    def completion_tokens(self) -> int:
        if self.chars == 0:
            return 0
        return max(1, math.ceil(self.cjk_chars * _TOKENS_PER_CJK_CHAR
                                + self.other_chars * _TOKENS_PER_OTHER_CHAR))


__all__ = [
    "StreamTextMeter",
    "estimate_prompt_tokens",
    "estimate_text_tokens",
]
