"""shared/text_split.py — LLM/用户给出的「标签/地址列表」字符串统一拆分。

背景（2026-09-21 格式问题巡检）：travel POI 偏好标签、邮件收件人、
数据采集去重键都在解析同一类输入——模型用自然语言习惯给分隔符
（中文逗号、**顿号**、分号、空格混用），此前各处只处理英文逗号或
只补了中文逗号，顿号这种中文最高频的列举分隔符全仓无人处理，
导致标签过滤静默失效、收件人变成畸形地址。

统一规则：中英文逗号 / 顿号 / 中英文分号 / 空白 皆视为分隔符，
拆分后去首尾空白、丢弃空项。单值输入（不含分隔符）原样返回一项。
"""
from __future__ import annotations

import re

__all__ = ["split_list"]

_SEPARATOR_RE = re.compile(r"[,，、;；\s]+")


def split_list(raw: str) -> list[str]:
    """把「a, b、c；d」式列表串拆成 ``["a", "b", "c", "d"]``。

    Args:
        raw: 原始字符串（可为空）。

    Returns:
        拆分后的非空项列表；输入为空返回 ``[]``。
    """
    if not raw:
        return []
    return [p for p in _SEPARATOR_RE.split(raw) if p]
