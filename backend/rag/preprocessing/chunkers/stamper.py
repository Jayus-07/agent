"""stamper.py — 结构标记、页码回填与 Chunk 标识治理。

包含 stamp_chunk_pages（双向文本包含页码映射）、stamp_chunk_structure（章节身份、
稳定源节点与定位打标）、局部命名空间与唯一 ID 保证。
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import (
    DocumentAST, DocumentNode, LEAF_TYPES, walk,
    infer_section_type, iter_sections, section_tree,
)
from backend.rag.preprocessing.chunkers.base import _is_legal_clause

_MAX_PAGE_SPAN_FOR_STAMP = 50


def _merge_source_locations(meta: dict, locations: list[dict]) -> None:
    """合并解析时直接传递与后置文本回映射得到的来源位置。"""
    merged: set[tuple[int, tuple | None]] = set()
    raw_existing = meta.get("source_locations_json")
    if isinstance(raw_existing, str):
        try:
            raw_existing = json.loads(raw_existing)
        except (TypeError, ValueError):
            raw_existing = []
    if isinstance(raw_existing, list):
        locations = [*raw_existing, *locations]
    for location in locations:
        if not isinstance(location, dict):
            continue
        try:
            page = int(location.get("page") or 0)
        except (TypeError, ValueError):
            continue
        if page <= 0:
            continue
        raw_bbox = location.get("bbox")
        bbox = tuple(raw_bbox) if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4 else None
        merged.add((page, bbox))
    if not merged:
        return
    ordered = sorted(merged, key=lambda item: (item[0], item[1] or ()))[:32]
    meta["pages"] = ",".join(str(page) for page in sorted({p for p, _ in merged}))
    meta["source_locations_json"] = json.dumps(
        [
            {"page": page, "bbox": list(bbox) if bbox else None}
            for page, bbox in ordered
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _node_source_location(node) -> dict | None:
    """取 AST 节点解析器确认的页码与 bbox，避免改写表格文本后回映射丢位置信息。"""
    try:
        page = int(getattr(node, "page_number", 0) or 0)
    except (TypeError, ValueError):
        return None
    if page <= 0:
        return None
    raw_bbox = getattr(node, "bbox", ()) or ()
    bbox = list(raw_bbox) if len(raw_bbox) == 4 else None
    return {"page": page, "bbox": bbox}


def _source_location_metadata(locations: list[dict | None]) -> dict:
    """生成向量库可存储的页码和位置标量元数据。"""
    metadata: dict = {}
    _merge_source_locations(metadata, [location for location in locations if location])
    return metadata


def stamp_chunk_pages(chunks: list, ast: DocumentAST) -> None:
    """把叶子节点的页码（PDF 解析 §5.2 可追溯字段 page_number）映射进 chunk metadata。

    原文定位（P0，2026-10-03）：chunk 是叶子文本的合并/二次切分产物，切分
    策略只保留文本不保留节点引用，这里用双向文本包含做确定性回映射：
      - 叶子前缀 ⊆ chunk 文本 → 叶子被并入此 chunk（合并型策略）
      - chunk 核心 ⊆ 叶子全文 → chunk 是该叶子的切片（Fixed/Recursive 对
        超长叶子二次切分，切片可能不含叶子开头）
    两个方向都不含（策略改写了文本）→ 不标页码，宁缺勿错——错误的页码比
    没有页码更伤引用可信度。存标量逗号串（向量库 metadata 标量化惯例，
    同 ocr_triggered）；非 PDF 文档叶子无 page_number → 整体无页码可标。
    单点收口在 parse_and_chunk_full，8 个切分策略零改动（G2）。
    """
    sources: list[tuple[str, str, int, tuple]] = []
    fallback_sources: list[tuple[str, int, tuple]] = []
    for n in walk(ast.root):
        if n.type not in LEAF_TYPES and n.type != "section":
            continue
        try:
            page = int(getattr(n, "page_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if page <= 0:
            continue
        text = (n.text or "").strip()
        if text:
            bbox = tuple(getattr(n, "bbox", ()) or ())
            sources.append((text[:60], text, page, bbox))
            if n.type in LEAF_TYPES:
                normalized = " ".join(text.split()).casefold()
                fallback_sources.append((normalized, page, bbox))
    if not sources:
        return

    def _normalize(value: str) -> str:
        return " ".join(value.split()).casefold()

    for c in chunks:
        meta = getattr(c, "metadata", None)
        body = (getattr(c, "page_content", "") or "").strip()
        if not isinstance(meta, dict) or not body:
            continue
        core = body[:200]
        matched_locations: dict[str, set[tuple[int, tuple]]] = {}
        for prefix, full, page, bbox in sources:
            # 先用尽可能长且唯一的文本作为匹配证据。旧逻辑只要前 60 个
            # 字符出现在 chunk 中就收集页码；重复页眉/页脚因此会把一个
            # Leaf 错映射到整份 PDF。相同证据若落在多处，宁可不标位置。
            if full and full in body:
                evidence = _normalize(full)
            elif prefix and prefix in body:
                evidence = _normalize(prefix)
            elif len(core) >= 40 and core in full:
                evidence = _normalize(core)
            else:
                continue
            matched_locations.setdefault(evidence, set()).add((page, bbox))

        locations: set[tuple[int, tuple]] = {
            next(iter(found))
            for found in matched_locations.values()
            if len(found) == 1
        }
        # 超短切分块没有足够长的核心用于双向包含，但可能仍能唯一回源。
        # 仅在其完整规范化文本只命中一个带页码叶子位置时补映射；重复短语
        #（如“执行”“应当”）不标页，继续遵守宁缺勿错。
        if not locations:
            normalized_body = _normalize(body)
            if len(normalized_body) >= 4:
                exact_locations = {
                    (page, bbox)
                    for full, page, bbox in fallback_sources
                    if normalized_body in _normalize(full)
                }
                if len(exact_locations) == 1:
                    locations = exact_locations
        if locations:
            pages_set = {page for page, _ in locations}
            if len(pages_set) > 1 and (
                max(pages_set) - min(pages_set)
            ) > _MAX_PAGE_SPAN_FOR_STAMP:
                continue
            _merge_source_locations(meta, [
                {"page": page, "bbox": list(bbox) if bbox else None}
                for page, bbox in locations
            ])


def stamp_chunk_structure(chunks: list, ast: DocumentAST) -> None:
    """为 Chunk 补齐章节身份、稳定源节点 ID 和格式专属定位。"""
    sections_by_path: dict[tuple[str, ...], list[DocumentNode]] = {}
    for section, path in iter_sections(ast):
        if section.level <= 0:
            continue
        sections_by_path.setdefault(tuple(path), []).append(section)

    tree = section_tree(ast)
    section_by_heading_path = {
        tuple(item.get("heading_path") or []): item for item in tree
    }
    section_by_id = {
        str(item.get("section_id") or ""): item for item in tree
    }
    ast_section_ids = set(section_by_id)
    derived_sections: list[dict] = []
    document_title = str(getattr(ast, "document_title", "") or "").strip()

    def _as_list(value) -> list:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        return value if isinstance(value, list) else []

    def _new_section_id(path: list[str], parent_id: str) -> str:
        identity = ["section", document_title, list(path), parent_id]
        return hashlib.sha256(
            json.dumps(
                identity, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()[:24]

    def _remap_section_id(value, old_id: str, new_id: str):
        if isinstance(value, list):
            return [_remap_section_id(item, old_id, new_id) for item in value]
        if isinstance(value, dict):
            return {
                key: (
                    new_id
                    if key == "section_id" and str(item) == old_id
                    else _remap_section_id(item, old_id, new_id)
                )
                for key, item in value.items()
            }
        return value

    def _apply_section_id_remap(metadata: dict, old_id: str, new_id: str) -> None:
        for key in ("table_structures_json", "sections_json", "references"):
            value = metadata.get(key)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    continue
                metadata[key] = json.dumps(
                    _remap_section_id(parsed, old_id, new_id),
                    ensure_ascii=False, separators=(",", ":"),
                )
            elif isinstance(value, (list, dict)):
                metadata[key] = _remap_section_id(value, old_id, new_id)

    def _resolve_section(path: tuple[str, ...], chunk) -> dict | None:
        """按完整标题路径解析章节；缺失的条款/记录层级生成稳定虚拟章节。"""
        if not path:
            return None
        if path in section_by_heading_path:
            return section_by_heading_path[path]
        parent = _resolve_section(path[:-1], chunk)
        heading = str(path[-1]).strip()
        if not heading:
            return parent
        section_type = "clause" if _is_legal_clause(heading) else "section"
        if str((chunk.metadata or {}).get("chunk_type") or "") == "faq":
            section_type = "faq_group"
        parent_id = str((parent or {}).get("section_id") or "")
        level = int((parent or {}).get("heading_level") or 0) + 1
        metadata = chunk.metadata or {}
        spans = _as_list(metadata.get("source_spans_json"))
        locators = _as_list(metadata.get("source_locators_json"))
        page_numbers: list[int] = []
        for value in str(metadata.get("pages") or "").split(","):
            try:
                page_numbers.append(int(value))
            except (TypeError, ValueError):
                continue
        section = {
            "section_id": _new_section_id(list(path), parent_id),
            "parent_section_id": parent_id,
            "heading": heading,
            "heading_level": level,
            "heading_path": list(path),
            "section_order": len(tree),
            "page_start": min(page_numbers) if page_numbers else None,
            "page_end": max(page_numbers) if page_numbers else None,
            "section_type": section_type,
            "source_span": spans[0] if spans and isinstance(spans[0], dict) else None,
            "source_locator": locators[0] if locators and isinstance(locators[0], dict) else {},
            "derived_from": "chunk_path",
        }
        tree.append(section)
        section_by_heading_path[path] = section
        section_by_id[section["section_id"]] = section
        ast_section_ids.add(section["section_id"])
        derived_sections.append(section)
        return section

    def _norm(value: str) -> str:
        return " ".join(str(value or "").split()).casefold()

    table_nodes = [
        node for node in walk(ast.root)
        if node.type == "table" and node.source_locator
    ]

    for chunk in chunks:
        metadata = getattr(chunk, "metadata", None)
        content = str(getattr(chunk, "page_content", "") or "")
        if not isinstance(metadata, dict):
            continue
        original_section_id = str(metadata.get("section_id") or "")
        raw_path = metadata.get("section_path") or []
        if isinstance(raw_path, str):
            try:
                raw_path = json.loads(raw_path)
            except (TypeError, ValueError):
                raw_path = [raw_path] if raw_path else []
        path = tuple(str(part) for part in raw_path if str(part))
        heading_path = list(path)
        document_title = str(getattr(ast, "document_title", "") or "").strip()
        if document_title and (
            not heading_path or heading_path[0] != document_title
        ):
            heading_path.insert(0, document_title)
        if heading_path:
            metadata["heading_path_json"] = json.dumps(
                heading_path, ensure_ascii=False, separators=(",", ":"),
            )
        candidates = sections_by_path.get(path, []) if path else []
        if not candidates and metadata.get("section_title"):
            title = str(metadata["section_title"])
            candidates = [
                section for entries in sections_by_path.values()
                for section in entries if section.text.strip() == title
            ]

        if len(candidates) > 1:
            evidence = _norm(content[:240])
            matching = []
            for section in candidates:
                section_text = " ".join(
                    node.text for node in walk(section) if node.text
                )
                if evidence and evidence in _norm(section_text):
                    matching.append(section)
            if len(matching) == 1:
                candidates = matching

        if len(candidates) == 1:
            section = candidates[0]
            metadata["section_id"] = section.section_id
            metadata["parent_section_id"] = section.parent_section_id
            metadata["section_type"] = (
                section.source_locator.get("section_type")
                or infer_section_type(section)
            )
        else:
            section = None

        section_id = str(metadata.get("section_id") or "")
        if not section_id or section_id not in ast_section_ids:
            resolved = _resolve_section(tuple(heading_path or path), chunk)
            if resolved is not None:
                metadata["section_id"] = resolved["section_id"]
                metadata["parent_section_id"] = resolved["parent_section_id"]
                metadata["section_type"] = resolved["section_type"]
        else:
            resolved = section_by_id.get(section_id)
            if resolved is not None and resolved.get("section_type"):
                metadata["section_type"] = resolved["section_type"]

        if str(metadata.get("chunk_type") or "") == "faq" and metadata.get(
            "section_id",
        ):
            metadata["section_type"] = "faq_group"
            faq_section = section_by_id.get(str(metadata["section_id"]))
            if faq_section is not None and faq_section.get(
                "section_type",
            ) == "section":
                faq_section["section_type"] = "faq_group"

        final_section_id = str(metadata.get("section_id") or "")
        if (
            original_section_id
            and final_section_id
            and original_section_id != final_section_id
        ):
            # 合同策略内部的 clause_N 会随路径解析替换为稳定章节 ID；
            # 同步改写表格结构/引用 JSON，避免顶层与嵌套身份不一致。
            _apply_section_id_remap(
                metadata, original_section_id, final_section_id,
            )

        table_node = None
        chunk_type = str(metadata.get("chunk_type") or "")
        if chunk_type.startswith("table_"):
            wanted_node_id = str(metadata.get("table_node_id") or "")
            if wanted_node_id:
                table_node = next(
                    (node for node in table_nodes if node.node_id == wanted_node_id),
                    None,
                )
            if table_node is None:
                scoped = [
                    node for node in table_nodes
                    if not metadata.get("section_id")
                    or node.section_id == metadata.get("section_id")
                ]
                # 没有稳定表格身份时，仅当该章节只有一张表才可安全回退；
                # 多表章节宁可留空，也不把引用指到错误的表。
                if len(scoped) == 1:
                    table_node = scoped[0]

        if table_node is not None:
            locator = dict(table_node.source_locator)
            for key in ("row_index", "row_start", "row_end"):
                row_value = metadata.get(key)
                if row_value is None:
                    continue
                try:
                    locator[key] = int(row_value)
                except (TypeError, ValueError):
                    locator[key] = str(row_value)[:32]
            if table_node.rows:
                locator["columns"] = [
                    str(value) for value in table_node.rows[0]
                ][:64]
            locator["table_id"] = metadata.get("table_id", "")
            metadata["source_locators_json"] = json.dumps(
                [locator], ensure_ascii=False, separators=(",", ":"),
            )[:4096]
            metadata["source_node_ids_json"] = json.dumps(
                [table_node.node_id], ensure_ascii=False, separators=(",", ":"),
            )
            start, end = table_node.source_range
            if end > start:
                metadata["source_spans_json"] = json.dumps(
                    [{"start": start, "end": end, "unit": "char"}],
                    ensure_ascii=False, separators=(",", ":"),
                )[:4096]
        else:
            source_nodes: list[DocumentNode] = []
            normalized_content = _norm(content)
            for node in walk(ast.root):
                if node.type not in LEAF_TYPES or not node.source_locator:
                    continue
                if node.section_id and metadata.get("section_id") and (
                    node.section_id != metadata.get("section_id")
                ):
                    node_text = _norm(node.text)
                    if not (node_text and node_text in normalized_content):
                        continue
                node_text = _norm(node.text)
                if not node_text:
                    continue
                core = node_text[:160]
                if node_text in normalized_content or (
                    len(core) >= 40 and core in normalized_content
                ) or (
                    len(normalized_content) >= 40
                    and normalized_content[:160] in node_text
                ):
                    source_nodes.append(node)

            if source_nodes:
                spans = []
                locators = []
                node_ids = []
                for node in source_nodes[:16]:
                    node_ids.append(node.node_id)
                    start, end = node.source_range
                    if end > start:
                        spans.append({"start": start, "end": end, "unit": "char"})
                    if node.source_locator:
                        locators.append(node.source_locator)
                metadata["source_node_ids_json"] = json.dumps(
                    node_ids, ensure_ascii=False, separators=(",", ":"),
                )
                if spans:
                    metadata["source_spans_json"] = json.dumps(
                        spans[:16], ensure_ascii=False, separators=(",", ":"),
                    )[:4096]
                if locators:
                    metadata["source_locators_json"] = json.dumps(
                        locators[:16], ensure_ascii=False, separators=(",", ":"),
                    )[:4096]
        metadata["content_hash"] = hashlib.sha256(
            content.encode("utf-8"),
        ).hexdigest()

    if derived_sections:
        # section_tree() 返回的是快照副本；写回缓存，让质量记录看到
        # 由 chunk 路径派生出的条款/记录章节。
        ast._section_tree = tree


def _namespace_content_unit_chunks(
    chunks: list[Document], file_path: str, unit_scope: str,
    *, set_content_unit_id: bool = True,
) -> None:
    """给局部策略输出加内容单元命名空间，保持 Chunk/Parent/表格 ID 唯一。

    局部策略会从每个 AST 子树重新编号章节和表格。相同章节名、表头和行内容
    在两个内容单元中可生成相同哈希 ID，导致索引覆盖与 Parent 引用审计失败。
    命名空间基于稳定的单元顺序和标题，并同步改写关联 ID。
    """
    if not chunks:
        return

    scope_id = hashlib.md5(
        f"{file_path}:content-unit:{unit_scope}".encode("utf-8"),
    ).hexdigest()[:10]
    old_chunk_ids = [
        str((chunk.metadata or {}).get("chunk_id") or "").strip()
        for chunk in chunks
    ]
    old_id_counts: dict[str, int] = {}
    for old_id in old_chunk_ids:
        if old_id:
            old_id_counts[old_id] = old_id_counts.get(old_id, 0) + 1
    occurrences: dict[str, int] = {}
    new_chunk_ids: list[str] = []
    for old_id in old_chunk_ids:
        if not old_id:
            new_chunk_ids.append("")
            continue
        occurrence = occurrences.get(old_id, 0)
        occurrences[old_id] = occurrence + 1
        new_chunk_ids.append(hashlib.md5(
            f"{scope_id}:chunk:{old_id}:{occurrence}".encode("utf-8"),
        ).hexdigest()[:12])

    parents_by_id: dict[str, list[tuple[int, str, tuple[str, ...]]]] = {}
    for index, (chunk, old_id, new_id) in enumerate(
        zip(chunks, old_chunk_ids, new_chunk_ids),
    ):
        metadata = chunk.metadata or {}
        if old_id and metadata.get("granularity") == "parent":
            path = tuple(str(part) for part in (metadata.get("section_path") or []))
            parents_by_id.setdefault(old_id, []).append((index, new_id, path))

    def _resolve_parent(old_id: str, index: int, path: tuple[str, ...]) -> str:
        candidates = parents_by_id.get(old_id, [])
        if not candidates:
            # 不抹掉原有悬空引用；让完整性门禁继续报告它。
            return old_id
        same_path = [candidate for candidate in candidates if candidate[2] == path]
        pool = same_path or candidates
        preceding = [candidate for candidate in pool if candidate[0] <= index]
        return (preceding[-1] if preceding else pool[0])[1]

    def _table_id(old_id: str, index: int, path: tuple[str, ...]) -> str:
        if parents_by_id.get(old_id):
            return _resolve_parent(old_id, index, path)
        return hashlib.md5(
            f"{scope_id}:table:{old_id}".encode("utf-8"),
        ).hexdigest()[:12]

    def _remap_table_structure(value, index: int, path: tuple[str, ...]):
        if isinstance(value, list):
            return [_remap_table_structure(item, index, path) for item in value]
        if isinstance(value, dict):
            return {
                key: (
                    _table_id(str(item), index, path)
                    if key == "table_id" and item is not None
                    else _remap_table_structure(item, index, path)
                )
                for key, item in value.items()
            }
        return value

    for index, (chunk, old_id, new_id) in enumerate(
        zip(chunks, old_chunk_ids, new_chunk_ids),
    ):
        metadata = chunk.metadata
        path = tuple(str(part) for part in (metadata.get("section_path") or []))
        if old_id:
            metadata["chunk_id"] = new_id
            if old_id_counts.get(old_id, 0) > 1:
                metadata["chunk_id_collision_resolved"] = "true"
        old_parent_id = str(metadata.get("parent_chunk_id") or "").strip()
        if old_parent_id:
            metadata["parent_chunk_id"] = _resolve_parent(
                old_parent_id, index, path,
            )
        if metadata.get("table_id") is not None:
            metadata["table_id"] = _table_id(
                str(metadata["table_id"]), index, path,
            )
        if isinstance(metadata.get("table_ids"), list):
            metadata["table_ids"] = [
                _table_id(str(table_id), index, path)
                for table_id in metadata["table_ids"]
            ]
        for key in ("references", "table_structures_json", "sections_json"):
            value = metadata.get(key)
            if isinstance(value, str) and key.endswith("_json"):
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    continue
                metadata[key] = json.dumps(
                    _remap_table_structure(parsed, index, path),
                    ensure_ascii=False,
                )
            elif isinstance(value, (list, dict)):
                metadata[key] = _remap_table_structure(value, index, path)
        if set_content_unit_id:
            metadata["content_unit_id"] = scope_id


def ensure_unique_chunk_ids(chunks: list[Document], file_path: str) -> bool:
    """修复局部策略内部的重复 ID，同时保持 Parent 与表格引用指向正确对象。"""
    ids = [
        str((chunk.metadata or {}).get("chunk_id") or "").strip()
        for chunk in chunks
    ]
    present = [chunk_id for chunk_id in ids if chunk_id]
    if len(present) == len(set(present)):
        return False
    _namespace_content_unit_chunks(
        chunks, file_path, "duplicate-id-repair", set_content_unit_id=False,
    )
    return True
