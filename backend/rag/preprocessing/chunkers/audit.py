"""audit.py — 源节点与 Leaf Chunk 覆盖对账审计器（coverage_audit）。

通过双向文本包含、切片重组与表格逐行逐单元格核对，定位切分后静默丢失的内容缺口，
支持超大文档采样保护与 PII 脱敏归一化对账。
"""
from __future__ import annotations

import json
import math
import re
from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.chunkers.strategies.legal import LegalChunkStrategy
from backend.rag.preprocessing.chunkers.table_handler import (
    CHUNK_TYPE_TABLE_HEADER, CHUNK_TYPE_TABLE_ROW,
)
from backend.rag.preprocessing.redaction import mask_sensitive_text
from backend.shared.logger import logger


def coverage_audit(ast: DocumentAST, chunks: List[Document],
                   max_audit_nodes: int = 2000,
                   normalize_pii: bool = False,
                   excluded_texts: List[str] | None = None) -> dict:
    """源节点 → 输出 Leaf Chunk 覆盖对账（P0-02，2026-10-13）。

    策略产出的 chunk 应覆盖 AST 全部可索引叶子；本审计用双向文本包含
    匹配（与 stamp_chunk_pages 同口径）找出未覆盖叶子：
      - 叶子前缀 ⊆ 某 chunk 文本 → 被合并覆盖
      - 某 chunk 核心 ⊆ 叶子全文 → 被切片覆盖
      - 两者皆否 → uncovered（策略静默丢内容的信号，进入 quality_issues）

    超大文档（叶子或 chunk 超 max_audit_nodes）抽样审计并标 sampled=true，
    防止 O(n²) 子串匹配拖垮索引主流程。审计失败不抛异常——返回
    {"error": ...}，安全网自身不能成为新的失败点。
    """
    def _normalize(text: str, mask_pii: bool = False) -> str:
        value = str(text or "")
        if mask_pii:
            # 只为实际标记脱敏的 Leaf 生成对照文本；未脱敏 Leaf 必须与原文
            # 对账，不能因其他 Leaf 脱敏而把它缺失的数字误当成已覆盖。
            from backend.rag.preprocessing.filter import ChunkFilter

            value = ChunkFilter.apply_pii_mask(value)
        return " ".join(value.split()).casefold()

    def _windows(text: str) -> list[str]:
        if len(text) <= 120:
            return [text] if text else []
        stride = 80
        windows = [text[i:i + 120] for i in range(0, len(text), stride)]
        if windows and len(windows[-1]) < 40:
            windows[-1] = text[-120:]
        return windows

    try:
        source_leaves = [
            n for n in walk(ast.root)
            if n.type in LEAF_TYPES and (
                (n.text or "").strip()
                or (n.type == "table" and any(
                    str(cell).strip()
                    for row in (n.rows or []) for cell in row
                    if cell is not None
                ))
            )
        ]
        # Parent 只供上下文扩展，绝不能计入可索引内容覆盖。
        bodies = [
            (
                _normalize(
                    c.page_content,
                    mask_pii=bool(normalize_pii and (
                        getattr(c, "metadata", {}) or {}
                    ).get("pii_masked")),
                ),
                bool(normalize_pii and (
                    getattr(c, "metadata", {}) or {}
                ).get("pii_masked")),
            )
            for c in chunks
            if (getattr(c, "page_content", None)
                and getattr(c, "metadata", {}).get("granularity") == "leaf")
        ]
        split_entries: dict[
            tuple[bool, str], list[tuple[int, int, str]]
        ] = {}
        for chunk in chunks:
            metadata = getattr(chunk, "metadata", {}) or {}
            group_id = metadata.get("split_group_id")
            part_index = metadata.get("split_part")
            part_total = metadata.get("split_total")
            if (
                metadata.get("granularity") != "leaf"
                or not group_id
                or not isinstance(part_index, int)
                or not isinstance(part_total, int)
                or part_total <= 1
                or not getattr(chunk, "page_content", None)
            ):
                continue
            is_masked = bool(normalize_pii and metadata.get("pii_masked"))
            split_entries.setdefault((is_masked, str(group_id)), []).append(
                (part_index, part_total, chunk.page_content)
            )
        # P2 收口（2026-10-19）：被 ChunkFilter 显式规则过滤（low_chinese_ratio
        # 等，reason 已入 filtered_details）的 chunk 是合法排除而非静默丢失；
        # Structure 粒度下此类叶子独立成 chunk，v3 路由变更后暴露为假
        # coverage_gap。纳入对照集豁免，过滤面仍由 filtered_count/reasons 观测。
        for _excluded in (excluded_texts or []):
            _norm = _normalize(_excluded)
            if _norm:
                bodies.append((_norm, False))
        split_bodies: dict[bool, list[str]] = {False: [], True: []}
        for (is_masked, _), entries in split_entries.items():
            totals = {part_total for _, part_total, _ in entries}
            if len(totals) != 1:
                continue
            part_total = next(iter(totals))
            if (
                len(entries) != part_total
                or sorted(index for index, _, _ in entries)
                != list(range(part_total))
            ):
                continue
            combined = "".join(
                content for _, _, content in sorted(entries)
            )
            split_bodies[is_masked].append(
                _normalize(combined, mask_pii=is_masked)
            )
        row_leaf_bodies: dict[tuple[str, int, str, bool], list[str]] = {}
        for chunk in chunks:
            metadata = getattr(chunk, "metadata", {}) or {}
            if (metadata.get("granularity") != "leaf"
                    or metadata.get("table_id") is None
                    or not getattr(chunk, "page_content", None)):
                continue
            is_masked = bool(normalize_pii and metadata.get("pii_masked"))
            table_id = str(metadata["table_id"])
            row_index = metadata.get("row_index")
            if (
                metadata.get("chunk_type") in (
                    CHUNK_TYPE_TABLE_ROW, CHUNK_TYPE_TABLE_HEADER,
                )
                and row_index is not None
            ):
                row_leaf_bodies.setdefault(
                    (table_id, int(row_index),
                     str(metadata.get("chunk_type")), is_masked),
                    [],
                ).append(_normalize(chunk.page_content, mask_pii=is_masked))
                continue

            # 条款内完整表格/长表行组在 metadata 中保留原始单元格结构；
            # 用它建立逐行覆盖证据，无需为每行创建独立向量 Leaf。
            try:
                structured_rows = json.loads(
                    metadata.get("table_rows_json") or "[]",
                )
            except (TypeError, ValueError):
                structured_rows = []
            if not isinstance(structured_rows, list) or not structured_rows:
                continue
            from backend.rag.preprocessing.parser._table_nl import (
                detect_multi_level_header,
            )

            header_count, _, _ = detect_multi_level_header(structured_rows)
            header_count = min(max(int(header_count), 0), len(structured_rows))
            row_start = int(metadata.get("row_start") or 1)
            for index, _row in enumerate(structured_rows):
                source_index = (
                    index if index < header_count
                    else row_start + index - header_count
                )
                row_key = (table_id, source_index, CHUNK_TYPE_TABLE_ROW, is_masked)
                row_leaf_bodies.setdefault(row_key, []).append(
                    _normalize(chunk.page_content, mask_pii=is_masked)
                )
        row_bodies = [
            ("".join(parts), key[3])
            for key, parts in row_leaf_bodies.items()
        ]

        # 只有存在已关联表格 Leaf 的条款，才允许覆盖审计忽略其原文中的
        # 引表技术标记；没有对应表格时，该标记仍按普通源文本核验。
        associated_table_sections = {
            str(metadata.get("section_id"))
            for chunk in chunks
            for metadata in [getattr(chunk, "metadata", {}) or {}]
            if metadata.get("granularity") == "leaf"
            and metadata.get("table_id")
            and metadata.get("section_id")
            and metadata.get("chunk_type") in {
                "table_inline", "table_row_group",
            }
        }
        associated_table_titles = {
            str(metadata.get("section_title") or "").strip()
            for chunk in chunks
            for metadata in [getattr(chunk, "metadata", {}) or {}]
            if metadata.get("granularity") == "leaf"
            and metadata.get("table_id")
            and metadata.get("chunk_type") in {
                "table_inline", "table_row_group",
            }
        }
        normalized_marker_leaf_ids: set[int] = set()
        if associated_table_sections or associated_table_titles:
            for clause_index, clause in enumerate(
                LegalChunkStrategy._clause_sections(ast), start=1,
            ):
                clause_title = str(clause.text or "").strip()
                if not (
                    f"clause_{clause_index}" in associated_table_sections
                    or str(clause.section_id or "") in associated_table_sections
                    or clause_title in associated_table_titles
                ):
                    continue
                normalized_marker_leaf_ids.update(
                    id(node) for node in walk(clause)
                    if node.type in LEAF_TYPES
                    and "SENTINEL-TABLE-REF" in str(node.text or "")
                )

        max_audit_nodes = max(1, int(max_audit_nodes))
        sampled = len(source_leaves) > max_audit_nodes or len(bodies) > max_audit_nodes
        if len(source_leaves) > max_audit_nodes:
            stride = max(1, math.ceil(len(source_leaves) / max_audit_nodes))
            leaves = source_leaves[::stride][:max_audit_nodes]
        else:
            leaves = source_leaves

        def _suffix_prefix_overlap(left: str, right: str) -> int:
            """返回左串后缀与右串前缀的最长重合长度。"""
            limit = min(len(left), len(right))
            if not limit:
                return 0
            pattern = right[:limit]
            sequence = pattern + "\0" + left[-limit:]
            prefix = [0] * len(sequence)
            for index in range(1, len(sequence)):
                matched = prefix[index - 1]
                while matched and sequence[index] != sequence[matched]:
                    matched = prefix[matched - 1]
                if sequence[index] == sequence[matched]:
                    matched += 1
                prefix[index] = matched
            return min(prefix[-1], limit)

        boundary_bodies: dict[bool, list[str]] = {False: [], True: []}
        max_body_length = max((len(body) for body, _ in bodies), default=120)
        previous_masked: bool | None = None
        stitched_tail = ""
        for body, is_masked in bodies:
            if is_masked != previous_masked:
                stitched_tail = body
                previous_masked = is_masked
                continue
            overlap = _suffix_prefix_overlap(stitched_tail, body)
            boundary_bodies[is_masked].append(
                stitched_tail[-120:] + body[overlap:overlap + 120]
            )
            stitched_tail = (
                stitched_tail + body[overlap:]
            )[-max_body_length:]

        def _covered_text(text: str) -> bool:
            raw_probes = _windows(_normalize(text))
            masked_probes = (
                _windows(_normalize(text, mask_pii=True))
                if normalize_pii else []
            )
            if not raw_probes:
                return False

            def _all_probes_covered(probes: list[str], masked: bool) -> bool:
                candidates = [
                    body for body, is_masked in bodies if is_masked == masked
                ]
                candidates.extend(boundary_bodies[masked])
                candidates.extend(split_bodies[masked])
                return all(
                    any(probe in body for body in candidates)
                    for probe in probes
                )

            raw_covered = _all_probes_covered(raw_probes, masked=False)
            masked_covered = bool(masked_probes) and _all_probes_covered(
                masked_probes, masked=True,
            )
            return raw_covered or masked_covered

        covered = 0
        uncovered: list[dict] = []
        table_rows_total = 0
        table_rows_uncovered = 0
        table_cells_total = 0
        table_cells_uncovered = 0

        for node in leaves:
            text = (node.text or "").strip()
            if id(node) in normalized_marker_leaf_ids:
                text = re.sub(
                    r"(?<![\w-])SENTINEL-TABLE-REF(?![\w-])", "", text,
                ).strip()
            rows = getattr(node, "rows", None) if node.type == "table" else None
            if not rows:
                hit = _covered_text(text)
                if hit:
                    covered += 1
                else:
                    uncovered.append({
                        "type": node.type,
                        "preview": mask_sensitive_text(text[:80]),
                    })
                continue

            # 行级和单元格级核对；双层财务切分会改写表格文本为 kv，
            # 但保留每个非空单元格，故 Parent/表级摘要不能代替行数据。
            source_row_counts: dict[tuple[str, ...], int] = {}
            row_cells_by_signature: dict[tuple[str, ...], list[str]] = {}
            for row in rows:
                cells = [
                    str(cell) for cell in row
                    if cell is not None and str(cell).strip()
                ]
                if not cells:
                    continue
                signature = tuple(_normalize(cell) for cell in cells)
                source_row_counts[signature] = source_row_counts.get(signature, 0) + 1
                row_cells_by_signature[signature] = cells
                table_rows_total += 1
                table_cells_total += len(cells)

            missing_rows = 0
            missing_cells = 0
            missing_row_samples: list[dict] = []
            for signature, required_count in source_row_counts.items():
                raw_cells = row_cells_by_signature[signature]
                # 每个带源行身份的 Leaf 只计一次；不能把同一个 Leaf 同时
                # 从原始 bodies 与拼接行 bodies 加入两次，掩盖重复源行缺失。
                matching_rows = [
                    (body, body_is_masked)
                    for body, body_is_masked in row_bodies
                    if all(
                        _normalize(cell, mask_pii=body_is_masked) in body
                        for cell in raw_cells
                    )
                ]
                if matching_rows:
                    matching_bodies = len(matching_rows)
                else:
                    # 无源行身份的 fallback 表格（例如仅有一条数据的 Sheet）
                    # 仍按实际输出 Leaf 检查，不能被其他表格的行块遮蔽。
                    table_bodies = [
                        body for body, body_is_masked in bodies
                        if not body_is_masked
                    ]
                    matching_bodies = sum(
                        1 for body in table_bodies
                        if all(
                            _normalize(cell) in body for cell in raw_cells
                        )
                    )
                if matching_bodies < required_count:
                    missing_count = required_count - matching_bodies
                    missing_rows += missing_count
                    if len(missing_row_samples) < 3:
                        missing_row_samples.append({
                            "cells": [
                                mask_sensitive_text(cell, limit=80)
                                for cell in raw_cells[:8]
                            ],
                            "missing_count": missing_count,
                        })
                for cell in raw_cells:
                    if not any(
                        _normalize(cell, mask_pii=body_is_masked) in body
                        for body, body_is_masked in bodies
                    ) and not any(
                        _normalize(cell, mask_pii=body_is_masked) in body
                        for body, body_is_masked in row_bodies
                    ):
                        missing_cells += 1

            table_rows_uncovered += missing_rows
            table_cells_uncovered += missing_cells
            if not missing_rows and not missing_cells and source_row_counts:
                covered += 1
            else:
                uncovered.append({
                    "type": node.type,
                    "preview": mask_sensitive_text(text[:80]),
                    "missing_rows": missing_rows,
                    "missing_cells": missing_cells,
                    "page": (
                        int(getattr(node, "page_number", 0) or 0)
                        or (node.source_locator or {}).get("page")
                    ),
                    "missing_row_samples": missing_row_samples,
                })

        audited_count = len(leaves)
        coverage_rate = (
            None if sampled or not source_leaves
            else covered / len(source_leaves)
        )
        return {
            "leaf_total": len(source_leaves),
            "audited_leaf_count": audited_count,
            "covered": covered,
            "uncovered_count": len(uncovered),
            "uncovered_samples": uncovered[:10],
            "table_rows_total": table_rows_total,
            "table_rows_uncovered": table_rows_uncovered,
            "table_cells_total": table_cells_total,
            "table_cells_uncovered": table_cells_uncovered,
            "coverage_rate": coverage_rate,
            "sampled": sampled,
            "pii_mask_normalized": normalize_pii,
        }
    except Exception as e:  # noqa: BLE001 — QualityGate 将错误按硬异常处理
        logger.warning(f"[CoverageAudit] 失败: {type(e).__name__}: {e}")
        return {
            "error": f"{type(e).__name__}: {e}",
            "leaf_total": 0,
            "audited_leaf_count": 0,
            "covered": 0,
            "uncovered_count": 0,
            "uncovered_samples": [],
            "coverage_rate": None,
            "sampled": False,
            "pii_mask_normalized": normalize_pii,
        }
