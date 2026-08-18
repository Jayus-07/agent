"""切分流水线编排 — Parser → Cleaner → Analyzer → Router → Strategy。"""
from __future__ import annotations

import os
from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.metadata import classify_doc_type
from backend.rag.preprocessing.parser import parse_file
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer
from backend.rag.preprocessing.chunking import ChunkStrategyRouter
from backend.rag.preprocessing.ast import walk
from backend.shared.logger import logger

_SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".xlsx"}


def parse_and_chunk(file_path: str, doc_type_hint: str = "") -> List[Document]:
    """单文件完整切分流水线。返回 leaf + parent 双粒度 chunk。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _SUPPORTED_EXTS:
        logger.warning(f"[ChunkPipeline] 暂不支持 {ext}（Phase 2），跳过: {file_path}")
        return []
    raw_ast = parse_file(file_path)

    # P0-3: 无文字层 PDF（扫描件/纯图片）友好报错，避免 0-chunk 假成功。
    # 图片内容当前被完全忽略（无 OCR/Vision），若 PDF 有页面但提取不到任何文字，
    # 后续索引必然产出 0 chunks 并失败——提前给出明确原因。
    if ext == ".pdf" and not (raw_ast.raw_text or "").strip():
        try:
            import pymupdf as fitz
            doc = fitz.open(file_path)
            page_count = len(doc)
            img_blocks = 0
            for pi in range(page_count):
                page = doc[pi]
                img_blocks += sum(
                    1 for b in page.get_text("dict").get("blocks", [])
                    if b.get("type") == 1
                )
            doc.close()
            hint = (
                f"该 PDF 共 {page_count} 页但无文字层"
                + (f"（含 {img_blocks} 个图片块，可能为扫描件/纯图片）" if img_blocks else "（可能为图片型 PDF）")
                + "，当前系统不支持 OCR/图片理解，请上传含可复制文字的 PDF"
            )
        except Exception as e:
            hint = f"该 PDF 无法提取文字（可能为损坏文件或扫描件）: {type(e).__name__}"
        raise ValueError(hint)

    # 结构安全清洗：清洗每个节点文本，保留结构
    cleaner = DocumentCleaner()
    source_type = "pdf" if file_path.lower().endswith(".pdf") else "text"
    for node in walk(raw_ast.root):
        if node.type not in ("table",):  # table 的 rows 不在 text 清洗范围
            node.text = cleaner.clean(node.text, source_type=source_type).text

    normalized_ast, report = StructureAnalyzer().analyze(raw_ast)

    doc_type = doc_type_hint or classify_doc_type(raw_ast.raw_text, filename=file_path, file_path=file_path)
    strategy = ChunkStrategyRouter().route(doc_type, report)
    logger.info(
        f"[ChunkPipeline] {file_path} doc_type={doc_type} "
        f"completeness={report.completeness} → {strategy.__class__.__name__}"
    )
    return strategy.split(normalized_ast, file_path)
