"""PdfParser — PyMuPDF 解析 PDF → Raw AST。

Phase 2 简化策略：
- 不识别标题（无章节结构，整篇当无结构长段落）
- 段落合并：同一页面、相邻、间距 < 阈值 视为同一段
- 表格识别（P1-5）：PyMuPDF page.find_tables() → table 节点（rows 保留，
  文本流剔除表格区域块，避免重复）；列关系通过 NL+CSV 双格式保留
- 图片（type=1 块）不做视觉理解；大面积图片页和表格检测失败进入解析质量报告
- OCR 兜底按页检查低文本量扫描页和明显图片区域；混合 PDF 对图片区域裁剪
  后识别，减少整页 OCR 重复原生文字。OCR 页无字号信息，表格不提取
  （OCR 无法还原行列结构）
- 可观测：页码、bbox、OCR 所需/失败页、图片页、表格检测失败页写入 AST 和质量记录
"""
from __future__ import annotations

import re
from collections import Counter

import pymupdf as fitz  # PyMuPDF；用 pymupdf 别名 fitz 消除 1.24+ deprecation warning

# 默认走原生块的快速路径；仅检测到双栏阅读顺序信号时，使用本地几何布局
# 排序。该回退不调用云端模型，也不依赖未安装的 PyMuPDF Layout 插件。

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.config.rag import (
    RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT, RAG_OCR_MIN_TEXT_CHARS,
)
from backend.shared.logger import logger

_PARAGRAPH_MERGE_GAP = 15  # PyMuPDF 文本块垂直间距（point）

# 标题字号比正文大多少（pt）视为标题（实测标题 14.0 vs 正文 10.5）
HEADING_SIZE_DELTA = 2.0

# 标题编号模式：第 N 章 / 一、 / 1. / 10、
# (?!\d) 排除小数编号条款（2.1 / 3.2 / 6.2）：旧模式 \d+(?:\.\d+)*[.、] 会回溯成
# "2." 前缀而把条款误判为标题，导致每个条款各自成 section、真正的章节标题
# （如「二、七天无理由退货」）被拆成孤立空 section，其正文 chunk 丢失标题上下文。
# 阿拉伯编号（\d+[.、]）仅作同字号短标题的弱信号（见 _is_heading_line 的
# 正文特征排除）：制度类 PDF 的 "1. xxx；" 条目行与标题同字号且极常见，
# 2026-10-02 实测（多部门知识库验收 TD-02）无差别判标题会把整份文档的
# 条目全部吞进 section → leaf 层清空、节标题丢失、质量门禁误杀。
_HEADING_NUMBER_RE = re.compile(
    r"^(第[一二三四五六七八九十百\d]+[章节]|[一二三四五六七八九十]+、|\d+[.、](?!\d))"
)

# 阿拉伯编号行的正文特征：行尾句读或长行 → 条目而非标题。
# 中文制度条款几乎必以 ；/。 收尾；标题（"1. 概述"/"10、附录"）短且无标点。
_HEADING_MAX_PLAIN_LEN = 40
_BODY_CLAUSE_TAILS = ("；", "。", "，", ";", ",", "！", "！", "?", "？", ":", "：")


def _body_font_size(sizes: list[float]) -> float:
    """计算正文字号（众数，正文占多数，标题字号是少数）。"""
    sizes = [size for size in sizes if size > 0]
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


