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


def sample_for_summary(text: str, *, max_chars: int | None = None) -> str:
    """文档全文 → 摘要采样（覆盖范围显式标注）。

    max_chars（P1，2026-10-24）：显式预算（LLM 元数据抽取接入用）。
    全文 ≤ 预算时原文返回；超出预算时保持「头 + 章节标题目录 + 中段
    分层采样 + 尾」结构，各段按预算缩放——超长文档的 LLM 抽取不再退回
    「头 60% + 中 20% + 尾 20%」的旧采样（中间章节缺席）。不传时行为
    与旧版完全一致。
    """
    text = text or ""
    n = len(text)
    if max_chars is not None and max_chars >= n:
        return text
    if n <= _SHORT:
        return text
    if n <= _MID:
        cut = int(n * 0.6)
        return text[:cut] + "\n...(中略)...\n" + text[-int(n * 0.4):]
    if max_chars is None and n <= _FULL_LIMIT:
        return text

    # ── 超长/超预算：头 + 章节目录 + 分层采样 + 尾 ──
    if max_chars is not None:
        budget = max(1000, int(max_chars))
        head_len = max(200, int(budget * 0.5))
        tail_len = max(100, int(budget * 0.2))
        # 分层步长自适应：预算内取 ~6 个中段片段
        span = max(1, n - head_len - tail_len)
        stride = max(200, int(span / 6))
        block_head = min(300, stride)
        toc_limit = 100
    else:
        head_len, tail_len = _HEAD, _TAIL
        stride, block_head, toc_limit = _STRIDE_BLOCK, _BLOCK_HEAD, 200

    lines = text.split("\n")
    toc = [
        ln.strip() for ln in lines
        if _HEADING_LINE_RE.match(ln.strip())
    ]
    toc_kept = toc[:toc_limit]
    toc_text = "\n".join(toc_kept)
    head = text[:head_len]
    tail = text[-tail_len:] if n > head_len + tail_len else ""

    middle_start, middle_end = head_len, n - tail_len
    samples: list[str] = []
    pos = middle_start
    while pos < middle_end:
        block = text[pos:min(pos + block_head, middle_end)]
        if block.strip():
            samples.append(block.rstrip())
        pos += stride
    sampled = "\n…\n".join(samples)

    covered = head_len + len(samples) * block_head + tail_len
    banner = (
        f"\n...(本文档共约 {n} 字符；以下采样覆盖：文档开头、"
        f"{len(toc_kept)} 条章节标题目录、中段 {len(samples)} 个分层片段、"
        f"文档结尾；采样总覆盖约 {covered} 字符，中间未展示内容未读)...\n"
    )
    if max_chars is None:
        logger.info(
            f"[SummarySampling] 超长文档采样: total={n} toc={len(toc_kept)} "
            f"blocks={len(samples)} covered~{covered}"
        )
    else:
        logger.info(
            f"[SummarySampling] 预算采样: total={n} budget={budget} "
            f"toc={len(toc_kept)} blocks={len(samples)} covered~{covered}"
        )
    return (
        head
        + "\n\n...(章节标题目录)...\n" + toc_text
        + "\n\n...(中段分层采样)...\n" + sampled
        + "\n\n...(结尾)...\n" + tail
        + banner
    )
