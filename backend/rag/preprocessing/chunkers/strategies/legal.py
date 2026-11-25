"""legal.py — 合同条款切分策略（LegalChunkStrategy）。

按「第 N 条」条款编号切分合同与法务文档，条款编号作为 chunk 边界。
包含 400+ 行完整的合同表格切分（_split_contract_with_tables），短表保留，长表按行组重复表头与上下文；
无条款编号时降级 Structure 或 Recursive 策略。
"""
from __future__ import annotations

import json
import re
from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import (
    DocumentAST, DocumentNode, LEAF_TYPES, walk,
)
from backend.rag.preprocessing.chunkers.base import (
    _LEGAL_CLAUSE_RE, _chunk_id, _doc_title, _enrich,
    _hard_split_by_budget, _is_legal_clause,
    _iter_leaf_nodes_with_section_path, _make_doc,
    _make_leaf_chunks_bounded, _section_ancestor_titles,
    count_tokens, get_leaf_chunk_tokens, get_parent_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.stamper import (
    _node_source_location, _source_location_metadata,
)
from backend.rag.preprocessing.chunkers.strategies.recursive import RecursiveChunkStrategy
from backend.rag.preprocessing.chunkers.strategies.structure import StructureChunkStrategy
from backend.rag.preprocessing.chunkers.table_handler import _attach_virtual_parents
from backend.shared.logger import logger


class LegalChunkStrategy:
    """合同条款切分（legal/contract_template）：按「第 N 条」条款编号切分。

    条款编号作为 chunk 边界，条款内容合并成一个 chunk，
    保证「一个条款一个语义单元」。
    """

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        clause_sections = self._clause_sections(ast)
        if clause_sections:
            return self._split_contract_with_tables(
                ast, file_path, clause_sections,
            )

        # 无条款降级：内容找不到「第 N 条」边界时，旧实现会把全文合成
        # 单一巨型 chunk（误分类为 legal 的流程/制度文档尤其受害）。
        # 有章节结构 → Structure 切分；否则 Recursive 兜底，不静默堆大 chunk。
        has_clause = any(
            n.type in LEAF_TYPES and _is_legal_clause(n.text)
            for n in walk(ast.root)
        )
        if not has_clause:
            has_sections = any(
                n.type == "section" and n.level > 0
                for n in walk(ast.root)
            )
            logger.info(
                "[LegalChunk] 无「第 N 条」条款编号 → "
                + ("Structure 结构切分降级" if has_sections else "Recursive 兜底")
            )
            if has_sections:
                return StructureChunkStrategy().split(ast, file_path)
            return RecursiveChunkStrategy().split(ast, file_path)

        chunks: List[Document] = []
        current: list[str] = []
        current_clause = ""
        current_prefix: list[str] = []
        current_path: list[str] = []

        def _flush_current() -> None:
            if not current:
                return
            chunk_texts = [*current_prefix, *current]
            chunks.extend(_make_leaf_chunks_bounded(
                chunk_texts, file_path,
                section_title=current_clause,
                section_level=len(current_path),
                section_path=current_path,
                preserve_source=True,
                chunk_type="legal_clause",
            ))
            current.clear()

        doc_title = _doc_title(ast)
        for n, ancestor_titles in _iter_leaf_nodes_with_section_path(ast):
            clause_match = _LEGAL_CLAUSE_RE.match((n.text or "").strip())
            if clause_match:
                _flush_current()
                clause_text = (n.text or "").strip()
                first_line = clause_text.splitlines()[0].strip() if clause_text else ""
                current_clause = (first_line or clause_match.group(0))[:80]
                current_prefix = [
                    part for part in (doc_title, *ancestor_titles) if part
                ]
                current_path = [*current_prefix, current_clause]
            elif not current:
                current_clause = ""
                current_prefix = [
                    part for part in (doc_title, *ancestor_titles) if part
                ]
                current_path = list(current_prefix)
            current.append(n.text)
        _flush_current()

        # C1: 每连续 N 条款挂一个虚拟 parent（默认 5，LEGAL_CLAUSES_PER_PARENT），
        # 修复 legal/contract_template leaf 无 parent_chunk_id 的检索退化
        from backend.config import LEGAL_CLAUSES_PER_PARENT
        return _enrich(
            _attach_virtual_parents(chunks, file_path, "legal",
                                    group_size=max(LEGAL_CLAUSES_PER_PARENT, 1)),
            file_path,
        )

    @staticmethod
    def _clause_sections(ast: DocumentAST) -> list[DocumentNode]:
        """按文档顺序找到条款 section，不进入已识别条款内部重复扫描。"""
        sections: list[DocumentNode] = []

        def visit(node: DocumentNode) -> None:
            for child in node.children:
                if child.type == "section" and _is_legal_clause(child.text):
                    sections.append(child)
                else:
                    visit(child)

        visit(ast.root)
        return sections

    def _split_contract_with_tables(
        self, ast: DocumentAST, file_path: str,
        clause_sections: list[DocumentNode],
    ) -> List[Document]:
        """合同表格随条款成 Leaf；短表完整保留，长表按行组重复表头。"""
        from backend.rag.preprocessing.parser._table_nl import (
            detect_multi_level_header, table_to_markdown,
        )

        doc_title = _doc_title(ast)
        records: list[dict] = []
        previous_table_id = ""
        next_table_number = 1
        leaf_tokens = get_leaf_chunk_tokens()
        parent_tokens = get_parent_chunk_tokens()

        for clause_index, section in enumerate(clause_sections, start=1):
            section_id = f"clause_{clause_index}"
            clause_title = section.text.strip()
            ancestor_titles = _section_ancestor_titles(ast, section)
            context_path = [
                part for part in (doc_title, *ancestor_titles, clause_title)
                if part
            ]
            items: list[dict] = []
            tables: list[dict] = []
            for node in walk(section):
                if node is section:
                    continue
                if node.type == "table" and node.rows:
                    table_id = f"table_{next_table_number}"
                    next_table_number += 1
                    table = {
                        "table_id": table_id,
                        "table_node_id": str(getattr(node, "node_id", "") or ""),
                        "rows": [list(row) for row in node.rows],
                        "section_id": section_id,
                        "source_location": _node_source_location(node),
                    }
                    table["text"] = table_to_markdown(table["rows"])
                    tables.append(table)
                    items.append({"kind": "table", "value": table})
                elif node.type in LEAF_TYPES and node.text.strip():
                    items.append({"kind": "text", "value": node.text.strip()})

            # 原始 DOCX 中的标记只在同条款内确有表格时才可移出索引文本；
            # AST/源文件保持不变，避免丢失上传原文证据。
            if tables:
                for item in items:
                    if item["kind"] == "text":
                        item["value"] = re.sub(
                            r"(?<![\w-])SENTINEL-TABLE-REF(?![\w-])",
                            "",
                            item["value"],
                        ).strip()

            prefix_parts = context_path
            prefix = "\n".join(prefix_parts)
            text_parts = [prefix] if prefix else []
            full_items: list[str] = []
            body_texts: list[str] = []
            for item in items:
                if item["kind"] == "table":
                    item_text = item["value"]["text"]
                else:
                    item_text = item["value"]
                    body_texts.append(item_text)
                full_items.append(item_text)
            text_parts.extend(full_items)
            clause_text = "\n".join(part for part in text_parts if part)

            references: list[dict[str, str]] = []
            combined_body = "\n".join(body_texts)
            if re.search(r"上表|上述表格|前表|前述表格", combined_body):
                if previous_table_id:
                    references.append({
                        "table_id": previous_table_id,
                        "relation": "refers_to",
                    })
            if tables:
                previous_table_id = tables[-1]["table_id"]

            records.append({
                "section_id": section_id,
                "section_title": clause_title,
                "section_path": context_path,
                "prefix": prefix,
                "body_text": "\n".join(body_texts),
                "items": items,
                "text": clause_text,
                "tables": tables,
                "references": references,
            })

        leaves: list[Document] = []
        for record in records:
            leaf_base = {
                "granularity": "leaf",
                "parent_chunk_id": "",
                "section_id": record["section_id"],
                "section_path": record["section_path"],
                "section_title": record["section_title"],
                "document_title": doc_title,
                "section_level": 1,
            }
            tables = record["tables"]
            table_source_location_meta = _source_location_metadata([
                table.get("source_location") for table in tables
            ])
            table_ids = [table["table_id"] for table in tables]
            references = record["references"]
            if count_tokens(record["text"]) <= leaf_tokens:
                metadata = {
                    **leaf_base,
                    "chunk_id": _chunk_id(
                        file_path, f"legal:{record['section_id']}", record["text"],
                    ),
                    "chunk_type": "table_inline" if tables else "legal_clause",
                    "chunk_tokens": count_tokens(record["text"]),
                    **table_source_location_meta,
                }
                if len(table_ids) == 1:
                    metadata["table_id"] = table_ids[0]
                    metadata["table_node_id"] = tables[0].get(
                        "table_node_id", "",
                    )
                    metadata["table_rows_json"] = json.dumps(
                        tables[0]["rows"], ensure_ascii=False,
                    )
                elif tables:
                    metadata["table_ids"] = table_ids
                    metadata["table_structures_json"] = json.dumps(
                        tables, ensure_ascii=False,
                    )
                if references:
                    metadata["references"] = references
                leaves.append(_make_doc(record["text"], metadata))
                continue

            body_emitted = False
            for item in record["items"]:
                if item["kind"] == "text":
                    text = "\n".join(
                        part for part in (record["prefix"], item["value"]) if part
                    )
                    for part_index, part in enumerate(_hard_split_by_budget(
                        text, leaf_tokens,
                    )):
                        metadata = {
                            **leaf_base,
                            "chunk_id": _chunk_id(
                                file_path,
                                f"legal:{record['section_id']}:text:{part_index}",
                                part,
                            ),
                            "chunk_type": "legal_clause",
                            "chunk_tokens": count_tokens(part),
                        }
                        if references:
                            metadata["references"] = references
                        leaves.append(_make_doc(part, metadata))
                    body_emitted = True
                    continue

                table = item["value"]
                header_count, header_rows, data_rows = detect_multi_level_header(
                    table["rows"],
                )
                if not header_rows:
                    header_rows = table["rows"][:1]
                    data_rows = table["rows"][1:]
                    header_count = len(header_rows)
                row_groups: list[tuple[int, int, list[list[str]]]] = []
                current_rows: list[list[str]] = []
                current_start = 0
                for data_index, row in enumerate(data_rows, start=1):
                    candidate = current_rows + [row]
                    candidate_rows = header_rows + candidate
                    section_context = (
                        record["prefix"]
                        if not body_emitted and not row_groups
                        else "\n".join(
                            part for part in (doc_title, record["section_title"])
                            if part
                        )
                    )
                    candidate_text = "\n".join(part for part in (
                        section_context,
                        record["body_text"] if not body_emitted and not row_groups else "",
                        table_to_markdown(candidate_rows),
                    ) if part)
                    if current_rows and count_tokens(candidate_text) > leaf_tokens:
                        row_groups.append((
                            current_start + 1,
                            data_index - 1,
                            current_rows,
                        ))
                        current_rows = [row]
                        current_start = data_index - 1
                    else:
                        if not current_rows:
                            current_start = data_index - 1
                        current_rows = candidate
                if current_rows:
                    row_groups.append((
                        current_start + 1,
                        len(data_rows),
                        current_rows,
                    ))
                if not data_rows:
                    row_groups.append((0, 0, []))

                for group_index, (row_start, row_end, rows_group) in enumerate(row_groups):
                    section_context = (
                        record["prefix"]
                        if not body_emitted and group_index == 0
                        else "\n".join(
                            part for part in (doc_title, record["section_title"])
                            if part
                        )
                    )
                    text_parts = [section_context]
                    if not body_emitted and group_index == 0 and record["body_text"]:
                        text_parts.append(record["body_text"])
                    text_parts.append(table_to_markdown(
                        header_rows + rows_group,
                    ))
                    chunk_text = "\n".join(part for part in text_parts if part)
                    if count_tokens(chunk_text) > leaf_tokens:
                        # 单行本身超过预算时，把该行按“列名：值”文本切开，
                        # 每片重复合同上下文与表头；完整单元格仍保存在 JSON。
                        headers = [
                            " / ".join(
                                str(header_row[col]).strip()
                                for header_row in header_rows
                                if col < len(header_row)
                                and str(header_row[col]).strip()
                            )
                            for col in range(max(
                                (len(row) for row in rows_group), default=0,
                            ))
                        ]
                        header_text = "表头：" + " | ".join(headers)
                        prefix = "\n".join(
                            part for part in (section_context, header_text)
                            if part
                        )
                        row_facts = [
                            f"{headers[col] if col < len(headers) else f'列{col + 1}'}：{value}"
                            for row in rows_group
                            for col, value in enumerate(row)
                            if str(value).strip()
                        ]
                        row_text = "；".join(row_facts)
                        available_tokens = max(
                            1,
                            leaf_tokens - count_tokens(prefix) - 5,
                        )
                        row_parts = _hard_split_by_budget(
                            row_text, available_tokens,
                        )
                        while (
                            available_tokens > 1
                            and any(
                                count_tokens(f"{prefix}\n{part}")
                                > leaf_tokens
                                for part in row_parts
                            )
                        ):
                            available_tokens -= 1
                            row_parts = _hard_split_by_budget(
                                row_text, available_tokens,
                            )
                        part_texts = [
                            f"{prefix}\n{part}" for part in row_parts
                        ]
                    else:
                        part_texts = [chunk_text]

                    for part_index, part_text in enumerate(part_texts):
                        metadata = {
                            **leaf_base,
                            "chunk_id": _chunk_id(
                                file_path,
                                f"legal:{record['section_id']}:{table['table_id']}:{row_start}:{row_end}:{part_index}",
                                part_text,
                            ),
                            "chunk_type": "table_row_group",
                            "table_id": table["table_id"],
                            "table_node_id": table.get("table_node_id", ""),
                            "row_start": row_start,
                            "row_end": row_end,
                            "table_rows_json": json.dumps(
                                header_rows + rows_group, ensure_ascii=False,
                            ),
                            "chunk_tokens": count_tokens(part_text),
                            **_source_location_metadata([
                                table.get("source_location"),
                            ]),
                        }
                        if len(part_texts) > 1:
                            metadata["row_part_index"] = part_index
                            metadata["row_part_count"] = len(part_texts)
                        if references:
                            metadata["references"] = references
                        leaves.append(_make_doc(part_text, metadata))
                body_emitted = True

        if not leaves:
            return []

        # Parent 只保存完整合同上下文，标记为不参与 Chunk 向量检索。
        # 长合同按 Parent 预算分组，Leaf 仍只绑定其对应上下文组。
        parent_groups: list[list[Document]] = []
        current_group: list[Document] = []
        for leaf in leaves:
            candidate = current_group + [leaf]
            candidate_text = "\n\n".join(
                part for part in ([doc_title] + [c.page_content for c in candidate])
                if part
            )
            if current_group and count_tokens(candidate_text) > parent_tokens:
                parent_groups.append(current_group)
                current_group = [leaf]
            else:
                current_group = candidate
        if current_group:
            parent_groups.append(current_group)

        output: list[Document] = []
        for group_index, group in enumerate(parent_groups):
            group_section_ids = {
                str(leaf.metadata.get("section_id") or "")
                for leaf in group
            }
            group_records = [
                record for record in records
                if record["section_id"] in group_section_ids
            ]
            section_titles = list(dict.fromkeys(
                str(leaf.metadata.get("section_title") or "").strip()
                for leaf in group
                if str(leaf.metadata.get("section_title") or "").strip()
            ))
            covers_document = (
                len(parent_groups) == 1 and len(group) == len(leaves)
            )
            parent_scope = "document" if covers_document else "section"
            parent_section_title = (
                section_titles[0]
                if parent_scope == "section" and len(section_titles) == 1
                else ""
            )
            parent_path = (
                [doc_title] if covers_document and doc_title
                else [part for part in (doc_title, *section_titles) if part]
            )
            table_structures = [
                table
                for record in group_records
                for table in record["tables"]
            ]
            parent_parts = [doc_title] if doc_title else []
            for leaf in group:
                content = leaf.page_content
                if doc_title and (
                    content.startswith(f"{doc_title}\n")
                    or content.startswith(f"{doc_title}\r\n")
                ):
                    content = content[len(doc_title):].lstrip("\r\n")
                if content:
                    parent_parts.append(content)
            parent_text = "\n\n".join(parent_parts)
            parent_id = _chunk_id(
                file_path, f"legal_contract_parent:{group_index}", parent_text,
            )
            output.append(_make_doc(parent_text, {
                "granularity": "parent",
                "chunk_id": parent_id,
                "parent_chunk_id": "",
                "chunk_type": "contract_context",
                "parent_scope": parent_scope,
                "vectorize": False,
                "section_path": parent_path,
                "section_title": parent_section_title,
                "section_titles": (
                    section_titles if parent_scope == "section" else []
                ),
                "document_title": doc_title,
                "section_level": 1 if parent_scope == "section" else 0,
                "chunk_tokens": count_tokens(parent_text),
                "table_structures_json": json.dumps(
                    table_structures, ensure_ascii=False,
                ),
                "sections_json": json.dumps([
                    {
                        "section_id": record["section_id"],
                        "section_title": record["section_title"],
                        "references": record["references"],
                    }
                    for record in group_records
                ], ensure_ascii=False),
            }))
            for leaf in group:
                leaf.metadata["parent_chunk_id"] = parent_id
            output.extend(group)

        return _enrich(output, file_path)
