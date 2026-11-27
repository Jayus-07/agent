"""RAG Upload 质检与失败信息摘要明细。"""
from __future__ import annotations

import json
import os

from backend.app.api.routes.rag_upload_subroutes.base import *

def _quality_failure_summary(record: dict) -> str:
    """把质量报告里的 OCR、原文核对和表格缺口压成管理端可读说明。"""
    parsing = record.get("parsing") or {}
    coverage = record.get("coverage") or {}
    details: list[str] = []

    required_pages = parsing.get("ocr_required_pages") or []
    required_count = len(required_pages) if isinstance(required_pages, list) else 0
    try:
        processed_pages = max(0, int(parsing.get("ocr_pages") or 0))
        page_budget = max(0, int(parsing.get("ocr_page_budget") or 0))
    except (TypeError, ValueError):
        processed_pages, page_budget = 0, 0
    if required_count and processed_pages < required_count:
        details.append(
            f"OCR 已处理 {processed_pages}/{required_count} 页，"
            f"页数上限 {page_budget} 页，尚有 {required_count - processed_pages} 页未处理"
        )

    failed_pages = parsing.get("ocr_failed_page_numbers") or []
    if isinstance(failed_pages, list) and failed_pages:
        pages = ", ".join(str(page) for page in failed_pages[:10])
        suffix = f"等{len(failed_pages)}页" if len(failed_pages) > 10 else "页"
        details.append(f"OCR 识别失败：第 {pages}{suffix}")

    audit_gaps = [
        issue for issue in parsing.get("parse_issues") or []
        if isinstance(issue, dict)
        and issue.get("code") == "pdf_source_text_audit_gap"
    ]
    audit_pages = sorted({
        int(page)
        for issue in audit_gaps
        for page in issue.get("page_numbers") or []
        if str(page).isdigit()
    })
    if audit_pages:
        pages = ", ".join(str(page) for page in audit_pages[:10])
        suffix = f"等{len(audit_pages)}页" if len(audit_pages) > 10 else "页"
        details.append(f"PDF 原文核对有缺口：第 {pages}{suffix}")

    try:
        uncovered = max(0, int(coverage.get("uncovered_count") or 0))
        missing_rows = max(0, int(coverage.get("table_rows_uncovered") or 0))
        missing_cells = max(0, int(coverage.get("table_cells_uncovered") or 0))
    except (TypeError, ValueError):
        uncovered, missing_rows, missing_cells = 0, 0, 0
    if uncovered or missing_rows or missing_cells:
        details.append(
            f"切分缺 {uncovered} 处，表格少 {missing_rows} 行、"
            f"{missing_cells} 个单元格"
        )
        samples = coverage.get("uncovered_samples") or []
        for sample in samples:
            row_samples = sample.get("missing_row_samples") or []
            if not row_samples:
                continue
            cells = [str(cell).strip()[:40] for cell in row_samples[0].get("cells", [])[:4]]
            if cells:
                page = sample.get("page")
                location = f"第 {page} 页" if page else "页码未记录"
                details.append(f"缺失表格行样例（{location}）：{' / '.join(cells)}")
            break
    return "；".join(details)


def _load_quality_failure_details(
    keys: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """只读取当前页质量失败项的报告，不扫描整个资料目录。"""
    from backend.config.database import RAG_DATA_DIR

    root = os.path.realpath(os.path.join(RAG_DATA_DIR, "quality_records"))
    result: dict[tuple[str, str], str] = {}
    for kb_id, doc_id in keys:
        safe_kb = str(kb_id or "default").strip().replace("\\", "_").replace("/", "_")
        safe_doc = str(doc_id or "").strip().replace("\\", "_").replace("/", "_")
        safe_kb = safe_kb.replace("..", "_") or "default"
        safe_doc = safe_doc.replace("..", "_")
        if not safe_doc:
            continue
        path = os.path.realpath(os.path.join(root, safe_kb, f"{safe_doc}.json"))
        try:
            if (
                os.path.commonpath([root, path]) != root
                # 长 PDF 的逐页核对记录可能超过 2 MiB；保留有限上限，
                # 同时覆盖当前 501 页样本的约 3.95 MiB 报告。
                or os.path.getsize(path) > 5 * 1024 * 1024
            ):
                continue
            with open(path, encoding="utf-8") as quality_file:
                record = json.load(quality_file)
            summary = facade._quality_failure_summary(record)
        except (OSError, ValueError, TypeError):
            continue
        if summary:
            result[(safe_kb, safe_doc)] = summary
    return result


for _fn in [
    _quality_failure_summary,
    _load_quality_failure_details
]:
    facade.register(_fn.__name__, _fn)
