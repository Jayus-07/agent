"""Chunk Store 详情响应的结构化元数据映射。"""
from __future__ import annotations

import json
from typing import Any


def _metadata_for(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("chunk_metadata")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def build_chunk_detail_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """映射 Chunk Store 字段；兼容已有合同 Parent 的旧元数据。"""
    metadata_by_index = [_metadata_for(row) for row in rows]
    identities = []
    leaf_count = 0
    for row, metadata in zip(rows, metadata_by_index):
        granularity = str(
            row.get("granularity") or metadata.get("granularity") or ""
        )
        chunk_id = str(row.get("chunk_id") or metadata.get("chunk_id") or "")
        parent_id = str(
            row.get("parent_chunk_id")
            or metadata.get("parent_chunk_id")
            or ""
        )
        if granularity == "leaf":
            leaf_count += 1
        identities.append((granularity, chunk_id, parent_id))

    items: list[dict[str, Any]] = []
    for index, (row, metadata) in enumerate(zip(rows, metadata_by_index)):
        granularity, chunk_id, _ = identities[index]
        child_rows = [
            rows[child_index]
            for child_index, (child_granularity, _, child_parent_id)
            in enumerate(identities)
            if child_granularity == "leaf" and child_parent_id == chunk_id
        ]
        chunk_type = str(metadata.get("chunk_type") or "")
        parent_scope = str(metadata.get("parent_scope") or "")
        if (
            granularity == "parent"
            and not parent_scope
            and chunk_type == "contract_context"
            and child_rows
        ):
            # 旧合同块没有范围字段。只用切分器写入的 Parent/Leaf 关联判断
            # 是否覆盖所有 Leaf，不按编号、正文长度或标题猜测。
            parent_scope = (
                "document" if len(child_rows) == leaf_count else "section"
            )

        document_title = str(metadata.get("document_title") or "")
        section_title = str(
            metadata.get("section_title") or row.get("section_title") or ""
        )
        section_titles = _string_list(metadata.get("section_titles"))
        if granularity == "parent":
            if parent_scope == "document":
                path = _string_list(metadata.get("section_path"))
                document_title = document_title or (path[0] if path else "")
                section_title = ""
                section_titles = []
            elif parent_scope == "section":
                if not section_titles:
                    section_titles = list(dict.fromkeys(
                        str(child.get("section_title") or "").strip()
                        for child in child_rows
                        if str(child.get("section_title") or "").strip()
                    ))
                if not section_title and len(section_titles) == 1:
                    section_title = section_titles[0]
            else:
                # 范围未由结构化元数据声明时，不把 Parent 标成章节。
                section_title = ""

        items.append({
            "chunk_index": row.get("chunk_index", index),
            "chunk_id": chunk_id,
            "parent_chunk_id": identities[index][2],
            "granularity": granularity,
            "parent_scope": parent_scope,
            "document_title": document_title,
            "section_title": section_title,
            "section_titles": section_titles,
            "chunk_type": chunk_type,
            "content": row.get("content", ""),
            "char_count": row.get("char_count", 0),
            "keywords": row.get("keywords", ""),
            "llm_keywords": row.get("llm_keywords", ""),
            "llm_model": row.get("llm_model", ""),
            "doc_type": row.get("doc_type", ""),
            "kb_id": row.get("kb_id", ""),
            "department": row.get("department", ""),
            "simulated_questions": row.get("simulated_questions", []),
        })
    return items
