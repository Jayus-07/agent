"""统一 Document AST — 切分流水线的中间表示。

Parser 产出 Raw AST（格式级结构），Structure Analyzer 归一化为 Normalized AST，
所有 ChunkStrategy 消费 Normalized AST。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from typing import Iterator

LEAF_TYPES = {
    "paragraph", "list", "table", "qa_question", "qa_answer", "code", "figure",
}


@dataclass
class DocumentNode:
    """AST 节点。section 是容器（text=标题，children=子节点），leaf 类型是叶子。"""
    type: str
    text: str
    level: int = 0
    children: list["DocumentNode"] = field(default_factory=list)
    rows: list[list[str]] | None = None       # table 专用
    source_range: tuple[int, int] = (0, 0)    # (start, end) 在 raw_text 中的偏移
    # R-P0-3 原文可追溯：清洗前原文 + 清洗操作留痕（字段追加在尾部，位置构造兼容）
    raw_text: str = ""                        # 清洗前原始文本；未清洗时为空串（等价 text）
    cleaning_operations: list[str] = field(default_factory=list)  # DocumentCleaner 执行的操作清单
    # 2026-10-18 内容完整性修复：清洗删除证据（操作名/噪音分类/原行样本）。
    # 供 Raw AST → 清洗输出 的审计对账；字段追加在尾部，位置构造兼容。
    cleaning_dropped: list[dict] = field(default_factory=list)
    # §5.2 原文可追溯（2026-09-17 追加，位置构造兼容）：页码与版面坐标
    # page_number：1-based 页码，非分页格式（MD/TXT 等）恒 0；bbox：PDF 版面
    # (x0, y0, x1, y1)，仅 PdfParser 填写，其他解析器为空元组
    page_number: int = 0
    bbox: tuple = ()
    # 稳定身份与父子关系：ID 在同一文档版本的重复解析中保持一致。
    node_id: str = ""
    parent_node_id: str = ""
    section_id: str = ""
    parent_section_id: str = ""
    # 格式专属原文定位（段落、行、页、幻灯片、DOM/XPath 等）。
    source_locator: dict = field(default_factory=dict)


@dataclass
class DocumentAST:
    """整棵文档结构树。root 是虚拟根（type="section", level=0, text=""）。"""
    root: DocumentNode
    source_file: str = ""
    raw_text: str = ""
    # §5.1 质量记录：扫描件 OCR 需求和执行状态（PdfParser 写入，下游留痕用）。
    # ``ocr_triggered`` 只表示有页面实际产出 OCR 文本；``ocr_required``
    # 表示文本层不足，即使 OCR 关闭/不可用也必须保留这个事实。
    ocr_required: bool = False
    ocr_attempted: bool = False
    ocr_triggered: bool = False
    ocr_pages: int = 0  # 实际产出文本的 OCR 页数（失败页不计）
    ocr_confidence_mean: float | None = None
    ocr_confidence_min: float | None = None
    ocr_confidence_scored_regions: int = 0
    ocr_confidence_unavailable_regions: int = 0
    # P0 超长文档收口（2026-10-13）：OCR 部分覆盖检查——字段追加在尾部，
    # 位置构造兼容。ocr_page_limit>0 表示 OCR 触发 RAG_OCR_MAX_PAGES 上限、
    # 其后页面未识别；ocr_failed_pages 为 OCR 失败页数。
    ocr_page_limit: int = 0
    ocr_failed_pages: int = 0
    # 本次 PDF 实际采用的 OCR 页预算；供质量记录/Trace 说明上限来源。
    ocr_page_budget: int = 0
    ocr_page_budget_source: str = "default"
    # 表格解析资源保护：CSV 流式计数可得精确总量；Excel 达到上限即停止，
    # total_known=False 时总量与截断量都只报告已观察到的下界。
    table_rows_truncated: int = 0
    table_rows_total: int = 0
    table_rows_total_known: bool = True
    table_cells_truncated: int = 0
    table_cells_total: int = 0
    table_cells_total_known: bool = True
    table_truncation_reason: str = ""
    # PDF 解析完整性：修复过结构的 PDF 若大量页无可检索内容，质量门禁拒绝部分入库。
    pdf_page_count: int = 0
    pdf_pages_with_content: int = 0
    pdf_skipped_pages: int = 0
    pdf_repaired: bool = False
    # 解析结果契约：partial 表示存在需要关注的解析降级；issues 只保存
    # 代码、严重度、页码和短说明，不复制文档正文。
    parse_status: str = "complete"
    parse_issues: list[dict] = field(default_factory=list)
    pdf_skipped_page_numbers: list[int] = field(default_factory=list)
    pdf_image_count: int = 0
    pdf_image_pages: list[int] = field(default_factory=list)
    pdf_unparsed_visual_pages: list[int] = field(default_factory=list)
    pdf_table_detection_failed_pages: list[int] = field(default_factory=list)
    # OCR 页码统一为 1-based；区域 bbox 仍使用 PDF 页面坐标。
    ocr_required_pages: list[int] = field(default_factory=list)
    ocr_required_regions: list[dict] = field(default_factory=list)
    ocr_failed_page_numbers: list[int] = field(default_factory=list)
    ocr_failed_regions: list[dict] = field(default_factory=list)
    # 解析器明确识别出的文档标题；不从普通章节标题兜底推断。
    document_title: str = ""
    document_title_node: DocumentNode | None = None
    # PDF 页面级审计：纯空白页不计为丢页；有可见内容但无 AST 内容的页需拦截。
    pdf_blank_pages: list[int] = field(default_factory=list)
    pdf_unparsed_content_pages: list[int] = field(default_factory=list)
    pdf_vector_text_suspect_pages: list[int] = field(default_factory=list)
    pdf_layout_fallback_pages: list[int] = field(default_factory=list)
    # 独立调用 PDF 原生文字提取与 AST 页内容对账；扫描/OCR 页明确标为未验证。
    pdf_source_text_audit: dict = field(default_factory=dict)
    # 解析契约版本与实际格式，随索引代次保存。
    source_format: str = ""
    parser_version: str = ""


def walk(node: DocumentNode) -> Iterator[DocumentNode]:
    """DFS 先序遍历所有节点。"""
    yield node
    for child in node.children:
        yield from walk(child)


def iter_sections(ast: DocumentAST) -> Iterator[tuple[DocumentNode, list[str]]]:
    """为每个 section 节点产出 (node, 祖先标题链)，链不含虚拟根。"""
    def _dfs(node: DocumentNode, path: list[str]):
        for child in node.children:
            if child.type == "section":
                child_path = path + [child.text]
                yield child, child_path
                yield from _dfs(child, child_path)
            else:
                yield from _dfs(child, path)

    yield from _dfs(ast.root, [])


_LEGAL_SECTION_RE = re.compile(r"^第[零〇两一二三四五六七八九十百千万\d]+条")


def infer_section_type(node: DocumentNode) -> str:
    """按解析器给出的真实定位与标题推断章节类型，不臆造业务标题。"""
    locator = node.source_locator or {}
    if locator.get("slide"):
        return "slide"
    if locator.get("json_path") and node.level > 1:
        return "record"
    if locator.get("xml_tag"):
        return "element"
    if _LEGAL_SECTION_RE.match((node.text or "").strip()):
        return "clause"
    return "section"


def annotate_ast_provenance(ast: DocumentAST, parser_version: str = "") -> list[dict]:
    """确定性补齐节点身份、章节关系和可回溯文本跨度。

    ``source_range`` 使用 ``DocumentAST.raw_text`` 的 Unicode 字符偏移，右端
    不包含。格式原生定位保存在 ``source_locator``，例如 PDF 页/bbox、工作表
    行号、幻灯片形状 ID、HTML DOM 路径或 JSON/XML 字段路径。
    """
    raw_text = ast.raw_text or ""
    if not ast.source_format:
        ast.source_format = os.path.splitext(ast.source_file)[1].lower().lstrip(".")
    if parser_version:
        ast.parser_version = parser_version

    cursor = 0
    sections: list[dict] = []
    section_pages: dict[str, list[int]] = {}
    section_parents: dict[str, str] = {}
    document_title = ast.document_title.strip()
    initial_heading_path = [document_title] if document_title else []

    def locate(node: DocumentNode) -> tuple[int, int]:
        nonlocal cursor
        target = (node.raw_text or node.text or "").strip()
        try:
            start, end = int(node.source_range[0]), int(node.source_range[1])
        except (TypeError, ValueError, IndexError):
            start, end = 0, 0
        if start >= 0 and end > start and end <= len(raw_text):
            existing = raw_text[start:end]
            if not target or target in existing or existing.strip() in target:
                return start, end
        if not target:
            return (start, end) if end > start else (0, 0)
        found = raw_text.find(target, cursor)
        if found < 0:
            found = raw_text.find(target)
        if found < 0:
            return (start, end) if end > start else (0, 0)
        cursor = max(cursor, found + len(target))
        return found, found + len(target)

    def visit(
        node: DocumentNode,
        parent_node_id: str,
        active_section_id: str,
        active_section_parent_id: str,
        heading_path: list[str],
        sibling_index: int,
    ) -> None:
        node.source_range = locate(node)
        locator = dict(node.source_locator or {})
        locator.setdefault("source_format", ast.source_format)
        if node.page_number > 0:
            locator.setdefault("page", node.page_number)
        if node.bbox:
            locator.setdefault("bbox", list(node.bbox))
        start, end = node.source_range
        if end > start:
            locator.setdefault(
                "source_range", {"start": start, "end": end, "unit": "char"},
            )
        node.source_locator = locator

        section_id = active_section_id
        parent_section_id = active_section_parent_id
        path = heading_path
        if node.type == "section" and node.level > 0:
            identity = [
                active_section_id, node.text, node.level, sibling_index,
                start, end, locator,
            ]
            section_id = hashlib.sha256(
                json.dumps(identity, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()[:24]
            parent_section_id = active_section_id
            if node is ast.document_title_node:
                path = heading_path or [node.text.strip()]
            else:
                path = heading_path + [node.text.strip()]
            node.section_id = section_id
            node.parent_section_id = parent_section_id
            section_parents[section_id] = parent_section_id
            sections.append({
                "section_id": section_id,
                "parent_section_id": parent_section_id,
                "heading": node.text.strip(),
                "heading_level": node.level,
                "heading_path": list(path),
                "section_type": infer_section_type(node),
                "section_order": len(sections),
                "page_start": None,
                "page_end": None,
                "source_span": [start, end] if end > start else None,
                "source_locator": locator,
            })
        else:
            node.section_id = section_id
            node.parent_section_id = parent_section_id

        node.parent_node_id = parent_node_id
        node_identity = [
            parent_node_id, node.type, sibling_index, start, end, locator,
            hashlib.sha256((node.raw_text or node.text or "").encode("utf-8")).hexdigest()[:16],
        ]
        node.node_id = hashlib.sha256(
            json.dumps(node_identity, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:24]
        if node.page_number > 0:
            page_section_id = section_id
            while page_section_id:
                section_pages.setdefault(page_section_id, []).append(node.page_number)
                page_section_id = section_parents.get(page_section_id, "")
        for index, child in enumerate(node.children):
            visit(
                child, node.node_id, section_id, parent_section_id, path, index,
            )

    visit(ast.root, "", "", "", initial_heading_path, 0)
    for item in sections:
        page_numbers = section_pages.get(item["section_id"], [])
        if page_numbers:
            item["page_start"] = min(page_numbers)
            item["page_end"] = max(page_numbers)
    ast._section_tree = sections
    return sections


def section_tree(ast: DocumentAST) -> list[dict]:
    """返回最近一次归一化产生的章节元数据快照。"""
    cached = getattr(ast, "_section_tree", None)
    if cached is not None:
        return [dict(item) for item in cached]
    return annotate_ast_provenance(ast)
