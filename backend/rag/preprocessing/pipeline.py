"""切分流水线编排 — Parser → Cleaner → Analyzer → Router → Strategy。"""
from __future__ import annotations

import os
import time
from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.metadata import classify_doc_type
from backend.rag.preprocessing.parser import parse_file, PARSABLE_EXTS
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter, _doc_title, coverage_audit, stamp_chunk_pages,
)
from backend.rag.preprocessing.ast import walk
from backend.shared.logger import logger

# F6: 从解析器注册表派生（单一来源），不再硬编码
_SUPPORTED_EXTS = set(PARSABLE_EXTS)


def parse_and_chunk(file_path: str, doc_type_hint: str = "") -> List[Document]:
    """单文件完整切分流水线。返回 leaf + parent 双粒度 chunk。

    兼容包装：需要质量记录上下文（AST/清洗统计）的调用方（indexer 质量门禁）
    请用 parse_and_chunk_full。
    """
    chunks, _qc = parse_and_chunk_full(file_path, doc_type_hint)
    return chunks


def parse_and_chunk_full(file_path: str, doc_type_hint: str = "") -> tuple[List[Document], dict]:
    """parse_and_chunk 的完整版：额外返回 §5.1 质量门禁所需上下文。

    qc 字典携带 raw_ast（清洗后节点已含可追溯字段）/normalized_ast/completeness
    报告/doc_type/strategy 名，供 quality_gate.build_quality_record 消费；
    普通调用方忽略之即可，行为与旧 parse_and_chunk 完全一致。
    """
    pipeline_started = time.monotonic()
    stage_ms: dict[str, int] = {}
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _SUPPORTED_EXTS:
        logger.warning(f"[ChunkPipeline] 暂不支持 {ext}（Phase 2），跳过: {file_path}")
        return [], {}
    stage_started = time.monotonic()
    raw_ast = parse_file(file_path)
    stage_ms["parse_file_ms"] = round((time.monotonic() - stage_started) * 1000)

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
    stage_started = time.monotonic()
    cleaner = DocumentCleaner()
    source_type = "pdf" if file_path.lower().endswith(".pdf") else "text"
    for node in walk(raw_ast.root):
        if node.type not in ("table",):  # table 的 rows 不在 text 清洗范围
            node.raw_text = node.text
            # structured=True：节点文本已是解析器切好的语义单元，没有版面
            # 上下文，禁用依赖"前后空行"的图注启发式（2026-10-18：该启发式
            # 曾把供应商规范 5 个内容叶节点 / 员工手册章节标题清空）。
            clean_result = cleaner.clean(
                node.text, source_type=source_type, structured=True,
            )
            node.text = clean_result.text
            node.cleaning_operations = list(clean_result.changes)
            node.cleaning_dropped = list(clean_result.dropped)

    # §P0-D 边界一：Raw AST → 清洗输出 的完整性对账。清洗把节点清空会让
    # coverage_audit 的分母一起缩水（假通过），这里独立核对清洗前后内容。
    from backend.rag.preprocessing.quality_gate import cleaning_coverage_audit

    cleaning_coverage = cleaning_coverage_audit(raw_ast)
    if cleaning_coverage.get("uncovered_count"):
        logger.warning(
            f"[ChunkPipeline] {file_path} 清洗内容丢失: "
            f"{cleaning_coverage['uncovered_count']}/"
            f"{cleaning_coverage['audited_node_count']} 个节点有内容未保留"
        )
    stage_ms["clean_ms"] = round((time.monotonic() - stage_started) * 1000)

    stage_started = time.monotonic()
    normalized_ast, report = StructureAnalyzer().analyze(raw_ast)
    stage_ms["structure_analysis_ms"] = round((time.monotonic() - stage_started) * 1000)

    stage_started = time.monotonic()
    doc_type = doc_type_hint or classify_doc_type(raw_ast.raw_text, filename=file_path, file_path=file_path)
    # P1（2026-10-13）：route_with_decision 产出可审计决策记录（结构信号/
    # 策略版本/降级原因/处理范围），随 qc 进质量记录与 Trace
    strategy, route_decision = ChunkStrategyRouter().route_with_decision(doc_type, report)
    logger.info(
        f"[ChunkPipeline] {file_path} doc_type={doc_type} "
        f"completeness={report.completeness} → {strategy.__class__.__name__}"
        + (f" (degrade={route_decision.degrade_reason})" if route_decision.degrade_reason else "")
    )
    stage_ms["classification_routing_ms"] = round((time.monotonic() - stage_started) * 1000)

    stage_started = time.monotonic()
    chunks = strategy.split(normalized_ast, file_path)
    document_title = _doc_title(normalized_ast)
    for chunk in chunks:
        chunk.metadata.setdefault("document_title", document_title)
    stage_ms["split_ms"] = round((time.monotonic() - stage_started) * 1000)

    stage_started = time.monotonic()
    # 原文定位（P0）：叶子页码（PDF §5.2）回映射进 chunk metadata["pages"]，
    # 供来源卡「第 X 页」展示与后续原文预览定位；单点收口，策略零改动
    stamp_chunk_pages(chunks, normalized_ast)
    # §5.1 质量记录：扫描件 OCR 触发 → 打进 chunk metadata（Chroma 只收标量，
    # 沿用 chunks_truncated 的字符串标记惯例），indexer 汇总进 quality_issues
    if getattr(raw_ast, "ocr_triggered", False):
        for c in chunks:
            c.metadata["ocr_triggered"] = "true"
            c.metadata["ocr_pages"] = int(getattr(raw_ast, "ocr_pages", 0) or 0)
    # P0-02 覆盖对账：源叶子 → 输出 chunk 的覆盖率（uncovered 进入
    # quality_issues 可追溯，不让策略静默丢内容）
    coverage = coverage_audit(normalized_ast, chunks)
    if coverage.get("uncovered_count"):
        logger.warning(
            f"[ChunkPipeline] {file_path} 覆盖缺口: "
            f"{coverage['uncovered_count']}/{coverage['leaf_total']} 叶子未入 chunk"
        )
    stage_ms["chunk_postprocess_ms"] = round((time.monotonic() - stage_started) * 1000)
    stage_ms["total_ms"] = round((time.monotonic() - pipeline_started) * 1000)
    qc = {
        "raw_ast": raw_ast,
        "normalized_ast": normalized_ast,
        "completeness": report,
        "doc_type": doc_type,
        "strategy_name": strategy.__class__.__name__,
        "route_decision": route_decision.as_dict(),
        "coverage": coverage,
        "cleaning_coverage": cleaning_coverage,
        "stage_ms": stage_ms,
    }
    return chunks, qc
