"""知识库快照内的证据切片检索（关键词口径）。

证据引用只能来自快照内切片（与转正校验 ``verify_evidence_anchors`` 同一
来源约束），因此检索范围固定为 ``corpus_snapshots.load`` 返回的 fulltext
行，不查线上知识库、不调用 embedding 模型——MVP 用关键词评分保证确定性
与零成本；后续如需语义召回，在此模块内替换评分实现即可，路由与契约不变。

fulltext 行来自 chunk 表（``SELECT *``），可用字段以 chunk_store_pg 为准：
``doc_id`` / ``chunk_id`` / ``chunk_index`` / ``content`` / ``chunk_metadata``。
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")


def _query_terms(query: str) -> list[str]:
    """查询词：拉丁/数字按词，中文按 2-gram（单字命中噪声太大）。"""
    normalized = str(query or "").strip().lower()
    if not normalized:
        return []
    terms = set(_WORD_RE.findall(normalized))
    # 中文按连续片段切 2-gram
    for fragment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(fragment) == 1:
            terms.add(fragment)
            continue
        terms.update(fragment[i: i + 2] for i in range(len(fragment) - 1))
    return sorted(terms)


def _row_text(row: dict) -> str:
    return str(row.get("content") or "")


def _row_title(row: dict) -> str:
    metadata = row.get("chunk_metadata")
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if isinstance(metadata, dict):
        return str(metadata.get("title") or "")
    return ""


def _snippet(text: str, terms: list[str], limit: int = 200) -> str:
    flat = text.replace("\n", " ")
    hit = -1
    for term in terms:
        hit = flat.lower().find(term)
        if hit >= 0:
            break
    if hit < 0:
        return flat[:limit]
    start = max(0, hit - limit // 4)
    return flat[start: start + limit]


def search_evidence(fulltext_rows: list[dict], query: str, top_k: int = 5) -> list[dict]:
    """按关键词命中数对快照切片评分，返回 top_k 条证据候选。"""
    terms = _query_terms(query)
    if not terms:
        return []
    scored: list[dict] = []
    for row in fulltext_rows:
        text = _row_text(row)
        if not text.strip():
            continue
        lowered = text.lower()
        score = sum(1 for term in terms if term in lowered)
        if score <= 0:
            continue
        scored.append({
            "doc_id": str(row.get("doc_id") or ""),
            "chunk_id": str(row.get("chunk_id") or ""),
            "chunk_index": int(row.get("chunk_index") or 0),
            "title": _row_title(row),
            "snippet": _snippet(text, terms),
            # 逐字原文（截断）：证据 quote 必须能在快照原文中逐字核验，
            # 因此返回未改写的内容切片，前端引用时直接整段填入。
            "content": text[:1000],
            "score": score,
        })
    scored.sort(key=lambda item: (-item["score"], item["doc_id"], item["chunk_index"]))
    return scored[: max(1, int(top_k))]
