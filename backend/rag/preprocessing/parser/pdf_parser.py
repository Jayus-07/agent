"""PdfParser — PyMuPDF 解析 PDF → Raw AST。

Phase 2 简化策略：
- 不识别标题（无章节结构，整篇当无结构长段落）
- 段落合并：同一页面、相邻、间距 < 阈值 视为同一段
- 表格识别：PyMuPDF page.get_text("dict") 的 type=1 块 → table
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

        raw_items: list[tuple[str, float]] = []  # (文本, 字号)
        skipped_pages: list[int] = []
        try:
            for page_idx in range(len(doc)):
                try:
                    page = doc[page_idx]
                    blocks = page.get_text("dict")["blocks"]
                    self._extract_blocks(blocks, raw_items)
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

        # 标题启发式：统计正文字号（众数），识别标题 → section，其余 → paragraph
        body_size = _body_font_size([size for _, size in raw_items])
        root = DocumentNode(type="section", text="", level=0)
        current_section = root
        heading_count = 0
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

        if heading_count:
            logger.info(
                f"[PdfParser] {file_path} 识别 {heading_count} 个标题"
                f"（正文字号 {body_size}）"
            )

        raw_text = "\n".join(text for text, _ in raw_items)
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)

    def _extract_blocks(
        self, blocks: list, out: list[tuple[str, float]]
    ) -> None:
        """从 PyMuPDF blocks 抽取文本块 + 字号，按垂直位置合并段落。"""
        # type=0 是文本块，type=1 是图片
        text_blocks = [b for b in blocks if b.get("type") == 0]
        # 按垂直位置排序（y0 升序）
        text_blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        current_lines: list[str] = []
        current_sizes: list[float] = []
        current_y_bottom: float = -1.0

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
                out.append((
                    "\n".join(current_lines),
                    max(current_sizes) if current_sizes else 0.0,
                ))
                current_lines = []
                current_sizes = []

            current_lines.append(block_text)
            current_sizes.append(block_size)
            current_y_bottom = block["bbox"][3]

        if current_lines:
            out.append((
                "\n".join(current_lines),
                max(current_sizes) if current_sizes else 0.0,
            ))
