"""table_handler.py — 表格节点双层切分、表格类型标识与虚拟父节点挂载。

提供表格类 chunk 类型判定（is_table_chunk）、通用表格节点切分（_split_table_node，
包含表级摘要 Layer 1、表头 Leaf Layer 2、行级 kv Layer 3）及虚拟 Parent 挂载（_attach_virtual_parents）。
"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.chunkers.base import (
    _chunk_id, _hard_split_by_budget, _make_doc, _split_to_budget,
    count_tokens, get_leaf_chunk_tokens, get_parent_chunk_tokens,
)
from backend.rag.preprocessing.chunkers.stamper import (
    _node_source_location, _source_location_metadata,
)
from backend.rag.preprocessing.financial_normalizer import extract_numeric_cells
from backend.rag.preprocessing.parser._table_nl import (
    build_table_summary, detect_multi_level_header, normalize_table_rows,
    row_to_kv, table_to_csv,
)

# ── 表格类 chunk 的类型标识（唯一一份）────────────────────────────────
CHUNK_TYPE_TABLE_ROW = "table_row"
CHUNK_TYPE_TABLE_SUMMARY = "table_summary"
CHUNK_TYPE_TABLE_FALLBACK = "table_fallback"
CHUNK_TYPE_TABLE_HEADER = "table_header"
TABLE_CHUNK_TYPES: tuple[str, ...] = (
    CHUNK_TYPE_TABLE_ROW, CHUNK_TYPE_TABLE_SUMMARY, CHUNK_TYPE_TABLE_FALLBACK,
    CHUNK_TYPE_TABLE_HEADER,
)
TABLE_CHUNK_PREFIX = "table_"


def is_table_chunk(chunk_type: str | None) -> bool:
    """是否表格类 chunk。

    判定 = 「已知三种（白名单）」或「table_ 前缀」，两者取并：
    - 前缀保证 fail-closed：将来新增表格类型（如 table_header）自动被认作表格。
      漏判会让财务表格的 PII 漏脱敏，多判只是多脱敏一次 —— 隐私方向宁可多判。
    - 白名单兜住命名约定被改的情形（table_ → tbl_），且
      tests/rag/test_chunk_type_contract.py 会在改名时变红，而不是静默放行。
    """
    if not chunk_type:
        return False
    return chunk_type in TABLE_CHUNK_TYPES or chunk_type.startswith(TABLE_CHUNK_PREFIX)


def _split_table_node(node, sec, path: list, file_path: str) -> List[Document]:
    """表格节点双层切分（C2 通用化）——表级摘要(parent) + 行级 kv(leaf)。

    原本只有 FinancialTableChunkStrategy 有此能力，policy/product_spec 等
    走 StructureChunkStrategy 的类型遇到超长表格时被 RecursiveCharacterText
    Splitter 按字符硬切、行从中间切断。本 helper 抽出该逻辑供两类策略共用：
      - Layer 1: 表级摘要(parent) — 表名/行列数/列名概述，供语义检索
      - Layer 2: 行级 kv(leaf) — 每行一个 chunk，kv 格式供精确检索，
        metadata 含 numeric_values（规范化数值）支持按数值范围过滤
    表头规范化失败 → 整表作单个 chunk 兜底（不丢内容）。

    financial 特有的 reporting_period/fiscal_year/is_latest 不在此层——
    由 FinancialTableChunkStrategy 在调用后对返回 chunks 补元数据。
    """
    leaf_tokens = get_leaf_chunk_tokens()
    section_anchor = ".".join(path)
    chunks: List[Document] = []
    source_location_meta = _source_location_metadata([
        _node_source_location(node),
    ])
    _, source_header_rows, _ = detect_multi_level_header(node.rows)
    flat_header, data_rows = normalize_table_rows(node.rows)
    if not flat_header or not data_rows:
        # 表头规范化失败 → 整表作 chunk 兜底（不丢内容）。
        # P0-05：超大表兜底不能是单一巨型 chunk——超 LEAF 预算按行分片，
        # 每片保留表格身份（chunk_type/section），行列内容不丢。
        # P0 指标（2026-10-13）：兜底分片同样挂虚拟 parent（此前 parent 空，
        # 「命中 leaf → 扩 parent」对兜底表静默退化）。
        fallback_meta = {
            "section_path": list(path),
            "section_title": sec.text if sec.level > 0 else "",
            "section_level": sec.level,
            "chunk_type": CHUNK_TYPE_TABLE_FALLBACK,
            "table_node_id": str(getattr(node, "node_id", "") or ""),
        }
        if count_tokens(node.text) <= leaf_tokens:
            chunks.append(_make_doc(node.text, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(file_path, f"leaf:{section_anchor}", node.text),
                "parent_chunk_id": "",
                "chunk_tokens": count_tokens(node.text),
                **fallback_meta,
                **source_location_meta,
            }))
            return _attach_virtual_parents(
                chunks, file_path, f"tblfb:{section_anchor}",
                parent_scope="section",
            )
        for si, sub in enumerate(_split_to_budget(
                node.text, leaf_tokens, separators=["\n", " ", ""],
                overlap=0)):
            chunks.append(_make_doc(sub, {
                "granularity": "leaf",
                "chunk_id": _chunk_id(
                    file_path, f"leaf:{section_anchor}:fb:{si}", sub),
                "parent_chunk_id": "",
                "chunk_tokens": count_tokens(sub),
                **fallback_meta,
                **source_location_meta,
            }))
        # 兜底分片挂文档内虚拟 parent（表级摘要不可用时的一致退路）
        return _attach_virtual_parents(
            chunks, file_path, f"tblfb:{section_anchor}",
            parent_scope="section")

    # Layer 1: 表级摘要(parent)
    summary = build_table_summary(node.rows, section_title=sec.text)
    parent_id = _chunk_id(file_path, f"table_parent:{section_anchor}", summary)
    chunks.append(_make_doc(summary, {
        "granularity": "parent",
        "chunk_id": parent_id,
        "table_id": parent_id,
        "table_node_id": str(getattr(node, "node_id", "") or ""),
        "parent_scope": "section",
        "section_path": list(path),
        "section_title": sec.text if sec.level > 0 else "",
        "section_level": sec.level,
        "chunk_type": CHUNK_TYPE_TABLE_SUMMARY,
        "row_count": len(data_rows),
        "col_count": len(flat_header),
        "chunk_tokens": count_tokens(summary),
        **source_location_meta,
    }))

    # Layer 2: 原始表头 Leaf 保留多级表头的每个来源行。
    for hi, header_row in enumerate(source_header_rows):
        header_text = table_to_csv([header_row])
        for si, header_part in enumerate(_hard_split_by_budget(
                header_text, leaf_tokens)):
            if not header_part.strip():
                continue
            header_id = _chunk_id(
                file_path, f"table_header:{section_anchor}:{hi}:{si}",
                header_part,
            )
            chunks.append(_make_doc(header_part, {
                "granularity": "leaf",
                "chunk_id": header_id,
                "parent_chunk_id": parent_id,
                "table_id": parent_id,
                "table_node_id": str(getattr(node, "node_id", "") or ""),
                "section_path": list(path),
                "section_title": sec.text if sec.level > 0 else "",
                "section_level": sec.level,
                "chunk_type": CHUNK_TYPE_TABLE_HEADER,
                "row_index": hi,
                "chunk_tokens": count_tokens(header_part),
                **source_location_meta,
            }))

    # Layer 3: 行级 kv + 原始行，保留自然语言召回及所有源单元格。
    for ri, row in enumerate(data_rows):
        kv_text = row_to_kv(flat_header, row, sec.text)
        raw_row_text = table_to_csv([row])
        row_text = "\n【原始行】\n".join(
            part for part in (kv_text, raw_row_text) if part
        )
        if not row_text.strip():
            continue
        row_parts = (
            _hard_split_by_budget(row_text, leaf_tokens)
            if count_tokens(row_text) > leaf_tokens
            else [row_text]
        )
        numeric_vals = extract_numeric_cells(flat_header, row)
        for part_index, row_part in enumerate(row_parts):
            leaf_id = _chunk_id(
                file_path,
                f"table_row:{section_anchor}:{ri}:{part_index}",
                row_part,
            )
            meta = {
                "granularity": "leaf",
                "chunk_id": leaf_id,
                "parent_chunk_id": parent_id,
                "table_id": parent_id,
                "table_node_id": str(getattr(node, "node_id", "") or ""),
                "section_path": list(path),
                "section_title": sec.text if sec.level > 0 else "",
                "section_level": sec.level,
                "chunk_type": CHUNK_TYPE_TABLE_ROW,
                "row_index": ri,
                "row_part_index": part_index,
                "chunk_tokens": count_tokens(row_part),
                **source_location_meta,
            }
            if numeric_vals:
                meta["numeric_values"] = numeric_vals
            chunks.append(_make_doc(row_part, meta))
    return chunks


def _attach_virtual_parents(leaf_chunks: List[Document], file_path: str,
                            anchor: str, group_size: int | None = None,
                            parent_scope: str | None = None) -> List[Document]:
    """为需要跨块上下文的无结构 Leaf 挂虚拟 Parent。

    Args:
        leaf_chunks: 策略产出的 leaf 列表（保持顺序）。
        anchor: chunk_id anchor 前缀（区分策略，避免与既有 id 撞车）。
        group_size: None = 全部 leaf 挂一个文档级 parent（Step 等策略）；
            N = 每 N 个 leaf 一组各挂一个 parent（Legal 条款区间）。
        parent_scope: 显式覆盖 parent 范围，表格兜底 Parent 使用 section。

    Returns:
        parent chunks（在前）+ 原 leaf 列表（parent_chunk_id 已回填）。
    """
    if not leaf_chunks:
        return leaf_chunks
    parent_budget = get_parent_chunk_tokens()

    def _parent_text(group: List[Document]) -> tuple[str, bool]:
        lines = []
        for c in group:
            t = (c.metadata.get("section_title") or "").strip()
            if not t:
                t = c.page_content.strip().split("\n")[0][:40]
            if t:
                lines.append(t)
        text = "\n".join(lines) or "（无标题内容）"
        truncated = False
        # P0-05：QA 全部问题列表 / Legal 大区间标题在极端文档下会超 parent
        # 预算，构造超长 embedding 输入。parent 是辅助上下文，截断 + 打标
        # （parent_truncated）可观测，不静默。
        if count_tokens(text) > parent_budget:
            truncated = True
            kept: list[str] = []
            used = 0
            for line in lines:
                t = count_tokens(line) + 1
                if kept and used + t > parent_budget:
                    break
                kept.append(line)
                used += t
            text = "\n".join(kept) + "\n…（超出 parent 预算截断）"
        return text, truncated

    groups = ([leaf_chunks] if group_size is None else
              [leaf_chunks[i:i + group_size]
               for i in range(0, len(leaf_chunks), group_size)])

    out: List[Document] = []
    for gi, group in enumerate(groups):
        ptext, p_truncated = _parent_text(group)
        parent_id = _chunk_id(file_path, f"parent:{anchor}:{gi}", ptext)
        scope = parent_scope or ("document" if group_size is None else "section")
        section_titles = list(dict.fromkeys(
            str(c.metadata.get("section_title") or "").strip()
            for c in group
            if str(c.metadata.get("section_title") or "").strip()
        ))
        section_title = (
            section_titles[0]
            if scope == "section" and len(section_titles) == 1
            else ""
        )
        group_paths = [
            [str(part) for part in (c.metadata.get("section_path") or []) if str(part)]
            for c in group
        ]
        common_path: list[str] = []
        if scope == "section" and group_paths:
            for parts in zip(*group_paths):
                if len(set(parts)) != 1:
                    break
                common_path.append(parts[0])
        pmeta = {
            "granularity": "parent",
            "chunk_id": parent_id,
            "parent_chunk_id": "",
            "parent_scope": scope,
            # 跨多个条款的虚拟 Parent 不能挂在第一个条款下；只保留
            # 组内所有 Leaf 的公共祖先路径。
            "section_path": common_path if scope == "section" else [],
            "section_title": section_title,
            "section_titles": section_titles if scope == "section" else [],
            "section_level": 1 if scope == "section" else 0,
            "chunk_tokens": count_tokens(ptext),
        }
        if p_truncated:
            pmeta["parent_truncated"] = "true"
        out.append(_make_doc(ptext, pmeta))
        for c in group:
            c.metadata["parent_chunk_id"] = parent_id
        out.extend(group)
    return out
