"""混合检索候选池合并。

候选池用于 rerank 前的召回并集；默认保持向量优先的历史顺序，定标/实验
场景可以显式交错两条检索腿，避免其中一路填满候选池导致另一条腿失去作用。
"""

from __future__ import annotations

from collections.abc import Iterable


def _candidate_key(doc) -> tuple[str, str]:
    metadata = getattr(doc, "metadata", {}) or {}
    chunk_id = str(metadata.get("chunk_id") or "").strip()
    if chunk_id:
        return "chunk", chunk_id
    return "content", str(getattr(doc, "page_content", "") or "")


def merge_candidate_docs(
    vector_docs: Iterable,
    sparse_docs: Iterable,
    *,
    limit: int,
    interleave: bool = False,
) -> list:
    """合并两路候选并按身份去重。

    ``interleave`` 只改变候选池的来源顺序，不改变单路内部排序；真正的
    相关性排序仍由后续 reranker 完成。limit 小于等于 0 时返回空列表。
    """
    if limit <= 0:
        return []

    vector = list(vector_docs or [])
    sparse = list(sparse_docs or [])
    ordered = []
    if interleave:
        for index in range(max(len(vector), len(sparse))):
            if index < len(vector):
                ordered.append(vector[index])
            if index < len(sparse):
                ordered.append(sparse[index])
    else:
        ordered = vector + sparse

    seen: set[tuple[str, str]] = set()
    merged = []
    for doc in ordered:
        key = _candidate_key(doc)
        if key in seen:
            continue
        seen.add(key)
        merged.append(doc)
        if len(merged) >= limit:
            break
    return merged
