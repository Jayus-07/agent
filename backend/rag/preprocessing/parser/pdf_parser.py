"""PdfParser — PyMuPDF 解析 PDF → Raw AST。

Phase 2 简化策略：
- 不识别标题（无章节结构，整篇当无结构长段落）
- 段落合并：同一页面、相邻、间距 < 阈值 视为同一段
- 表格识别：PyMuPDF page.get_text("dict") 的 type=1 块 → table
- 可观测：单页失败 → log warning + 计数器，最终汇总报告
"""
from __future__ import annotations

import pymupdf as fitz  # PyMuPDF；用 pymupdf 别名 fitz 消除 1.24+ deprecation warning

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.parser.base import BaseDocumentParser
from backend.shared.logger import logger

_PARAGRAPH_MERGE_GAP = 15  # PyMuPDF 文本块垂直间距（point）


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

        raw_lines: list[str] = []
        skipped_pages: list[int] = []
        try:
            for page_idx in range(len(doc)):
                try:
                    page = doc[page_idx]
                    blocks = page.get_text("dict")["blocks"]
                    self._extract_blocks(blocks, raw_lines)
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

        root = DocumentNode(type="section", text="", level=0)
        for line in raw_lines:
            if line.strip():
                root.children.append(
                    DocumentNode(type="paragraph", text=line.strip())
                )

        raw_text = "\n".join(raw_lines)
        return DocumentAST(root=root, source_file=file_path, raw_text=raw_text)

    def _extract_blocks(
        self, blocks: list, out: list[str]
    ) -> None:
        """从 PyMuPDF blocks 抽取文本块，按垂直位置合并段落。"""
        # type=0 是文本块，type=1 是图片
        text_blocks = [b for b in blocks if b.get("type") == 0]
        # 按垂直位置排序（y0 升序）
        text_blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        current_lines: list[str] = []
        current_y_bottom: float = -1.0

        for block in text_blocks:
            block_text = "\n".join(
                span["text"]
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()
            if not block_text:
                continue

            block_y_top = block["bbox"][1]
            if current_lines and (block_y_top - current_y_bottom) > _PARAGRAPH_MERGE_GAP:
                # 间距超过阈值 → 当前段落结束
                out.append("\n".join(current_lines))
                current_lines = []

            current_lines.append(block_text)
            current_y_bottom = block["bbox"][3]

        if current_lines:
            out.append("\n".join(current_lines))
