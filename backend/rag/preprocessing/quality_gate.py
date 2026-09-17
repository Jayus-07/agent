"""§5.1–5.3 质量门禁（R3，2026-09-17）。

三件事：
  §5.1 每文档质量记录：文件大小/SHA256/解析节点数/清洗前后字符数/叶子数/
       表格行列数/leaf+parent 数/过滤数与原因/截断数/OCR 触发/降级标记/
       重试失败记录 —— 全量落 data/quality_records/{kb}/{doc_id}.json。
  §5.2 原文可追溯：node_id/doc_id/source_range/page_number/bbox/content_hash
       进质量 JSON；URL/邮箱/编号/金额/日期无依据丢失 → 异常留痕。
  §5.3 类型化校验：按格式校验字符量/关键节点/行列表头；截断/降级/大量过滤
       标记质量异常（写 quality_issues，可审计）；硬异常（0 叶子/0 字符）
       抛错 → 文档不得置 active。

设计约束：记录 JSON 只存节点摘要（不存全文），体积有界；门禁故障不得阻断
索引主流程（校验硬异常除外——那是业务语义本身）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from backend.rag.preprocessing.ast import LEAF_TYPES, DocumentAST, walk
from backend.shared.logger import logger

QUALITY_RECORD_SCHEMA_VERSION = "1.0"

# §5.3 文本类格式字符量下限：低于此值视为解析异常（与 RAG_OCR_MIN_TEXT_CHARS
# 无关——那是 OCR 触发阈值，这是"解析产出是否为空壳"的门禁）
MIN_CLEANED_CHARS = 20

# §5.3 清洗前后字符比合理区间：清洗只做规范化（控制字符/全半角/HTML 剥离），
# 不应暴增（>3x，疑似解析重复）或暴减（<0.1x，疑似清洗吞内容）
RAW_CLEAN_RATIO_RANGE = (0.1, 3.0)

# §5.3 大量过滤阈值：被过滤 chunk 占比超过此值 → 质量异常留痕
HEAVY_FILTER_RATIO = 0.5

# §5.2 值丢失检测的正则（URL/邮箱/日期/长数字串/金额）
_VALUE_PATTERNS: dict[str, re.Pattern] = {
    "url": re.compile(r"https?://[^\s\u4e00-\u9fff]+", re.IGNORECASE),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "date": re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}"),
    "number": re.compile(r"\d{4,}(?:\.\d+)?"),  # 长数字串（编号/金额数值部分）
}


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _node_summary(ast: DocumentAST) -> list[dict]:
    """§5.2 节点级可追溯摘要（preorder 序号即确定性 node_id）。"""
    nodes: list[dict] = []
    for i, node in enumerate(walk(ast.root)):
        nodes.append({
            "node_id": f"n{i}",
            "type": node.type,
            "source_range": list(node.source_range or (0, 0)),
            "page_number": getattr(node, "page_number", 0) or None,
            "bbox": list(node.bbox) if getattr(node, "bbox", None) else None,
            "content_hash": _content_hash(node.text),
            "chars": len(node.text),
            "cleaned": bool(node.cleaning_operations),
        })
    return nodes


def _value_loss_check(raw_text: str, cleaned_text: str) -> list[dict]:
    """§5.2：URL/邮箱/日期/长数字串 在清洗后不得无依据丢失。

    返回异常列表（warn 级——清洗可能合法规范化全半角，误报人工复核）。
    """
    anomalies: list[dict] = []
    for name, pat in _VALUE_PATTERNS.items():
        lost = set(pat.findall(raw_text or "")) - set(pat.findall(cleaned_text or ""))
        if lost:
            samples = sorted(lost)[:3]
            anomalies.append({
                "check": "value_loss",
                "value_type": name,
                "severity": "warn",
                "detail": f"清洗后丢失 {len(lost)} 个 {name}，样例: {samples}",
            })
    return anomalies


def build_quality_record(
    *,
    file_path: str,
    doc_id: str,
    kb_id: str,
    raw_ast: DocumentAST,
    chunks: list,
    file_size: int,
    file_hash: str,
    doc_type: str = "",
    strategy_name: str = "",
    completeness: Any = None,
    filtered_details: list[dict] | None = None,
    truncated: bool = False,
    ocr_triggered: bool = False,
    ocr_pages: int = 0,
    index_errors: list[str] | None = None,
) -> dict:
    """组装 §5.1/§5.2 质量记录（纯函数，不做校验不落盘）。"""
    node_summaries = _node_summary(raw_ast)
    leaf_nodes = [n for n in walk(raw_ast.root) if n.type in LEAF_TYPES]
    section_count = sum(1 for n in walk(raw_ast.root) if n.type == "section")
    table_nodes = [n for n in walk(raw_ast.root) if n.type == "table"]

    raw_chars = len(raw_ast.raw_text or "")
    cleaned_chars = sum(len(n.text) for n in leaf_nodes)

    table_rows = sum(len(n.rows or []) for n in table_nodes)
    table_cols = max(
        (max((len(r) for r in (n.rows or [])), default=0) for n in table_nodes),
        default=0,
    )

    chunk_meta = [ch.metadata or {} for ch in chunks]
    parent_count = sum(1 for m in chunk_meta if m.get("granularity") == "parent")
    leaf_chunk_count = len(chunks) - parent_count

    filtered = filtered_details or []
    reason_breakdown: dict[str, int] = {}
    for d in filtered:
        r = str(d.get("reason", "unknown"))
        reason_breakdown[r] = reason_breakdown.get(r, 0) + 1

    # 降级标记：OCR 兜底（识别路径降级于原生文字层）。§5.1 要求降级可审计。
    degraded = ocr_triggered

    record: dict = {
        "schema_version": QUALITY_RECORD_SCHEMA_VERSION,
        "doc_id": doc_id,
        "kb_id": kb_id,
        "source_file": os.path.basename(file_path),
        "file_path": file_path,
        # §5.1 文件大小 / SHA256
        "file_size": file_size,
        "file_sha256": file_hash,
        "format": os.path.splitext(file_path)[1].lower().lstrip("."),
        "doc_type": doc_type,
        "chunking_strategy": strategy_name,
        "completeness": getattr(completeness, "completeness", None) if completeness is not None else None,
        # §5.1 解析统计
        "parsing": {
            "node_count": len(node_summaries),
            "section_count": section_count,
            "leaf_count": len(leaf_nodes),
            "table_count": len(table_nodes),
            "table_total_rows": table_rows,
            "table_max_cols": table_cols,
            "ocr_triggered": ocr_triggered,
            "ocr_pages": ocr_pages,
            "degraded": degraded,
            "degrade_reason": "ocr_fallback" if degraded else "",
        },
        # §5.1 清洗统计（R-P0-3 raw_text 留痕的量化面）
        "cleaning": {
            "raw_chars": raw_chars,
            "cleaned_chars": cleaned_chars,
            "nodes_cleaned": sum(1 for n in node_summaries if n["cleaned"]),
        },
        # §5.1 chunk 统计（leaf+parent 双粒度）
        "chunking": {
            "chunk_count": len(chunks),
            "leaf_chunk_count": leaf_chunk_count,
            "parent_chunk_count": parent_count,
            "truncated": truncated,
            "filtered_count": len(filtered),
            "filtered_reasons": reason_breakdown,
        },
        # §5.1 重试失败记录（本轮 per-file 异常；历史失败见 registry status）
        "index_errors": list(index_errors or []),
        # §5.2 节点级可追溯
        "traceability": {"nodes": node_summaries},
        # §5.3 校验结果（run_typed_validation 填充）
        "validation": {"checks": [], "anomalies": []},
    }
    return record


def run_typed_validation(record: dict, raw_ast: DocumentAST) -> list[dict]:
    """§5.3 类型化校验：按格式校验，返回异常列表并写回 record["validation"]。

    异常分两级：
      error → 硬异常，调用方必须让文档进 failed（不得伪装 active）；
      warn  → 软异常，留痕 quality_issues 供人工复核。
    """
    fmt = record.get("format", "")
    checks: list[dict] = []
    anomalies: list[dict] = []

    def _check(name: str, passed: bool, detail: str = "", severity: str = "error") -> None:
        checks.append({"check": name, "pass": passed, "detail": detail})
        if not passed:
            anomalies.append({
                "check": name, "severity": severity, "detail": detail,
            })

    parsing = record["parsing"]
    cleaning = record["cleaning"]
    chunking = record["chunking"]
    tabular = fmt in ("xlsx", "xls", "csv")

    if tabular:
        # §5.3 XLSX/CSV：行列/表头校验（表头 = 首行，要求行列齐备）
        _check("table_present", parsing["table_count"] >= 1,
               f"table_count={parsing['table_count']}")
        _check("table_rows", parsing["table_total_rows"] >= 1,
               f"rows={parsing['table_total_rows']}")
        _check("table_cols", parsing["table_max_cols"] >= 1,
               f"cols={parsing['table_max_cols']}")
    else:
        # §5.3 PDF/DOCX/MD/TXT：字符量 + 关键节点
        _check("cleaned_chars", cleaning["cleaned_chars"] >= MIN_CLEANED_CHARS,
               f"cleaned_chars={cleaning['cleaned_chars']} < {MIN_CLEANED_CHARS}")
        _check("leaf_nodes", parsing["leaf_count"] >= 1,
               f"leaf_count={parsing['leaf_count']}")
        if cleaning["raw_chars"] > 0:
            ratio = cleaning["cleaned_chars"] / cleaning["raw_chars"]
            lo, hi = RAW_CLEAN_RATIO_RANGE
            _check("clean_ratio", lo <= ratio <= hi,
                   f"cleaned/raw={ratio:.3f} 超出 [{lo}, {hi}]（疑似解析重复或清洗吞内容）",
                   severity="warn")

    # 表格切分结果校验：table 节点必须产出非空文本
    empty_tables = [
        n.text[:30] for n in walk(raw_ast.root)
        if n.type == "table" and not (n.text or "").strip()
    ]
    _check("table_split_nonempty", not empty_tables,
           f"{len(empty_tables)} 个空表格节点", severity="warn")

    # §5.3 截断 / 大量过滤 → 质量异常留痕（文档仍可 active，但必须可审计）
    if chunking["truncated"]:
        anomalies.append({
            "check": "chunks_truncated", "severity": "warn",
            "detail": "chunk 超上限被截断，检索覆盖不完整",
        })
    total_pre_filter = chunking["chunk_count"] + chunking["filtered_count"]
    if total_pre_filter > 0 and chunking["filtered_count"] / total_pre_filter > HEAVY_FILTER_RATIO:
        anomalies.append({
            "check": "heavy_filtering", "severity": "warn",
            "detail": f"过滤 {chunking['filtered_count']}/{total_pre_filter} 超阈值 "
                      f"{HEAVY_FILTER_RATIO:.0%}，原因: {chunking['filtered_reasons']}",
        })
    if parsing["degraded"]:
        anomalies.append({
            "check": "ocr_degraded", "severity": "warn",
            "detail": f"扫描件经 OCR 兜底识别 {parsing['ocr_pages']} 页，"
                      "文本可靠性低于原生文字层",
        })
    # §5.2 值丢失检测（原始文本 vs 清洗后叶子文本）
    cleaned_all = "\n".join(
        n.text for n in walk(raw_ast.root) if n.type in LEAF_TYPES
    )
    anomalies.extend(_value_loss_check(raw_ast.raw_text or "", cleaned_all))

    record["validation"]["checks"] = checks
    record["validation"]["anomalies"] = anomalies
    return anomalies


def persist_quality_record(record: dict, quality_dir: str = "data/quality_records") -> str:
    """质量报告 JSON 落盘：data/quality_records/{kb_id}/{doc_id}.json。"""
    kb = record.get("kb_id") or "default"
    doc = record.get("doc_id") or hashlib.sha256(
        (record.get("file_path") or "x").encode("utf-8")
    ).hexdigest()[:16]
    out_dir = os.path.join(quality_dir, kb)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{doc}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 原子落盘
    return path


def hard_anomalies(anomalies: list[dict]) -> list[dict]:
    """§5.3 硬异常（error 级）：存在即不得置 active。"""
    return [a for a in anomalies if a.get("severity") == "error"]


def anomaly_summary(anomalies: list[dict]) -> str:
    """异常列表 → quality_issues 追加串（warn 级留痕）。"""
    warns = [a for a in anomalies if a.get("severity") == "warn"]
    if not warns:
        return ""
    parts = [f"quality_anomaly:{a['check']}" for a in warns]
    return f"quality_anomalies({len(warns)}):" + ",".join(parts)