def _page_has_rendered_content(page) -> bool:
    """用低分辨率缩略图区分真空白页与无文字层的可见内容页。"""
    pix = page.get_pixmap(
        matrix=fitz.Matrix(0.12, 0.12),
        colorspace=fitz.csGRAY,
        alpha=False,
    )
    samples = pix.samples
    if not samples:
        return False
    stride = max(1, len(samples) // 50_000)
    values = samples[::stride]
    if not values:
        return False
    # 相对主背景检测像素变化，浅灰纸张仍按背景处理，淡色字迹相对纸张的
    # 对比也能触发。缩略图最多采样约 5 万像素，避免大页额外占用过高。
    background = Counter(values).most_common(1)[0][0]
    content_pixels = sum(abs(value - background) >= 5 for value in values)
    # 不用单个暗像素触发，避免渲染噪点将白纸判成内容页；显著偏暗的整页
    # 背景仍视为可见内容，浅色纸张则依靠相对背景的像素差识别字迹。
    return background < 200 or content_pixels >= max(4, int(len(values) * 0.0005))


def _page_has_dense_vector_content(page) -> bool:
    """识别文本层稀疏、但由大量矢量路径组成的疑似扫描/描摹页。"""
    try:
        drawings = page.get_drawings()
    except Exception:
        return False
    if len(drawings) >= 80:
        return True
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    drawn_area = 0.0
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is not None:
            drawn_area += max(float(rect.width), 0.0) * max(float(rect.height), 0.0)
    return drawn_area / page_area >= 0.2


def _text_bbox(event: tuple) -> tuple[float, float, float, float]:
    bbox = event[4]
    if len(bbox) != 4:
        return (0.0, event[0], 0.0, event[0])
    return tuple(float(value) for value in bbox)


def _order_page_events(events: list[tuple], page_width: float) -> tuple[list[tuple], bool]:
    """复杂双栏页按列阅读；普通单栏页保留纵坐标排序。"""
    ordered = sorted(events, key=lambda event: event[0])
    text_events = [
        event for event in ordered
        if event[1] == "text" and str(event[2][0] or "").strip()
    ]
    midpoint = page_width / 2

    def column_of(event: tuple) -> int:
        bbox = _text_bbox(event)
        if bbox[2] < midpoint - 6:
            return 0
        if bbox[0] > midpoint + 6:
            return 1
        return -1

    left = [event for event in text_events if column_of(event) == 0]
    right = [event for event in text_events if column_of(event) == 1]
    if len(left) < 2 or len(right) < 2:
        return ordered, False

    left_top, left_bottom = min(e[0] for e in left), max(e[0] for e in left)
    right_top, right_bottom = min(e[0] for e in right), max(e[0] for e in right)
    overlap = max(0.0, min(left_bottom, right_bottom) - max(left_top, right_top))
    shorter_height = max(1.0, min(left_bottom - left_top, right_bottom - right_top))
    if overlap / shorter_height < 0.35:
        return ordered, False

    column_top = min(left_top, right_top)
    column_bottom = max(left_bottom, right_bottom)
    in_columns = [
        event for event in ordered
        if column_top <= event[0] <= column_bottom
    ]
    before = [event for event in ordered if event[0] < column_top]
    after = [event for event in ordered if event[0] > column_bottom]
    full_width = [
        event for event in in_columns
        if (
            _text_bbox(event)[2] - _text_bbox(event)[0] >= page_width * 0.68
            or column_of(event) < 0
        )
    ]
    output = sorted(before, key=lambda event: event[0])
    cursor = column_top - 1.0

    def append_band(end_y: float) -> None:
        band = [
            event for event in in_columns
            if cursor <= event[0] < end_y and event not in full_width
        ]
        has_left = any(column_of(event) == 0 for event in band)
        has_right = any(column_of(event) == 1 for event in band)
        if has_left and has_right:
            band.sort(key=lambda event: (
                column_of(event) if column_of(event) >= 0 else 2,
                event[0],
            ))
        else:
            band.sort(key=lambda event: event[0])
        output.extend(band)

    for separator in sorted(full_width, key=lambda event: event[0]):
        append_band(separator[0])
        output.append(separator)
        cursor = separator[0]
    append_band(column_bottom + 1.0)
    output.extend(sorted(after, key=lambda event: event[0]))
    return output, True


def _normalize_audit_text(value: str) -> str:
    return re.sub(r"[\W_]", "", str(value or ""), flags=re.UNICODE).casefold()


def _audit_native_text_page(source_text: str, ast_text: str) -> tuple[int, int]:
    """返回可对账/总片段数；逐行滑窗降低阅读顺序差异的影响。"""
    normalized_ast = _normalize_audit_text(ast_text)
    covered = total = 0
    for line in (source_text or "").splitlines():
        normalized_line = _normalize_audit_text(line)
        if not normalized_line:
            continue
        windows = (
            [normalized_line]
            if len(normalized_line) <= 24
            else [
                normalized_line[start:start + 24]
                for start in range(0, len(normalized_line), 20)
            ]
        )
        for window in windows:
            if not window:
                continue
            total += 1
            covered += int(window in normalized_ast)
    return covered, total


def _is_heading_line(text: str, size: float, body_size: float) -> bool:
    """判断一行是否标题：字号明显大于正文，或匹配编号模式。

    编号模式中阿拉伯编号（1. / 10、）仅是弱信号：行尾带句读或超过
    _HEADING_MAX_PLAIN_LEN 的长行是制度条款的强特征，判回正文——
    同字号条款误判标题会让整节正文吞进 section（见 _HEADING_NUMBER_RE
    注释的实测记录）。中文编号与"第N章"不受此排除影响。
    """
    if size >= body_size + HEADING_SIZE_DELTA:
        return True
    stripped = text.strip()
    if not _HEADING_NUMBER_RE.match(stripped):
        return False
    if re.match(r"^\d+[.、]", stripped):
        if len(stripped) > _HEADING_MAX_PLAIN_LEN or stripped.endswith(_BODY_CLAUSE_TAILS):
            return False
    return True


class PdfParser(BaseDocumentParser):
    def parse(
        self, file_path: str, *,
        ocr_page_budget: int | None = None,
    ) -> DocumentAST:
        if ocr_page_budget is not None and not (
            1 <= int(ocr_page_budget) <= RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT
        ):
            raise ValueError(
                "单次 PDF OCR 页预算必须在 1 到 "
                f"{RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT} 页之间"
            )
        budget_source = "upload_override" if ocr_page_budget is not None else "default"
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
                parse_status="failed",
                parse_issues=[{
                    "code": "pdf_open_failed",
                    "severity": "error",
                    "page_numbers": [],
                    "detail": f"{type(e).__name__}: {e}",
                }],
            )
        except Exception as e:
            # 未知异常：log 后向上抛出，让 caller 决定如何处理
            logger.exception(
                f"[PdfParser] 打开异常 {file_path}: {type(e).__name__}"
            )
            raise

        pdf_repaired = bool(getattr(doc, "is_repaired", False))
        raw_items: list[tuple[str, float, int, tuple]] = []  # (文本, 字号, 页码, bbox) — 仅非表格文本
        # P0-04（2026-10-13）：段落与表格按 (页序, 页内 y) 统一成事件流，
        # 建 AST 时按阅读顺序交错挂 section——旧实现先处理全部文本、再把
        # 全部表格追加到最后一个 section，表格-正文交错关系丢失。
        page_count = len(doc)
        page_events: list[list] = [[] for _ in range(page_count)]
        page_widths: dict[int, float] = {}
        skipped_pages: list[int] = []  # 1-based 页码
        image_pages: set[int] = set()
        image_regions_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
        large_image_regions_by_page: dict[
            int, list[tuple[float, float, float, float]]
        ] = {}
        image_count = 0
        unparsed_visual_pages: set[int] = set()
        blank_pages: set[int] = set()
        unparsed_content_pages: set[int] = set()
        vector_text_suspect_pages: set[int] = set()
        layout_fallback_pages: set[int] = set()
        native_source_text_by_page: dict[int, str] = {}
        table_detection_failed_pages: set[int] = set()
        parse_issues: list[dict] = []
        try:
            for page_idx in range(len(doc)):
                try:
                    page = doc[page_idx]
                    page_widths[page_idx + 1] = float(page.rect.width)
                    blocks = page.get_text("dict")["blocks"]
                    # 保留与 dict→事件流不同的原生文本提取结果，供最终 AST 做
                    # 页级独立对账。它只验证 PDF 可检索文字层，不覆盖 OCR 准确率。
                    native_source_text_by_page[page_idx + 1] = page.get_text(
                        "text", sort=False,
                    )
                    image_blocks = [b for b in blocks if b.get("type") == 1]
                    if image_blocks:
                        page_number = page_idx + 1
                        image_pages.add(page_number)
                        image_count += len(image_blocks)
                        page_area = max(float(page.rect.width * page.rect.height), 1.0)
                        for image_block in image_blocks:
                            bbox = tuple(
                                float(value)
                                for value in (image_block.get("bbox") or ())
                            )
                            if len(bbox) == 4:
                                image_regions_by_page.setdefault(
                                    page_number, [],
                                ).append(bbox)
                                image_area = max(
                                    0.0,
                                    float(bbox[2] - bbox[0]) * float(bbox[3] - bbox[1]),
                                )
                                if image_area / page_area >= 0.05:
                                    unparsed_visual_pages.add(page_number)
                                    large_image_regions_by_page.setdefault(
                                        page_number, [],
                                    ).append(bbox)
                    # P1-5: 表格识别（PyMuPDF find_tables，替代原"type=1→table"错误注释）。
                    # type=1 是图片块（当前完全忽略），表格需用 find_tables 检测行列结构，
                    # 产 table 节点走 NL+CSV 双格式，避免纯文本顺序流丢失列关系。
                    tables: list[tuple[tuple, list[list[str]]]] = []
                    try:
                        for t in page.find_tables(use_layout=False).tables:
                            rows = t.extract()
                            if rows and any(
                                any(c is not None and str(c).strip() for c in row)
                                for row in rows
                            ):
                                tables.append((t.bbox, rows))
                    except Exception as e:
                        table_detection_failed_pages.add(page_idx + 1)
                        logger.warning(
                            f"[PdfParser] 第 {page_idx + 1} 页表格识别失败: "
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
                    # §5.2 可追溯：merged 元组携带页码与版面 bbox
                    events: list[tuple[float, str, object, int, tuple]] = []
                    self._extract_blocks(text_blocks, events, table_bboxes, page_idx + 1)
                    for tbbox, rows in tables:
                        events.append((tbbox[1], "table", rows, page_idx + 1, tbbox))
                    events, used_layout_fallback = _order_page_events(
                        events, float(page.rect.width),
                    )
                    page_events[page_idx] = events
                    if used_layout_fallback:
                        layout_fallback_pages.add(page_idx + 1)
                    if not events:
                        if _page_has_rendered_content(page):
                            unparsed_content_pages.add(page_idx + 1)
                        else:
                            blank_pages.add(page_idx + 1)
                    elif not image_blocks:
                        page_text_chars = sum(
                            len(str(event[2][0] or ""))
                            for event in events if event[1] == "text"
                        )
                        if (
                            page_text_chars < RAG_OCR_MIN_TEXT_CHARS
                            and _page_has_dense_vector_content(page)
                        ):
                            vector_text_suspect_pages.add(page_idx + 1)
                except Exception as e:
                    logger.warning(
                        f"[PdfParser] 第 {page_idx + 1} 页解析失败: "
                        f"{type(e).__name__}: {e}"
                    )
                    skipped_pages.append(page_idx + 1)
                    continue
        finally:
            doc.close()

        def _ordered_events() -> list[tuple[float, str, object, int, tuple]]:
            return [event for page in page_events for event in page]

        def _text_items(events: list[tuple]) -> list[tuple[str, float, int, tuple]]:
            return [
                (event[2][0], event[2][1], event[3], event[4])
                for event in events
                if event[1] == "text"
            ]

        ordered_events = _ordered_events()
        raw_items = _text_items(ordered_events)
        native_page_chars = [0] * page_count
        for text, _size, page_number, _bbox in raw_items:
            if 1 <= page_number <= page_count:
                native_page_chars[page_number - 1] += len(text)
        # OCR 按页面和图像区域判断，避免整篇文字层充足的混合 PDF 掩盖扫描页。
        # 低文本量扫描页识别页内图片；文字量充足但包含大面积图片时仅裁剪图片
        # 区域，避免对原生文字做整页 OCR。纯扫描页继续逐页兜底。
        ocr_required_regions: list[tuple[int, tuple[float, ...] | None]] = []
        for page_number, page_chars in enumerate(native_page_chars, start=1):
            if page_number in blank_pages:
                continue
            page_images = image_regions_by_page.get(page_number, [])
            if page_chars == 0 and page_number in unparsed_content_pages:
                # 含矢量内容但没有图片块的页面也会走整页 OCR；不能只依赖
                # 文本量和 image block 推断扫描页。
                targets = page_images or [None]
            elif page_number in vector_text_suspect_pages:
                targets = [None]
            elif page_chars < RAG_OCR_MIN_TEXT_CHARS and page_images:
                targets = page_images
            else:
                targets = large_image_regions_by_page.get(page_number, [])
            ocr_required_regions.extend(
                (page_number, bbox) for bbox in targets
            )
        ocr_required_pages = list(dict.fromkeys(
            page_number for page_number, _bbox in ocr_required_regions
        ))

        ocr_required = bool(ocr_required_pages)
        ocr_attempted = False
        ocr_pages = 0
        ocr_page_limit = 0
        ocr_failed_page_numbers: list[int] = []
        ocr_failed_regions: list[tuple[int, tuple[float, ...] | None]] = []
        selected_ocr_regions: list[tuple[int, tuple[float, ...] | None]] = []
        if ocr_required:
            from backend.config import rag as rag_cfg
            from backend.rag.preprocessing.parser import ocr as ocr_mod

            max_ocr_pages = (
                int(ocr_page_budget)
                if ocr_page_budget is not None
                else max(int(rag_cfg.RAG_OCR_MAX_PAGES), 0)
            )
            pages_to_ocr = ocr_required_pages[:max_ocr_pages]
            pages_to_ocr_set = set(pages_to_ocr)
            selected_ocr_regions = [
                (page_number, bbox)
                for page_number, bbox in ocr_required_regions
                if page_number in pages_to_ocr_set
            ]
            ocr_page_limit = len(ocr_required_pages) - len(pages_to_ocr)
            if ocr_mod.ocr_available():
                ocr_attempted = True
                logger.warning(
                    f"[PdfParser] {file_path} 需要 OCR 的页面 {ocr_required_pages}，"
                    f"启动 OCR 兜底（{rag_cfg.RAG_OCR_PROVIDER}）"
                )
                ocr_items, ocr_failed_regions = self._ocr_fallback(
                    file_path, selected_ocr_regions,
                )
                ocr_failed_page_numbers = sorted({
                    page_number for page_number, _bbox in ocr_failed_regions
                })
                for text, size, page_number, bbox_ in ocr_items:
                    y = bbox_[1] if len(bbox_) == 4 else 1e9
                    page_events[page_number - 1].append((
                        y, "text", (text, size, page_number, bbox_),
                        page_number, bbox_,
                    ))
                    page_events[page_number - 1], used_layout_fallback = (
                        _order_page_events(
                            page_events[page_number - 1],
                            page_widths.get(page_number, 0.0),
                        )
                    )
                    if used_layout_fallback:
                        layout_fallback_pages.add(page_number)
                ocr_pages = len({item[2] for item in ocr_items})
                logger.info(
                    f"[PdfParser] {file_path} OCR 完成: {ocr_pages} 页产出文本"
                    + (f"，失败页码 {ocr_failed_page_numbers}"
                       if ocr_failed_page_numbers else "")
                    + (f"，页数上限遗漏 {ocr_page_limit} 页"
                       if ocr_page_limit else "")
                )
            else:
                logger.warning(
                    f"[PdfParser] {file_path} 需要 OCR 的页面 {ocr_required_pages}，"
                    "但当前 OCR 供应商不可用"
                )

        ordered_events = _ordered_events()
        raw_items = _text_items(ordered_events)
        table_count = sum(1 for _, kind, *_ in ordered_events if kind == "table")
        parsed_pages = {event[3] for event in ordered_events}
        unparsed_content_pages.difference_update(parsed_pages)

        if skipped_pages:
            logger.warning(
                f"[PdfParser] {file_path} 跳过 {len(skipped_pages)} 页: "
                f"{skipped_pages}"
            )
        if table_count:
            logger.info(f"[PdfParser] {file_path} 识别 {table_count} 个表格")

        parse_issues = []
        if image_pages:
            parse_issues.append({
                "code": "pdf_images_detected",
                "severity": "info",
                "page_numbers": sorted(image_pages),
                "detail": "检测到图片块；当前解析器不做通用图像语义识别",
            })
        if table_detection_failed_pages:
            parse_issues.append({
                "code": "pdf_table_detection_failed",
                "severity": "warn",
                "page_numbers": sorted(table_detection_failed_pages),
                "detail": "表格结构检测器失败，该页已保留文本流但表格行列关系未确认",
            })
        if unparsed_visual_pages:
            visual_regions = [
                {"page": page_number, "bbox": list(bbox)}
                for page_number, bboxes in sorted(large_image_regions_by_page.items())
                for bbox in bboxes
            ]
            parse_issues.append({
                "code": "pdf_visual_content_unparsed",
                "severity": "warn",
                "page_numbers": sorted(unparsed_visual_pages),
                "regions": visual_regions[:100],
                "detail": "页面含大面积图片；OCR 仅提取图中文字，不还原图像语义或图表结构",
            })
        if ocr_required and not ocr_attempted:
            parse_issues.append({
                "code": "pdf_ocr_unavailable",
                "severity": "error",
                "page_numbers": list(ocr_required_pages),
                "regions": [
                    {"page": page_number, "bbox": list(bbox) if bbox else None}
                    for page_number, bbox in ocr_required_regions
                ][:100],
                "detail": "所需图片区域未执行 OCR，当前 OCR 供应商不可用",
            })
        if ocr_failed_page_numbers:
            parse_issues.append({
                "code": "pdf_ocr_failed",
                "severity": "error",
                "page_numbers": list(ocr_failed_page_numbers),
                "regions": [
                    {"page": page_number, "bbox": list(bbox) if bbox else None}
                    for page_number, bbox in ocr_failed_regions
                ][:100],
                "detail": "部分图片区域 OCR 失败，页面内容不完整",
            })
        if ocr_page_limit:
            omitted_pages = ocr_required_pages[len(pages_to_ocr):]
            omitted_page_set = set(omitted_pages)
            parse_issues.append({
                "code": "pdf_ocr_page_limit",
                "severity": "error",
                "page_numbers": omitted_pages,
                "regions": [
                    {"page": page_number, "bbox": list(bbox) if bbox else None}
                    for page_number, bbox in ocr_required_regions
                    if page_number in omitted_page_set
                ][:100],
                "detail": "所需 OCR 页面超过配置上限，部分页面未识别",
            })

        # 标题启发式：统计正文字号（众数），识别标题 → section，其余 → paragraph
        # P0-04：按统一事件流遍历，表格在阅读顺序位置挂当前 section（不再全部
        # 堆到最后一个 section）。§5.2 可追溯：段落/标题/表格节点填页码与 bbox
        body_size = _body_font_size([size for _, size, _, _ in raw_items])
        root = DocumentNode(type="section", text="", level=0)
        current_section = root
        heading_count = 0
        for _y, kind, payload, pno, bbox_ in ordered_events:
            if kind == "text":
                text, size = payload[0], payload[1]
                if not text.strip():
                    continue
                if _is_heading_line(text, size, body_size):
                    section = DocumentNode(type="section", text=text.strip(), level=1,
                                           page_number=pno, bbox=bbox_)
                    root.children.append(section)
                    current_section = section
                    heading_count += 1
                else:
                    current_section.children.append(
                        DocumentNode(type="paragraph", text=text.strip(),
                                     page_number=pno, bbox=bbox_)
                    )
            else:
                # kind == "table"：rows 在阅读顺序位置挂当前 section
                from backend.rag.preprocessing.parser._table_nl import make_table_chunk_text
                rows = payload
                sec_title = current_section.text if current_section is not root else ""
                table_text = make_table_chunk_text(rows, section_title=sec_title)
                table_node = DocumentNode(type="table", text=table_text, rows=rows,
                                          page_number=pno, bbox=bbox_)
                current_section.children.append(table_node)

        if heading_count:
            logger.info(
                f"[PdfParser] {file_path} 识别 {heading_count} 个标题"
                f"（正文字号 {body_size}）"
            )

        raw_text = "\n".join(text for text, _, _, _ in raw_items)
        _tables_in_order = [rows for _, kind, rows, _, _ in ordered_events if kind == "table"]
        if _tables_in_order:
            raw_text += "\n\n" + "\n\n".join(
                make_table_chunk_text(rows) for rows in _tables_in_order
            )
        parsed_page_text: dict[int, list[str]] = {}
        for _y, kind, payload, page_number, _bbox in ordered_events:
            if kind == "text":
                parsed_page_text.setdefault(page_number, []).append(
                    str(payload[0] or ""),
                )
            elif kind == "table":
                parsed_page_text.setdefault(page_number, []).extend(
                    str(cell or "")
                    for row in payload
                    for cell in row
                    if cell is not None
                )
        source_audit_pages = 0
        source_audit_units = 0
        source_audit_covered = 0
        source_audit_missing_pages: list[int] = []
        source_audit_unverified_pages: list[int] = []
        for page_number in range(1, page_count + 1):
            if page_number in blank_pages:
                continue
            if page_number in ocr_required_pages:
                # 页面内 OCR 追加的图像/扫描内容不在原生文字层对账范围内；即使
                # 同页其它原生文字完整，也必须明确保留这部分未独立验证状态。
                source_audit_unverified_pages.append(page_number)
            source_text = native_source_text_by_page.get(page_number, "")
            if not source_text.strip():
                if page_number not in source_audit_unverified_pages:
                    source_audit_unverified_pages.append(page_number)
                continue
            source_audit_pages += 1
            covered, total = _audit_native_text_page(
                source_text,
                "\n".join(parsed_page_text.get(page_number, [])),
            )
            source_audit_covered += covered
            source_audit_units += total
            if total and covered < total:
                source_audit_missing_pages.append(page_number)
        source_text_audit = {
            "scope": "native_text_layer",
            "status": (
                "complete"
                if source_audit_units and not source_audit_missing_pages
                and not source_audit_unverified_pages
                else "partial"
            ),
            "audited_pages": source_audit_pages,
            "unverified_pages": source_audit_unverified_pages,
            "checked_units": source_audit_units,
            "covered_units": source_audit_covered,
            "missing_pages": source_audit_missing_pages,
            "coverage_rate": (
                round(source_audit_covered / source_audit_units, 4)
                if source_audit_units else None
            ),
            "sampled": False,
        }

        ast = DocumentAST(root=root, source_file=file_path, raw_text=raw_text)
        # §5.1 质量记录：OCR 兜底发生 → AST 置位（pipeline 据此给 chunk 打标，
        # indexer 汇总进 registry quality_issues，门禁按 ocr_triggered 审计）
        ast.ocr_required = ocr_required
        ast.ocr_attempted = ocr_attempted
        ast.ocr_triggered = ocr_pages > 0
        ast.ocr_pages = ocr_pages
        ast.ocr_page_limit = ocr_page_limit
        ast.ocr_page_budget = max_ocr_pages if ocr_required else 0
        ast.ocr_page_budget_source = budget_source if ocr_required else "not_needed"
        ast.ocr_failed_page_numbers = list(ocr_failed_page_numbers)
        ast.ocr_failed_pages = len(ocr_failed_page_numbers)
        ast.ocr_required_pages = list(ocr_required_pages)
        ast.ocr_required_regions = [
            {
                "page": page_number,
                "bbox": list(bbox) if bbox else None,
            }
            for page_number, bbox in ocr_required_regions
        ]
        ast.ocr_failed_regions = [
            {
                "page": page_number,
                "bbox": list(bbox) if bbox else None,
            }
            for page_number, bbox in ocr_failed_regions
        ]
        ast.pdf_page_count = page_count
        ast.pdf_pages_with_content = len({event[3] for event in ordered_events})
        ast.pdf_skipped_pages = len(skipped_pages)
        ast.pdf_skipped_page_numbers = list(skipped_pages)
        ast.pdf_blank_pages = sorted(blank_pages)
        ast.pdf_unparsed_content_pages = sorted(unparsed_content_pages)
        ast.pdf_vector_text_suspect_pages = sorted(vector_text_suspect_pages)
        ast.pdf_layout_fallback_pages = sorted(layout_fallback_pages)
        ast.pdf_source_text_audit = source_text_audit
        ast.pdf_image_count = image_count
        ast.pdf_image_pages = sorted(image_pages)
        ast.pdf_unparsed_visual_pages = sorted(unparsed_visual_pages)
        ast.pdf_table_detection_failed_pages = sorted(table_detection_failed_pages)
        ast.pdf_repaired = pdf_repaired
        if blank_pages:
            parse_issues.append({
                "code": "pdf_blank_pages",
                "severity": "info",
                "page_numbers": sorted(blank_pages),
                "detail": "已识别并排除真实空白页；空白页不计为内容缺失",
            })
        if layout_fallback_pages:
            parse_issues.append({
                "code": "pdf_layout_fallback",
                "severity": "info",
                "page_numbers": sorted(layout_fallback_pages),
                "detail": "复杂双栏页已使用本地几何布局排序；页码与 bbox 保留",
            })
        if vector_text_suspect_pages:
            parse_issues.append({
                "code": "pdf_vector_text_suspected",
                "severity": "info",
                "page_numbers": sorted(vector_text_suspect_pages),
                "detail": "文字层稀疏且矢量图形密集，已尝试整页 OCR",
            })
        if unparsed_content_pages:
            parse_issues.append({
                "code": "pdf_content_page_unparsed",
                "severity": "error",
                "page_numbers": sorted(unparsed_content_pages),
                "detail": "页面有可见内容，但解析与 OCR 后仍未产出正文或表格",
            })
        if source_audit_missing_pages:
            coverage_rate = source_text_audit["coverage_rate"]
            parse_issues.append({
                "code": "pdf_source_text_audit_gap",
                "severity": "error" if coverage_rate < 0.95 else "warn",
                "page_numbers": source_audit_missing_pages,
                "detail": (
                    "独立原生文字层→AST 对账发现缺失片段；"
                    f"已覆盖 {source_audit_covered}/{source_audit_units} 个片段"
                ),
            })
        if source_audit_unverified_pages:
            parse_issues.append({
                "code": "pdf_source_text_audit_unverified",
                "severity": "info",
                "page_numbers": source_audit_unverified_pages,
                "detail": "这些页面包含 OCR 或缺少原生文字层内容，未由原生文字对账覆盖；OCR 结果准确率未由此审计",
            })
        ast.parse_issues = parse_issues
        has_incomplete_issue = any(
            str(issue.get("severity") or "").lower()
            in {"warn", "warning", "error", "hard"}
            for issue in parse_issues
        )
        ast.parse_status = (
            "partial"
            if (
                has_incomplete_issue or skipped_pages or ocr_page_limit
                or ocr_failed_page_numbers or (ocr_required and not ocr_attempted)
            ) else "complete"
        )
        return ast

    def _ocr_fallback(
        self,
        file_path: str,
        targets: list[tuple[int, tuple[float, ...] | None]],
    ) -> tuple[
        list[tuple[str, float, int, tuple]],
        list[tuple[int, tuple[float, ...] | None]],
    ]:
        """渲染 OCR 页面或图像区域，返回文本、页码、bbox 和失败页码。

        页码为 1-based；图像裁剪保留其源 bbox，整页扫描兜底的 bbox 为空。
        任一目标区域失败都会记录所在页，质量门禁据此阻止不完整页面入库。
        """
        from backend.config import rag as rag_cfg
        from backend.rag.preprocessing.parser import ocr as ocr_mod
        items: list[tuple[str, float, int, tuple]] = []
        failed_targets: list[tuple[int, tuple[float, ...] | None]] = []
        try:
            ocr_provider = ocr_mod.get_ocr_model_identity().provider
        except Exception:
            ocr_provider = None
        base_dpi = max(int(rag_cfg.RAG_OCR_DPI), 72)
        retry_dpi = min(max(base_dpi + 100, 300), 400)
        render_dpis = [base_dpi]
        # 只为本地 OCR 的失败/空结果做一次高分辨率重试，避免云 OCR 的
        # 不确定计费请求被自动重复发送。页数上限在外层统一控制，重试不增加页数。
        if ocr_provider == "rapidocr" and retry_dpi > base_dpi:
            render_dpis.append(retry_dpi)
        doc = fitz.open(file_path)
        try:
            for page_number, bbox in targets:
                page_idx = page_number - 1
                recognized_text = ""
                last_error: Exception | None = None
                try:
                    clip = fitz.Rect(*bbox) if bbox else None
                    for attempt, dpi in enumerate(render_dpis):
                        try:
                            pix = doc[page_idx].get_pixmap(dpi=dpi, clip=clip)
                            text = ocr_mod.ocr_image(pix.tobytes("png"))
                            if text and text.strip():
                                recognized_text = text.strip()
                                break
                            last_error = None
                            if attempt + 1 < len(render_dpis):
                                logger.warning(
                                    f"[PdfParser][OCR] {file_path} 第 {page_number} 页"
                                    f"初次 OCR 未返回文本，将以 {render_dpis[attempt + 1]} DPI 重试"
                                )
                        except Exception as exc:
                            last_error = exc
                            if attempt + 1 < len(render_dpis):
                                logger.warning(
                                    f"[PdfParser][OCR] {file_path} 第 {page_number} 页"
                                    f"初次 OCR 失败，将以 {render_dpis[attempt + 1]} DPI 重试"
                                )
                    if recognized_text:
                        items.append((
                            recognized_text, 0.0, page_number,
                            tuple(bbox or ()),
                        ))
                    else:
                        failed_targets.append((page_number, bbox))
                        detail = (
                            f"{type(last_error).__name__}: {last_error}"
                            if last_error else "OCR 未返回文本"
                        )
                        logger.warning(
                            f"[PdfParser][OCR] {file_path} 第 {page_number} 页区域 OCR 失败: "
                            f"{detail}"
                        )
                except Exception as e:
                    failed_targets.append((page_number, bbox))
                    logger.warning(
                        f"[PdfParser][OCR] {file_path} 第 {page_number} 页区域 OCR 失败: "
                        f"{type(e).__name__}: {e}"
                    )
        finally:
            doc.close()
        return items, failed_targets

    def _extract_blocks(
        self, blocks: list, out: list, table_bboxes: list[tuple] | None = None,
        page_number: int = 0,
    ) -> None:
        """从 PyMuPDF blocks 抽取文本块 + 字号，按垂直位置合并段落。

        out 接收 (y_top, "text", (text, size, page_number, bbox), page_number, bbox)
        五元组，便于 parse() 与表格按 y 混排并保留 §5.2 可追溯信息。
        table_bboxes 非空时跳过与表格区域重叠的块（表格内容已由 find_tables 提取）。
        """
        table_bboxes = table_bboxes or []
        # type=0 是文本块，type=1 是图片（忽略）
        text_blocks = [b for b in blocks if b.get("type") == 0]
        # 按垂直位置排序（y0 升序）
        text_blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        current_lines: list[str] = []
        current_sizes: list[float] = []
        current_bbox: tuple = ()
        current_y_top: float = -1.0
        current_y_bottom: float = -1.0

        def _flush():
            nonlocal current_bbox, current_y_top
            if current_lines:
                out.append((
                    current_y_top,
                    "text",
                    (
                        "\n".join(current_lines),
                        max(current_sizes) if current_sizes else 0.0,
                        page_number,
                        current_bbox,
                    ),
                    page_number,
                    current_bbox,
                ))
                current_lines.clear()
                current_sizes.clear()
                current_bbox = ()
                current_y_top = -1.0

        for block in text_blocks:
            block_text = "\n".join(
                str(span.get("text") or "")
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
            block_bbox = tuple(
                round(float(value), 1) for value in block.get("bbox", ())
            )
            horizontal_gap = bool(
                current_bbox and len(block_bbox) == 4
                and (
                    block_bbox[0] > current_bbox[2] + 12
                    or current_bbox[0] > block_bbox[2] + 12
                )
            )
            if current_lines and (
                (block_y_top - current_y_bottom) > _PARAGRAPH_MERGE_GAP
                or size_jump or horizontal_gap
            ):
                # 间距超过阈值或字号突变 → 当前段落结束
                _flush()

            if not current_lines:
                current_bbox = block_bbox
                current_y_top = float(block_y_top)
            elif len(block_bbox) == 4 and len(current_bbox) == 4:
                current_bbox = (
                    min(current_bbox[0], block_bbox[0]),
                    min(current_bbox[1], block_bbox[1]),
                    max(current_bbox[2], block_bbox[2]),
                    max(current_bbox[3], block_bbox[3]),
                )
            current_lines.append(block_text)
            current_sizes.append(block_size)
            current_y_bottom = block["bbox"][3]

        _flush()
