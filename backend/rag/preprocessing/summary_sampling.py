"""summary_sampling — 摘要采样（章节感知，2026-10-13 P1 元数据治理）。

原 `_sample_for_summary` 对超长文档只取「头 20k + 尾 20k」，中间章节完全
缺席，却把采样结果当作整份文档的代表喂给元数据 LLM（摘要/关键词/分类）。
本模块的采样策略：
  - 短/中文档全文（<=50k 字符，与旧行为一致）；
  - 超长文档：文档头（通常含目的/适用范围）+ 全文章节标题目录 +
    中段等距分层采样（每段开头）+ 文档尾，并在采样中显式标注覆盖范围，
    不虚构未读内容。

纯函数、零 LLM 依赖；indexer 与 MetadataStage 共用（消除重复副本）。
"""
from __future__ import annotations

import re

from backend.shared.logger import logger

_SHORT = 2000
_MID = 8000
_FULL_LIMIT = 50000
_HEAD = 8000
_TAIL = 4000
_STRIDE_BLOCK = 30000    # 中段每 30k 字符取一个采样块
_BLOCK_HEAD = 800        # 每块取开头字符数

# 标题行（章节目录用）：Markdown 标题 / 中文编号 / 第N章条 / 阿拉伯编号
_HEADING_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s+\S.*"
    r"|第[一二三四五六七八九十百\d]+[章节篇][^\n]{0,40}"
    r"|[一二三四五六七八九十]+、[^\n]{0,40}"
    r"|\d+(?:\.\d+)*[、.)]?\s*[^\n]{0,40})$"
)


def sample_for_summary(text: str) -> str:
    """文档全文 → 摘要采样（覆盖范围显式标注）。"""
    n = len(text or "")
    if n <= _SHORT:
        return text or ""
    if n <= _MID:
        cut = int(n * 0.6)
        return text[:cut] + "\n...(中略)...\n" + text[-int(n * 0.4):]
    if n <= _FULL_LIMIT:
        return text

    # ── 超长：头 + 章节目录 + 分层采样 + 尾 ──
    lines = text.split("\n")
    toc = [
        ln.strip() for ln in lines
        if _HEADING_LINE_RE.match(ln.strip())
    ]
    toc_text = "\n".join(toc[:200])   # 目录上限 200 行防膨胀
    head = text[:_HEAD]
    tail = text[-_TAIL:] if n > _HEAD + _TAIL else ""

    middle_start, middle_end = _HEAD, n - _TAIL
    samples: list[str] = []
    pos = middle_start
    while pos < middle_end:
        block = text[pos:min(pos + _BLOCK_HEAD, middle_end)]
        if block.strip():
            samples.append(block.rstrip())
        pos += _STRIDE_BLOCK
    sampled = "\n…\n".join(samples)

    covered = _HEAD + len(samples) * min(_BLOCK_HEAD, _STRIDE_BLOCK) + _TAIL
    banner = (
        f"\n...(本文档共约 {n} 字符；以下采样覆盖：文档开头、"
        f"{len(toc[:200])} 条章节标题目录、中段 {len(samples)} 个分层片段"
        f"（约每 {_STRIDE_BLOCK} 字符取段首）、文档结尾；"
        f"采样总覆盖约 {covered} 字符，中间未展示内容未读)...\n"
    )
    logger.info(
        f"[SummarySampling] 超长文档采样: total={n} toc={len(toc[:200])} "
        f"blocks={len(samples)} covered~{covered}"
    )
    return (
        head
        + "\n\n...(章节标题目录)...\n" + toc_text
        + "\n\n...(中段分层采样)...\n" + sampled
        + "\n\n...(结尾)...\n" + tail
        + banner
    )
