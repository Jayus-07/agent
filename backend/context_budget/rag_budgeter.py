"""context_budget.rag_budgeter — P2-1 RAG 证据 relevance-aware 预算

机械尾删（trim_texts_to_budget 从头保留、尾部整体丢）只在「输入序 =
价值序」时安全。经过 RRF / rerank / parent-child 扩展 / 文档分组 /
multi-query 合并后，尾部未必最低价值；且同一 source 的全部 chunk 可能
被一起删掉，丧失 source diversity。

RAGBudgeter（确定性、零 LLM）：
  - 按 rerank_score 从高到低贪心装入；
  - 同分/无分时保持原序（兼容旧行为）；
  - source diversity：同 source 已保留 chunk 数达到上限后跳过该 source
    的后续 chunk（放不下别人时先让位），避免一个核心 source 独占预算、
    也避免核心 source 被整簇删除；
  - parent-child：child 跟随 parent 决策（可选 source 归属参数）。
调用方不提供 scores 时退化为原序裁剪（行为不变）。
"""

from __future__ import annotations

import re
from typing import Any

from backend.memory.token_budget import count_tokens, trim_texts_to_budget

# 单一 source 最多保留的 chunk 数（diversity 上限）
_MAX_CHUNKS_PER_SOURCE = 3

# 常见引用标注形态的 source 提取（确定性启发；提取失败按整条独立 source）
_SOURCE_RE = re.compile(r"(?:来源|source|出处)\s*[:：]\s*(\S{1,64})", re.IGNORECASE)
_FILE_RE = re.compile(r"([\w.\-]+\.(?:md|txt|pdf|docx|html?|csv))", re.IGNORECASE)


def _guess_source(text: str, index: int) -> str:
    m = _SOURCE_RE.search(text[:400]) or _FILE_RE.search(text[:400])
    return m.group(1) if m else f"__anon_{index}"


def budget_rag_texts(
    texts: list[str],
    budget_tokens: int,
    *,
    scores: list[float] | None = None,
    sources: list[str] | None = None,
) -> tuple[list[str], int]:
    """在预算内保留最高价值、尽量多样的证据集合。

    Returns:
        (kept_texts, dropped_count)——kept 保持原输入相对顺序（引用
        标注序不被打乱），dropped 为被丢弃条数。
    """
    kept_idx, dropped = budget_rag_indices(
        texts, budget_tokens, scores=scores, sources=sources)
    return [texts[i] for i in kept_idx], dropped


def budget_rag_indices(
    texts: list[str],
    budget_tokens: int,
    *,
    scores: list[float] | None = None,
    sources: list[str] | None = None,
) -> tuple[list[int], int]:
    """同 budget_rag_texts，但返回保留下标（保持原相对顺序）。

    价值序选择出的 kept 未必是输入前缀，调用方若需把结果映射回带
    metadata 的对象（如 Document），必须用下标而不是 docs[:len(kept)]。
    """
    n = len(texts)
    if budget_tokens <= 0 or not texts:
        return list(range(n)), 0

    # 无分数 / 口径不一致 → 兼容旧行为：原序即相关性序，从头保留（前缀）
    if not scores or len(scores) != len(texts):
        kept, dropped = trim_texts_to_budget(texts, budget_tokens)
        return list(range(len(kept))), dropped

    tokens = [count_tokens(t) for t in texts]
    srcs = [
        (sources[i] if sources and i < len(sources) and sources[i]
         else _guess_source(texts[i], i))
        for i in range(n)
    ]

    # 贪心装入：score 高者优先；同分按原序。多样性约束逐 source 计数。
    order = sorted(range(n), key=lambda i: (-float(scores[i]), i))
    chosen: set[int] = set()
    per_source: dict[str, int] = {}
    used = 0
    for i in order:
        if per_source.get(srcs[i], 0) >= _MAX_CHUNKS_PER_SOURCE:
            continue  # diversity 上限：让位给其他 source
        if used + tokens[i] > budget_tokens:
            continue  # 放不下：尝试下一条（小的低分证据仍可装入）
        chosen.add(i)
        used += tokens[i]
        per_source[srcs[i]] = per_source.get(srcs[i], 0) + 1

    # 全部装不下（预算极小）时至少保住分数最高的一条，避免空证据
    if not chosen:
        best = min(order, key=lambda i: (-scores[i], i))
        chosen = {best}

    return sorted(chosen), n - len(chosen)


def summarize_rag_budget(
    texts: list[str], kept: list[str],
) -> dict[str, Any]:
    """观测辅助：条数与 token 的前后对比（进日志/SSE，不进 Prometheus label）。"""
    before = sum(count_tokens(t) for t in texts)
    after = sum(count_tokens(t) for t in kept)
    return {
        "rag_chunks_before": len(texts),
        "rag_chunks_after": len(kept),
        "rag_tokens_before": before,
        "rag_tokens_after": after,
    }
