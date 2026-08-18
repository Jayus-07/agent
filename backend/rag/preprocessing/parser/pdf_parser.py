"""PdfParser — PyMuPDF 解析 PDF → Raw AST。

Phase 2 简化策略：
- 不识别标题（无章节结构，整篇当无结构长段落）
- 段落合并：同一页面、相邻、间距 < 阈值 视为同一段
- 表格识别（P1-5）：PyMuPDF page.find_tables() → table 节点（rows 保留，
  文本流剔除表格区域块，避免重复）；列关系通过 NL+CSV 双格式保留
- 图片（type=1 块）完全忽略（无 OCR/Vision，见 P0-3）
- 可观测：单页失败 → log warning + 计数器，最终汇总报告
"""
from __future__ import annotations

import re
from collections import Counter

import pymupdf as fitz  # PyMuPDF；用 pymupdf 别名 fitz 消除 1.24+ deprecation warning

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_PARAGRAPH_MERGE_GAP = 15  # PyMuPDF 文本块垂直间距（point）

# 标题字号比正文大多少（pt）视为标题（实测标题 14.0 vs 正文 10.5）
HEADING_SIZE_DELTA = 2.0

# 中文标题编号模式：第 N 章 / 一、 / 1. / 1.1.
_HEADING_NUMBER_RE = re.compile(
    r"^(第[一二三四五六七八九十百\d]+[章节]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[.、])"
)


def _body_font_size(sizes: list[float]) -> float:
    """计算正文字号（众数，正文占多数，标题字号是少数）。"""
    if not sizes:
        return 0.0
    counter = Counter(round(s, 1) for s in sizes)
    return counter.most_common(1)[0][0]


def _overlaps_table(bbox: tuple, table_bboxes: list[tuple]) -> bool:
    """判断文本块 bbox 是否与任一表格区域显著重叠（面积占比 > 0.5）。

    P1-5: 表格内容已由 find_tables 提取为 rows，需从文本流剔除避免重复入库。
    """
    if not table_bboxes:
        return False
    x0, y0, x1, y1 = bbox
    block_area = max((x1 - x0) * (y1 - y0), 1e-9)
    for tx0, ty0, tx1, ty1 in table_bboxes:
        ix0, iy0 = max(x0, tx0), max(y0, ty0)
        ix1, iy1 = min(x1, tx1), min(y1, ty1)
        inter = max(ix1 - ix0, 0) * max(iy1 - iy0, 0)
        if inter / block_area > 0.5:
            return True
    return False


def _is_heading_line(text: str, size: float, body_size: float) -> bool:
    """判断一行是否标题：字号明显大于正文，或匹配编号模式。"""
    if size >= body_size + HEADING_SIZE_DELTA:
        return True
    return bool(_HEADING_NUMBER_RE.match(text.strip()))


