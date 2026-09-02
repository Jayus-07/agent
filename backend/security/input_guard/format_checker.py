"""format_checker.py — L0 格式检查（纯规则，0 成本，不调用 LLM）

检查项：空输入 / 最大字符数 / 最大 token 数 / 大量重复字符 /
异常控制字符 / 私有区等异常 Unicode。
命中即返回 BLOCK 性质的 verdict（由 InputGuard 组装为 GuardResult）。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from backend.config import guard as cfg
from backend.rag.preprocessing.token_counter import count_tokens
from backend.security.input_guard.normalize import _CONTROL
from backend.security.input_guard.types import GuardCategory

# 私有区（含辅助平面）— 正常用户输入几乎不会出现
_PRIVATE_USE = re.compile("[\uE000-\uF8FF\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]")
# 表情/装饰符号区段（少量出现正常，海量出现视为异常负载）
_SYMBOL_HEAVY = re.compile(
    "[\u2600-\u27BF\U0001F000-\U0001FAFF\uFE00-\uFE0F]"
)
_EMOJI_BURST_LIMIT = 30


@dataclass
class FormatVerdict:
    """None = 通过；否则给出拦截类别与原因。"""

    category: GuardCategory | None = None
    reason: str = ""


def check_format(
    raw: str,
    normalized: str,
    *,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    max_repeat_ratio: float | None = None,
    repeat_min_len: int | None = None,
) -> FormatVerdict:
    """对原始 + 归一化后的查询做格式检查。

    参数缺省读 config/guard.py；显式传参仅供测试注入。
    """
    max_chars = cfg.GUARD_MAX_INPUT_CHARS if max_chars is None else max_chars
    max_tokens = cfg.GUARD_MAX_INPUT_TOKENS if max_tokens is None else max_tokens
    max_repeat_ratio = (
        cfg.GUARD_MAX_REPEAT_RATIO if max_repeat_ratio is None else max_repeat_ratio
    )
    repeat_min_len = cfg.GUARD_REPEAT_MIN_LEN if repeat_min_len is None else repeat_min_len

    # 1. 空输入
    if not raw or not raw.strip():
        return FormatVerdict(GuardCategory.INVALID, "空输入")

    # 2. 归一化后为空（全是零宽/控制字符）
    if not normalized.strip():
        return FormatVerdict(GuardCategory.ABNORMAL, "输入仅含不可见字符")

    # 3. 超长字符
    if len(raw) > max_chars:
        return FormatVerdict(
            GuardCategory.TOO_LONG, f"输入超过最大字符数 {max_chars}"
        )

    # 4. 超 token（复用 rag 的 tiktoken 计数，带 LRU 缓存）
    tokens = count_tokens(normalized)
    if tokens > max_tokens:
        return FormatVerdict(
            GuardCategory.TOO_LONG,
            f"输入超过最大 token 数 {max_tokens}（估算 {tokens}）",
        )

    # 5. 大量重复字符（如 "1111111..." / "哈哈哈哈..."）
    if len(normalized) >= repeat_min_len:
        non_ws_chars = [ch for ch in normalized if not ch.isspace()]
        if non_ws_chars:
            top_char, top_count = Counter(non_ws_chars).most_common(1)[0]
            if top_count / len(non_ws_chars) > max_repeat_ratio:
                return FormatVerdict(
                    GuardCategory.ABNORMAL,
                    f"单一字符重复率过高（{top_char!r} 占 "
                    f"{top_count / len(non_ws_chars):.0%}）",
                )

    # 6. 控制字符密集（1-2 个换行/制表符正常，密集出现视为异常负载）
    ctrl_hits = _CONTROL.findall(raw)
    if len(ctrl_hits) >= 3:
        return FormatVerdict(
            GuardCategory.ABNORMAL, f"包含 {len(ctrl_hits)} 个控制字符"
        )

    # 7. 私有区字符（Unicode 变体绕过常用手段）
    if _PRIVATE_USE.search(raw):
        return FormatVerdict(GuardCategory.ABNORMAL, "包含私有区 Unicode 字符")

    # 8. 表情/装饰符号轰炸
    if len(_SYMBOL_HEAVY.findall(raw)) > _EMOJI_BURST_LIMIT:
        return FormatVerdict(GuardCategory.ABNORMAL, "异常大量符号/表情字符")

    return FormatVerdict()
