"""normalize.py — 查询归一化（反空格/标点/Unicode 变体绕过的基础）

只处理"不可见与变体字符"，不做语义改写：
- NFKC：全角→半角、上标/罗马数字等兼容字符归一（"ｉｇｎｏｒｅ" → "ignore"）
- 剥离零宽字符 / 双向控制符（隐写绕过常用手段）
- 剥离其余 ASCII 控制字符
- 折叠连续空白（空格/换行绕过）
- casefold 由调用方在匹配时执行
"""
from __future__ import annotations

import re
import unicodedata

# 零宽字符 + 双向格式控制符
_ZERO_WIDTH = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff"
    "\u00ad\u034f\u061c\u180e]"
)
# ASCII 控制字符（保留 \t \n \r，其余视为异常）
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_WS = re.compile(r"\s{2,}")


def strip_invisible(text: str) -> str:
    """剥离零宽/控制字符（保留可见内容）。"""
    return _CONTROL.sub("", _ZERO_WIDTH.sub("", text))


def normalize_query(raw: str) -> str:
    """归一化用户查询，供规则匹配与审计使用。"""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = strip_invisible(text)
    text = _MULTI_WS.sub(" ", text)
    return text.strip()
