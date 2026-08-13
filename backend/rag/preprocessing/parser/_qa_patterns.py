"""_qa_patterns.py — FAQ 文档 Q/A 节点识别（MD/TXT 共用）。

三种模式**独立**识别，由 caller 根据 pattern_type 决定路由：
- `qa_bold`: `**Q:** ... **A:** ...`
- `qa_heading`: `### 问题 / ### 答案`
- `qa_numbered`: `Q1. ... A1. ...`

为什么三种独立产出而非「首个命中即停」：
- 可扩展：未来支持混合模式时无需重构
- 可观测：每种模式命中数可知，方便统计 FAQ 文档格式分布
- 可控制：caller 可按需过滤（如只信任 `qa_heading`，忽略 `qa_numbered`）
"""
from __future__ import annotations

import re

# 模式 1: **Q:** 问题 \n **A:** 答案
# 兼容两种加粗写法：
#   - 标签加粗、正文不加粗（真实文档常见）：`**Q:** 问题` / `**A:** 答案`
#   - 整行加粗（旧测试格式）：`**Q: 问题**` / `**A: 答案**`
_QA_BOLD_RE = re.compile(
    r"\*\*Q[:：]\*{0,2}\s*(.+?)(?:\*\*)?\n+\s*\*\*A[:：]\*{0,2}\s*(.+?)(?:\*\*)?(?=\n+\s*\*\*Q[:：]|\Z)",
    re.DOTALL,
)

# 模式 2: ## 问题 ... ## 答案 ...（heading 配对）
# 配对模式：问题 heading + 内容 + 答案 heading + 内容
_QA_HEADING_RE = re.compile(
    r"(?m)^#{2,4}\s+(?:问题|Question|问|疑问)[:：]?\s*\n+(.+?)\n+"
    r"#{2,4}\s+(?:答案|Answer|答|解答)[:：]?\s*\n+(.+?)(?=\n#{2,4}|\Z)",
    re.DOTALL,
)

# 模式 3: Q1. ... A1. ...（编号配对）
_QA_NUMBERED_RE = re.compile(
    r"^Q(\d+)[.、]?\s*(.+?)\n+A\1[.、]?\s*(.+?)(?=\nQ\d+|\Z)",
    re.MULTILINE | re.DOTALL,
)

_PATTERNS: list[tuple[str, re.Pattern, int, int]] = [
    # (pattern_type, regex, question_group_idx, answer_group_idx)
    ("qa_bold", _QA_BOLD_RE, 1, 2),
    ("qa_heading", _QA_HEADING_RE, 1, 2),
    ("qa_numbered", _QA_NUMBERED_RE, 2, 3),  # g1=Q 数字（跳过）, g2=问题, g3=答案
]


def extract_qa_pairs(
    text: str,
) -> list[tuple[str, str, str]]:
    """提取所有 Q/A 对，每个元素 = (question, answer, pattern_type)。

    三种模式独立识别并产出，互不冲突。返回顺序按 pattern 在 _PATTERNS
    中的顺序；同 pattern 内按出现顺序。
    """
    results: list[tuple[str, str, str]] = []
    for ptype, pattern, q_idx, a_idx in _PATTERNS:
        for m in pattern.finditer(text):
            q = m.group(q_idx).strip()
            a = m.group(a_idx).strip()
            if q and a:  # 过滤空匹配
                results.append((q, a, ptype))
    return results


def looks_like_qa_doc(text: str, min_pairs: int = 1) -> bool:
    """判断文档是否像 FAQ（任意 pattern 累计 ≥ min_pairs 个 Q/A 对）。

    默认 min_pairs=1：单条 Q/A 也算 FAQ（用户上传的单问题 FAQ 常见）。
    """
    return len(extract_qa_pairs(text)) >= min_pairs


def dominant_pattern(text: str) -> str:
    """返回出现最多的 pattern_type（用于统计 / 路由决策）。无 Q/A 时返回 ""。"""
    pairs = extract_qa_pairs(text)
    if not pairs:
        return ""
    counts: dict[str, int] = {}
    for _, _, ptype in pairs:
        counts[ptype] = counts.get(ptype, 0) + 1
    return max(counts, key=counts.get)  # type: ignore[arg-type]