class PdfParser(BaseDocumentParser):
    def parse(self, file_path: str) -> DocumentAST:
        try:
            doc = fitz.open(file_path)
        except (fitz.FileDataError, RuntimeError) as e:
            # 已知可恢复异常：文件损坏 / 加密 / 格式异常 → 返回空 AST + log
            logger.error(
                f"[PdfParser] 打开失败 {file_path}: "
                f"{type(e).__name__}: {e}"
            )
            return DocumentAST(
                root=DocumentNode(type="section", text="", level=0),
                source_file=file_path,
                raw_text="",
            )
        except Exception as e:
            # 未知异常：log 后向上抛出，让 caller 决定如何处理
            logger.exception(
                f"[PdfParser] 打开异常 {file_path}: {type(e).__name__}"
            )
            raise

        raw_items: list[tuple[str, float]] = []  # (文本, 字号) — 仅非表格文本
        table_items: list[tuple[float, list[list[str]]]] = []  # (y_top, rows) — 表格
        skipped_pages: list[int] = []
        try:
            for page_idx in range(len(doc)):
                try:
                    page = doc[page_idx]
                    blocks = page.get_text("dict")["blocks"]
                    # P1-5: 表格识别（PyMuPDF find_tables，替代原"type=1→table"错误注释）。
                    # type=1 是图片块（当前完全忽略），表格需用 find_tables 检测行列结构，
                    # 产 table 节点走 NL+CSV 双格式，避免纯文本顺序流丢失列关系。
                    tables: list[tuple[tuple, list[list[str]]]] = []
                    try:
                        for t in page.find_tables().tables:
                            rows = t.extract()
                            if rows and any(any(str(c).strip() for c in r) for r in rows):
                                tables.append((t.bbox, rows))
                    except Exception as e:
                        logger.warning(
                            f"[PdfParser] 第 {page_idx} 页表格识别失败: "
                            f"{type(e).__name__}: {e}"
                        )
                    table_bboxes = [tb for tb, _ in tables]
                    # 文本块剔除与表格区域显著重叠的块（避免表格内容在段落与 table 节点重复）
                    text_blocks = [
                        b for b in blocks
                        if b.get("type") == 0
                        and not _overlaps_table(b.get("bbox", (0, 0, 0, 0)), table_bboxes)
                    ]
                    # 段落合并 + 表格按 y 顺序混排（保持文档阅读顺序）
                    merged: list[tuple[float, str, object]] = []
                    self._extract_blocks(text_blocks, merged, table_bboxes)
                    for tbbox, rows in tables:
                        merged.append((tbbox[1], "table", rows))
                    merged.sort(key=lambda x: x[0])
                    for _, kind, payload in merged:
                        if kind == "text":
                            raw_items.append(payload)  # type: ignore[arg-type]
                        else:
                            table_items.append((_, payload))  # type: ignore[arg-type]
                except Exception as e:
                    logger.warning(
                        f"[PdfParser] 第 {page_idx} 页解析失败: "
                        f"{type(e).__name__}: {e}"
                    )
                    skipped_pages.append(page_idx)
                    continue
        finally:
            doc.close()

        # 可观测：汇总报告
        if skipped_pages:
            logger.warning(
                f"[PdfParser] {file_path} 跳过 {len(skipped_pages)} 页: "
                f"{skipped_pages}"
            )
        if table_items:
            logger.info(
                f"[PdfParser] {file_path} 识别 {len(table_items)} 个表格"
            )

        # 标题启发式：统计正文字号（众数），识别标题 → section，其余 → paragraph
        body_size = _body_font_size([size for _, size in raw_items])
        root = DocumentNode(type="section", text="", level=0)
        current_section = root
        heading_count = 0
        # 文本与表格按 y 顺序混合（表格已按 y 排序进 table_items，此处按出现顺序交错插入）
        # 简化：表格按 y 归入最近的 section —— 遍历文本构建 section 栈后，再按 y 归属表格。
        text_cursor = 0
        for text, size in raw_items:
            if not text.strip():
                continue
            if _is_heading_line(text, size, body_size):
                section = DocumentNode(type="section", text=text.strip(), level=1)
                root.children.append(section)
                current_section = section
                heading_count += 1
            else:
                current_section.children.append(
                    DocumentNode(type="paragraph", text=text.strip())
                )
        # 表格节点挂到当前（最后出现的）section；P1-5 表格保留行列结构
        for _y, rows in table_items:
            from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text
            sec_title = current_section.text if current_section is not root else ""
            table_text = make_table_chunk_text(rows, section_title=sec_title)
            table_node = DocumentNode(type="table", text=table_text, rows=rows)
            current_section.children.append(table_node)

        if heading_count:
            logger.info(
                f"[PdfParser] {file_path} 识别 {heading_count} 个标题"
                f"（正文字号 {body_size}）"
            )

        raw_text = "\n".join(text for text, _ in raw_items)
        if table_items:
            raw_text += "\n\n" + "\n\n".join(
                make_table_chunk_text(rows) for _, rows in table_items
            )
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)

    def _extract_blocks(
        self, blocks: list, out: list, table_bboxes: list[tuple] | None = None
    ) -> None:
        """从 PyMuPDF blocks 抽取文本块 + 字号，按垂直位置合并段落。

        out 接收 (y_top, "text", (text, size)) 三元组，便于 parse() 与表格按 y 混排。
        table_bboxes 非空时跳过与表格区域重叠的块（表格内容已由 find_tables 提取）。
        """
        table_bboxes = table_bboxes or []
        # type=0 是文本块，type=1 是图片（忽略）
        text_blocks = [b for b in blocks if b.get("type") == 0]
        # 按垂直位置排序（y0 升序）
        text_blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        current_lines: list[str] = []
        current_sizes: list[float] = []
        current_y_bottom: float = -1.0

        def _flush(y_top: float):
            if current_lines:
                out.append((
                    y_top,
                    "text",
                    (
                        "\n".join(current_lines),
                        max(current_sizes) if current_sizes else 0.0,
                    ),
                ))
                current_lines.clear()
                current_sizes.clear()

        for block in text_blocks:
            block_text = "\n".join(
                span["text"]
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()
            if not block_text:
                continue
            # block 字号 = 最大 span 字号（标题字号较大）
            block_sizes = [
                span.get("size", 0)
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ]
            block_size = max(block_sizes) if block_sizes else 0.0

            block_y_top = block["bbox"][1]
            current_size = max(current_sizes) if current_sizes else 0.0
            # 字号突变（标题 vs 正文）也视为段落边界，避免标题与正文被合并
            size_jump = abs(block_size - current_size) >= HEADING_SIZE_DELTA
            if current_lines and (
                (block_y_top - current_y_bottom) > _PARAGRAPH_MERGE_GAP or size_jump
            ):
                # 间距超过阈值或字号突变 → 当前段落结束
                _flush(block_y_top)

            current_lines.append(block_text)
            current_sizes.append(block_size)
            current_y_bottom = block["bbox"][3]

        _flush(1e9)
