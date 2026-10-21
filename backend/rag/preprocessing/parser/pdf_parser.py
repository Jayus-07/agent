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

# 版面分析增强（可选）状态。
#
# 2026-09-15 核实：PyPI 包 `pymupdf-layout` 不提供顶层模块 `pymupdf_layout`
# ——它把代码装进 PyMuPDF 插件命名空间 `pymupdf.layout`（`import pymupdf.layout`
# 即通过 monkey-patch `pymupdf._get_layout` 完成激活）。因此原实现
# `import pymupdf_layout` 恒失败、`pymupdf_layout.analyze(page)` 也是不存在的
# API（真实入口是 `pymupdf.layout.DocumentLayoutAnalyzer` / `activate()`）：
# 该增强分支从未生效过，一直走 `get_text("dict")` 原生块。
#
# 这里保持显式 False（= 现状），因为激活会改变块结构 → 影响切分与既有索引，
# 需连同解析质量评估 + 重索引一起做。正确接线姿势（后续任务）：
#     import pymupdf.layout   # 激活版面分析（副作用式）
#     # 再按 pymupdf.layout.activate() 的集成方式取块，不要调用不存在的 analyze()
_HAS_LAYOUT = False

# 依赖声明保留在 pyproject（pymupdf-layout）——装好≠接线，避免"包已装但路径静默失效"。

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.config.rag import RAG_OCR_MIN_TEXT_CHARS
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
        skipped_pages: list[int] = []  # 1-based 页码
        image_pages: set[int] = set()
        image_regions_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
        large_image_regions_by_page: dict[
            int, list[tuple[float, float, float, float]]
        ] = {}
        image_count = 0
        unparsed_visual_pages: set[int] = set()
        table_detection_failed_pages: set[int] = set()
        parse_issues: list[dict] = []
        try:
            for page_idx in range(len(doc)):
                try:
                    page = doc[page_idx]
                    # 版面分析未接线（见文件头 _HAS_LAYOUT 说明）——现阶段
                    # 一律走 PyMuPDF 原生 dict 块；接入时在此处替换为
                    # pymupdf.layout 的真实调用，别复活不存在的 analyze()。
                    blocks = page.get_text("dict")["blocks"]
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
                        for t in page.find_tables().tables:
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
                    events.sort(key=lambda x: x[0])
                    page_events[page_idx] = events
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
        total_text_chars = sum(native_page_chars)

        # OCR 按页面和图像区域判断，避免整篇文字层充足的混合 PDF 掩盖扫描页。
        # 低文本量扫描页识别页内图片；文字量充足但包含大面积图片时仅裁剪图片
        # 区域，避免对原生文字做整页 OCR。纯扫描页继续逐页兜底。
        ocr_required_regions: list[tuple[int, tuple[float, ...] | None]] = []
        for page_number, page_chars in enumerate(native_page_chars, start=1):
            page_images = image_regions_by_page.get(page_number, [])
            if total_text_chars == 0:
                targets = page_images or [None]
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

            max_ocr_pages = max(int(rag_cfg.RAG_OCR_MAX_PAGES), 0)
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
                    page_events[page_number - 1].sort(key=lambda event: event[0])
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
        ast = DocumentAST(root=root, source_file=file_path, raw_text=raw_text)
        # §5.1 质量记录：OCR 兜底发生 → AST 置位（pipeline 据此给 chunk 打标，
        # indexer 汇总进 registry quality_issues，门禁按 ocr_triggered 审计）
        ast.ocr_required = ocr_required
        ast.ocr_attempted = ocr_attempted
        ast.ocr_triggered = ocr_pages > 0
        ast.ocr_pages = ocr_pages
        ast.ocr_page_limit = ocr_page_limit
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
        ast.pdf_image_count = image_count
        ast.pdf_image_pages = sorted(image_pages)
        ast.pdf_unparsed_visual_pages = sorted(unparsed_visual_pages)
        ast.pdf_table_detection_failed_pages = sorted(table_detection_failed_pages)
        ast.pdf_repaired = pdf_repaired
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
        doc = fitz.open(file_path)
        try:
            for page_number, bbox in targets:
                page_idx = page_number - 1
                try:
                    clip = fitz.Rect(*bbox) if bbox else None
                    pix = doc[page_idx].get_pixmap(
                        dpi=rag_cfg.RAG_OCR_DPI,
                        clip=clip,
                    )
                    text = ocr_mod.ocr_image(pix.tobytes("png"))
                    if text and text.strip():
                        items.append((
                            text.strip(), 0.0, page_number,
                            tuple(bbox or ()),
                        ))
                    else:
                        failed_targets.append((page_number, bbox))
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
        current_y_bottom: float = -1.0

        def _flush(y_top: float):
            nonlocal current_bbox
            if current_lines:
                out.append((
                    y_top,
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
            if current_lines and (
                (block_y_top - current_y_bottom) > _PARAGRAPH_MERGE_GAP or size_jump
            ):
                # 间距超过阈值或字号突变 → 当前段落结束
                _flush(block_y_top)

            current_lines.append(block_text)
            current_sizes.append(block_size)
            current_bbox = tuple(round(v, 1) for v in block.get("bbox", ()))
            current_y_bottom = block["bbox"][3]

        _flush(1e9)
