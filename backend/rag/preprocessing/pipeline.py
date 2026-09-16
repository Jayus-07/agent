"""切分流水线编排 — Parser → Cleaner → Analyzer → Router → Strategy。"""
from __future__ import annotations

import os
from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.metadata import classify_doc_type
from backend.rag.preprocessing.parser import parse_file, PARSABLE_EXTS
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer
from backend.rag.preprocessing.chunking import ChunkStrategyRouter
from backend.rag.preprocessing.ast import walk
from backend.shared.logger import logger

# F6: 从解析器注册表派生（单一来源），不再硬编码
_SUPPORTED_EXTS = set(PARSABLE_EXTS)


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
            )
        except Exception as e:
            hint = f"该 PDF 无法提取文字（可能为损坏文件或扫描件）: {type(e).__name__}"
        # OCR 兜底已在解析器内部尝试过（走到这里说明 OCR 不可用或产出为空），
        # 报错信息按 OCR 实际配置给出可操作指引，不再笼统说"不支持"
        from backend.rag.preprocessing.parser import ocr as _ocr_mod
        if _ocr_mod.ocr_available():
            hint += "；OCR 已启用但未能识别出文本（页面可能全为失败页），请检查文档内容或 OCR 日志"
        else:
            hint += "；当前未启用可用的 OCR 供应商（检查 RAG_OCR_PROVIDER 及 API Key 配置），请上传含可复制文字的 PDF"
        raise ValueError(hint)

    # 结构安全清洗：清洗每个节点文本，保留结构
    # R-P0-3 原文可追溯：清洗前留存 raw_text + 清洗操作留痕（不改清洗行为本身）
    cleaner = DocumentCleaner()
    source_type = "pdf" if file_path.lower().endswith(".pdf") else "text"
    for node in walk(raw_ast.root):
        if node.type not in ("table",):  # table 的 rows 不在 text 清洗范围
            node.raw_text = node.text
            clean_result = cleaner.clean(node.text, source_type=source_type)
            node.text = clean_result.text
            node.cleaning_operations = list(clean_result.changes)

    normalized_ast, report = StructureAnalyzer().analyze(raw_ast)

    doc_type = doc_type_hint or classify_doc_type(raw_ast.raw_text, filename=file_path, file_path=file_path)
    strategy = ChunkStrategyRouter().route(doc_type, report)
    logger.info(
        f"[ChunkPipeline] {file_path} doc_type={doc_type} "
        f"completeness={report.completeness} → {strategy.__class__.__name__}"
    )
    chunks = strategy.split(normalized_ast, file_path)
    # §5.1 质量记录：扫描件 OCR 触发 → 打进 chunk metadata（Chroma 只收标量，
    # 沿用 chunks_truncated 的字符串标记惯例），indexer 汇总进 quality_issues
    if getattr(raw_ast, "ocr_triggered", False):
        for c in chunks:
            c.metadata["ocr_triggered"] = "true"
            c.metadata["ocr_pages"] = int(getattr(raw_ast, "ocr_pages", 0) or 0)
    return chunks
