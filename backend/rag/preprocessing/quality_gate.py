"""§5.1–5.3 质量门禁（R3，2026-09-17）。

三件事：
  §5.1 每文档质量记录：文件大小/SHA256/解析节点数/清洗前后字符数/叶子数/
       表格行列数/leaf+parent 数/过滤数与原因/截断数/OCR 触发/降级标记/
       重试失败记录 —— 全量落 data/quality_records/{kb}/{doc_id}.json。
  §5.2 原文可追溯：node_id/doc_id/source_range/page_number/bbox/content_hash
       进质量 JSON；URL/邮箱/编号/金额/日期无依据丢失 → 异常留痕。
  §5.3 类型化校验：按格式校验字符量/关键节点/行列表头；截断、覆盖缺口、
       覆盖审计失败/降级/大量过滤均写 quality_issues；数据完整性硬异常抛错。

设计约束：记录 JSON 只存节点摘要（不存全文），体积有界；非完整性类审计
故障可留痕后继续索引，覆盖审计失败必须 fail closed，避免不完整内容 active。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any

from backend.rag.preprocessing.ast import LEAF_TYPES, DocumentAST, walk
from backend.rag.preprocessing.redaction import mask_sensitive_text
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
            samples = [mask_sensitive_text(item, limit=80) for item in sorted(lost)[:3]]
            anomalies.append({
                "check": "value_loss",
                "value_type": name,
                "severity": "warn",
                "detail": f"清洗后丢失 {len(lost)} 个 {name}，样例: {samples}",
            })
    return anomalies


def compute_quality_score(anomalies: list[dict]) -> int:
    """§5.1 质量评分（TD-09，2026-10-02）：确定性公式，供 registry/前端展示。

    score = 100 - 40×硬异常数 - 10×警告数，下限 0。
    此前 quality_score 列恒为默认 0（从未计算）→ 每份文档都触发前端
    「质量评分偏低 (0/100)」告警，纯噪音且掩盖真实质量差异。
    口径：硬异常在本层已被门禁拦截（文档不会 active），评分主要刻画
    警告密度——≤4 条警告仍 ≥60（前端阈值），持续劣化才跌破。
    """
    hard = sum(1 for a in anomalies if a.get("severity") == "hard")
    warn = sum(1 for a in anomalies if a.get("severity") != "hard")
    return max(0, 100 - 40 * hard - 10 * warn)


def audit_chunk_integrity(chunks: list) -> dict[str, int]:
    """汇总最终索引 Chunk 与 Parent 引用的完整性，供既有 QualityGate 使用。"""
    ids: list[str] = []
    granularity_by_id: dict[str, list[str]] = {}
    leaf_count = 0
    valid_leaf_count = 0
    parent_count = 0
    empty_count = 0
    missing_id_count = 0
    parent_reference_count = 0
    referenced_parent_ids: list[str] = []

    for chunk in chunks:
        metadata = getattr(chunk, "metadata", None) or {}
        chunk_id = str(metadata.get("chunk_id") or "").strip()
        granularity = str(metadata.get("granularity") or "").strip()
        content = str(getattr(chunk, "page_content", None) or "")
        if not content.strip():
            empty_count += 1
        if not chunk_id:
            missing_id_count += 1
        else:
            ids.append(chunk_id)
            granularity_by_id.setdefault(chunk_id, []).append(granularity)

        if granularity == "leaf":
            leaf_count += 1
            if content.strip() and chunk_id:
                valid_leaf_count += 1
            parent_id = str(metadata.get("parent_chunk_id") or "").strip()
            if parent_id:
                parent_reference_count += 1
                referenced_parent_ids.append(parent_id)
        elif granularity == "parent":
            parent_count += 1

    duplicate_id_count = len(ids) - len(set(ids))
    invalid_parent_reference_count = sum(
        1 for parent_id in referenced_parent_ids
        if len(granularity_by_id.get(parent_id, [])) != 1
        or granularity_by_id[parent_id][0] != "parent"
    )
    return {
        "chunk_count": len(chunks),
        "leaf_chunk_count": leaf_count,
        "valid_leaf_chunk_count": valid_leaf_count,
        "parent_chunk_count": parent_count,
        "empty_chunk_count": empty_count,
        "missing_chunk_id_count": missing_id_count,
        "duplicate_chunk_id_count": duplicate_id_count,
        "parent_reference_count": parent_reference_count,
        "invalid_parent_reference_count": invalid_parent_reference_count,
    }


def _audit_normalize(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def cleaning_coverage_audit(
    ast: DocumentAST, max_audit_nodes: int | None = None,
) -> dict:
    """Raw AST → 清洗输出 的合法清洗对账（P0-D 边界一，2026-10-18）。

    coverage_audit 只审「归一化 AST → 最终 Leaf」，看不到 Cleaner 把节点
    清空：被清空的叶子会从覆盖审计的分母里消失，形成"分母缩水式假通过"。

    对账口径（逐节点、用节点自身的 raw_text → text 配对）：
      - 规范化差异（控制字符/全半角/空白/中文标点/HTML 剥离）不算丢失；
      - 行形态即可判定的确定性噪音（页码/页脚/模板行/维基噪音/URL 邮箱/
        纯符号）算合法删除并计数；
      - 其余在**该节点自身**清洗结果中消失的行计入 uncovered，由质量门禁
        fail-closed。

    不使用全文拼接的公共文本对账：两个节点文本相同时，"全文里还出现过"
    不能抵消被清空的另一个节点（2026-10-18 边界修复）。
    默认**全量**审计：逐节点线性核对，不做全文 O(n²) 扫描；只有调用方显式
    传入 max_audit_nodes 才抽样，此时 sampled=True，发布门禁按"审计不完整"
    硬失败——抽样无缺口不得被表述成全量完整。
    审计自身异常返回 {"error": ...}，调用方必须按硬异常处理。
    """
    from backend.rag.preprocessing.cleaner import DocumentCleaner

    cleaner = DocumentCleaner()
    try:
        nodes = [n for n in walk(ast.root) if n.type != "table"]
        audited_all = [
            n for n in nodes if (getattr(n, "raw_text", "") or "").strip()
        ]
        sampled = False
        if max_audit_nodes is None:
            audited = audited_all
        else:
            cap = max(1, int(max_audit_nodes))
            if len(audited_all) > cap:
                stride = max(1, math.ceil(len(audited_all) / cap))
                audited = audited_all[::stride][:cap]
                sampled = True
            else:
                audited = audited_all

        covered = 0
        uncovered: list[dict] = []
        legal_noise_line_count = 0
        lost_line_count = 0

        for node in audited:
            raw_text = node.raw_text or ""
            raw_normalized = _audit_normalize(
                cleaner.normalize_for_compare(raw_text)
            )
            if not raw_normalized:
                covered += 1
                continue
            node_cleaned = _audit_normalize(
                cleaner.normalize_for_compare(node.text)
            )
            if raw_normalized in node_cleaned:
                covered += 1
                continue

            lost: list[str] = []
            for line in raw_text.splitlines():
                if not line.strip():
                    continue
                line_normalized = _audit_normalize(
                    cleaner.normalize_for_compare(line)
                )
                if not line_normalized:
                    continue
                if line_normalized in node_cleaned:
                    continue
                if cleaner.classify_noise_line(line):
                    legal_noise_line_count += 1
                    continue
                lost.append(mask_sensitive_text(line.strip(), limit=80))

            if lost:
                lost_line_count += len(lost)
                uncovered.append({
                    "type": node.type,
                    "preview": mask_sensitive_text(raw_text.strip(), limit=80),
                    "lost_lines": len(lost),
                    "lost_samples": lost[:3],
                })
            else:
                covered += 1

        return {
            "raw_leaf_total": len(audited_all),
            "audited_node_count": len(audited),
            "covered": covered,
            "uncovered_count": len(uncovered),
            "uncovered_samples": uncovered[:10],
            "legal_noise_line_count": legal_noise_line_count,
            "lost_line_count": lost_line_count,
            "sampled": sampled,
        }
    except Exception as e:  # noqa: BLE001 — 门禁按硬异常处理
        logger.warning(f"[CleaningAudit] 失败: {type(e).__name__}: {e}")
        return {
            "error": f"{type(e).__name__}: {e}",
            "raw_leaf_total": 0,
            "audited_node_count": 0,
            "covered": 0,
            "uncovered_count": 0,
            "uncovered_samples": [],
            "legal_noise_line_count": 0,
            "lost_line_count": 0,
            "sampled": False,
        }


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
    route_decision: dict | None = None,
    coverage: dict | None = None,
    cleaning_coverage: dict | None = None,
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

    # 清洗删除证据汇总（P0-A.4 可审计）：操作名/分类/样本，只留摘要不落全文
    dropped_evidence: list[dict] = []
    for node in walk(raw_ast.root):
        dropped_evidence.extend(getattr(node, "cleaning_dropped", []) or [])
    dropped_by_operation: dict[str, int] = {}
    for item in dropped_evidence:
        op = str(item.get("operation") or "unknown")
        dropped_by_operation[op] = dropped_by_operation.get(op, 0) + 1

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
    coverage_record = dict(coverage) if isinstance(coverage, dict) else None
    if coverage_record is not None and coverage_record.get("sampled"):
        # 抽样审计没有全量覆盖率；不接受调用方传入的 100% 数值。
        coverage_record["coverage_rate"] = None

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
            "table_resource_limit": {
                "rows_scanned": int(getattr(raw_ast, "table_rows_total", 0) or 0),
                "rows_truncated": int(getattr(raw_ast, "table_rows_truncated", 0) or 0),
                "rows_total_known": bool(getattr(raw_ast, "table_rows_total_known", True)),
                "cells_scanned": int(getattr(raw_ast, "table_cells_total", 0) or 0),
                "cells_truncated": int(getattr(raw_ast, "table_cells_truncated", 0) or 0),
                "cells_total_known": bool(getattr(raw_ast, "table_cells_total_known", True)),
                "reason": str(getattr(raw_ast, "table_truncation_reason", "") or ""),
            },
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
            # 删除原因与证据（合法噪音 vs 内容丢失由 cleaning_coverage 裁决）
            "dropped_total": len(dropped_evidence),
            "dropped_by_operation": dropped_by_operation,
            "dropped_samples": dropped_evidence[:10],
            "coverage": cleaning_coverage,
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
        "chunk_integrity": audit_chunk_integrity(chunks),
        # §5.1 重试失败记录（本轮 per-file 异常；历史失败见 registry status）
        "index_errors": list(index_errors or []),
        # §5.2 节点级可追溯
        "traceability": {"nodes": node_summaries},
        # P1 切分路由可审计决策（为什么用这个策略）
        "route_decision": dict(route_decision or {}),
        "coverage": coverage_record,
        # P0-D 边界一：Raw AST → 清洗输出（独立于覆盖审计，杜绝分母缩水）
        "cleaning_coverage": (
            dict(cleaning_coverage)
            if isinstance(cleaning_coverage, dict) else None
        ),
        # §5.3 校验结果（run_typed_validation 填充）
        "validation": {"checks": [], "anomalies": []},
    }
    return record


def run_typed_validation(record: dict, raw_ast: DocumentAST) -> list[dict]:
    """§5.3 类型化校验：按格式校验，返回异常列表并写回 record["validation"]。

    异常分两级：
      error → 硬异常，调用方必须让文档进 failed（不得伪装 active）；
      warn  → 软异常，留痕 quality_issues 供人工复核；完整性异常为 hard error。
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
    chunk_integrity = record.get("chunk_integrity")
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

    if not isinstance(chunk_integrity, dict):
        _check(
            "chunk_integrity_audit_failed", False,
            "Chunk 完整性审计结果缺失或格式错误",
        )
    else:
        valid_leaf_count = int(chunk_integrity.get("valid_leaf_chunk_count") or 0)
        empty_chunk_count = int(chunk_integrity.get("empty_chunk_count") or 0)
        missing_id_count = int(
            chunk_integrity.get("missing_chunk_id_count") or 0
        )
        duplicate_id_count = int(
            chunk_integrity.get("duplicate_chunk_id_count") or 0
        )
        invalid_parent_count = int(
            chunk_integrity.get("invalid_parent_reference_count") or 0
        )
        _check(
            "chunks_present", valid_leaf_count > 0,
            f"valid_leaf_chunk_count={valid_leaf_count}，至少需要一个非空 Leaf",
        )
        _check(
            "chunk_identifiers",
            empty_chunk_count == 0
            and missing_id_count == 0
            and duplicate_id_count == 0,
            "空 Chunk={}; 缺失 ID={}; 重复 ID={}".format(
                empty_chunk_count, missing_id_count, duplicate_id_count,
            ),
        )
        _check(
            "parent_references", invalid_parent_count == 0,
            "无效 Parent 引用={}/{}".format(
                invalid_parent_count,
                int(chunk_integrity.get("parent_reference_count") or 0),
            ),
        )

    cleaning_coverage = record.get("cleaning_coverage")
    if cleaning_coverage is not None:
        cleaning_fields = {
            "raw_leaf_total", "audited_node_count", "uncovered_count", "sampled",
        }
        cleaning_error = bool(
            not isinstance(cleaning_coverage, dict)
            or cleaning_coverage.get("error")
            or not cleaning_fields.issubset(cleaning_coverage)
        )
        if not cleaning_error:
            try:
                cleaning_total = int(cleaning_coverage.get("raw_leaf_total") or 0)
                cleaning_audited = int(
                    cleaning_coverage.get("audited_node_count") or 0
                )
                cleaning_uncovered = int(
                    cleaning_coverage.get("uncovered_count") or 0
                )
                cleaning_sampled = cleaning_coverage.get("sampled")
                if (
                    cleaning_total < 0
                    or cleaning_audited < 0
                    or cleaning_uncovered < 0
                    or cleaning_audited > cleaning_total
                    or cleaning_uncovered > cleaning_audited
                    or (
                        cleaning_audited < cleaning_total
                        and cleaning_sampled is not True
                    )
                    or not isinstance(cleaning_sampled, bool)
                ):
                    cleaning_error = True
            except (TypeError, ValueError):
                cleaning_total = cleaning_audited = cleaning_uncovered = 0
                cleaning_sampled = False
                cleaning_error = True
        if cleaning_error:
            error_detail = (
                str(cleaning_coverage.get("error") or "invalid cleaning audit")
                if isinstance(cleaning_coverage, dict)
                else "invalid cleaning audit"
            )
            _check(
                "cleaning_audit_failed", False,
                f"清洗完整性审计异常或结果字段缺失: {error_detail}",
            )
        else:
            samples = cleaning_coverage.get("uncovered_samples") or []
            _check(
                "cleaner_content_loss", cleaning_uncovered == 0,
                f"{cleaning_uncovered}/{cleaning_audited} 个已清洗节点存在正文丢失"
                + (f"，样例: {samples[:3]}" if samples else ""),
            )
            if cleaning_sampled:
                # 清洗边界是内容完整性的硬边界：未审计的节点可能已经丢失，
                # 不能被当作"抽样无缺口 = 全量通过"。这里按硬异常阻断发布。
                _check(
                    "cleaning_audit_incomplete", False,
                    f"清洗审计仅抽样 {cleaning_audited}/{cleaning_total} 个节点，"
                    "未审计节点不允许静默丢失；请使用全量审计",
                )

    coverage = record.get("coverage")
    if coverage is not None:
        coverage_fields = {
            "leaf_total", "audited_leaf_count", "uncovered_count", "sampled",
        }
        coverage_error = bool(
            not isinstance(coverage, dict)
            or coverage.get("error")
            or not coverage_fields.issubset(coverage)
        )
        try:
            leaf_total = int(coverage.get("leaf_total") or 0)
            audited_count = int(coverage.get("audited_leaf_count") or 0)
            uncovered = int(coverage.get("uncovered_count") or 0)
            sampled = coverage.get("sampled")
            if (
                leaf_total < 0
                or audited_count < 0
                or uncovered < 0
                or audited_count > leaf_total
                or uncovered > audited_count
                or (audited_count < leaf_total and sampled is not True)
                or not isinstance(sampled, bool)
            ):
                coverage_error = True
        except (TypeError, ValueError, AttributeError):
            leaf_total = audited_count = uncovered = 0
            sampled = False
            coverage_error = True
        if coverage_error:
            error_detail = (
                str(coverage.get("error") or "invalid coverage report")
                if isinstance(coverage, dict) else "invalid coverage report"
            )
            _check(
                "coverage_audit_failed", False,
                "coverage_audit 异常或结果字段缺失: "
                f"{error_detail}",
            )
        else:
            _check(
                "coverage_gap", uncovered == 0,
                f"{uncovered}/{audited_count} "
                "个已审 AST 叶节点未被实际输出 Leaf 覆盖",
            )
            if sampled:
                detail = (
                    f"抽样审计 {audited_count}/{leaf_total} 个源叶节点；"
                    "不能据此宣称全量覆盖率 100%"
                )
                checks.append({
                    "check": "coverage_audit_sampled",
                    "pass": False,
                    "detail": detail,
                })
                anomalies.append({
                    "check": "coverage_audit_sampled",
                    "severity": "warn",
                    "detail": detail,
                })

    # §5.3 截断 → 硬异常（P0 超长文档收口 2026-10-13）：截断文档的检索
    # 覆盖天然不完整，此前 warn 级留痕 + active 属「假成功」。任务口径：
    # 超过单文档上限不得截断后标记成功——文档进 failed，用户拆分或调大
    # MAX_CHUNKS_PER_DOC 后重传。（大量过滤仍为 warn 留痕。）
    if chunking["truncated"]:
        anomalies.append({
            "check": "chunks_truncated", "severity": "error",
            "detail": (
                "chunk 超出单文档上限被截断，拒绝部分索引（不得以不完整"
                "状态 active）；请拆分文档或调大 MAX_CHUNKS_PER_DOC 后重传"
            ),
        })
    # 解析级资源保护截断不能以部分内容 active；Excel 触顶后停止扫描，
    # 因此只报告已知下界，并明确后续总量是否未知。
    table_trunc = int(getattr(raw_ast, "table_rows_truncated", 0) or 0)
    cells_trunc = int(getattr(raw_ast, "table_cells_truncated", 0) or 0)
    if table_trunc or cells_trunc:
        rows_total = int(getattr(raw_ast, "table_rows_total", 0) or 0)
        rows_known = bool(getattr(raw_ast, "table_rows_total_known", True))
        cells_known = bool(getattr(raw_ast, "table_cells_total_known", True))
        row_total_detail = (
            f"总计扫描 {rows_total} 行"
            if rows_known else f"至少扫描 {rows_total} 行，后续未读且总行数未知"
        )
        cell_detail = (
            f"；已知跳过 {cells_trunc} 个单元格"
            if cells_known else
            f"；至少跳过 {cells_trunc} 个单元格，后续总量未知"
        ) if cells_trunc else ""
        row_trunc_detail = (
            f"已截断 {table_trunc} 行"
            if rows_known else f"至少有 {table_trunc} 行未纳入（下界）"
        )
        anomalies.append({
            "check": "table_rows_truncated", "severity": "error",
            "detail": (
                f"表格解析触发资源上限，{row_trunc_detail}；"
                f"{row_total_detail}{cell_detail}，不允许以部分内容 active"
            ),
        })
    ocr_limit = getattr(raw_ast, "ocr_page_limit", 0) or 0
    ocr_failed = getattr(raw_ast, "ocr_failed_pages", 0) or 0
    if ocr_limit or ocr_failed:
        anomalies.append({
            "check": "ocr_coverage_partial", "severity": "error",
            "detail": (
                f"OCR 覆盖不完整：页数上限 {ocr_limit or '∞'}，"
                f"失败页 {ocr_failed}，实际产出 {parsing['ocr_pages']} 页；"
                "不允许以部分内容 active"
            ),
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


def persist_quality_record(record: dict, quality_dir: str | None = None) -> str:
    """质量报告 JSON 落盘：{RAG_DATA_DIR}/quality_records/{kb_id}/{doc_id}.json。

    默认目录从 RAG_DATA_DIR 派生（容器 volume 与测试 conftest 的 tmp 隔离
    统一生效）——原硬编码相对路径 "data/quality_records" 随 CWD 漂移，
    宿主从仓库根跑测试会写穿工作区（N-01，2026-10-06）。kb_id/doc_id 做
    路径段卫生化：kb_id 为 ".." 时曾把落盘目录上跳到 data 根（路径逃逸）。
    """
    if quality_dir is None:
        from backend.config.database import RAG_DATA_DIR

        quality_dir = os.path.join(RAG_DATA_DIR, "quality_records")
    kb = _sanitize_path_segment(record.get("kb_id") or "default")
    doc = _sanitize_path_segment(record.get("doc_id") or hashlib.sha256(
        (record.get("file_path") or "x").encode("utf-8")
    ).hexdigest()[:16])
    out_dir = os.path.join(quality_dir, kb)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{doc}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 原子落盘
    return path


def _sanitize_path_segment(value: str) -> str:
    """路径段卫生化：禁目录上跳/分隔符，空值兜底。"""
    cleaned = str(value or "").strip().replace("\\", "_").replace("/", "_")
    cleaned = cleaned.replace("..", "_")
    return cleaned or "default"


def hard_anomalies(anomalies: list[dict]) -> list[dict]:
    """§5.3 硬异常（error 级）：存在即不得置 active。"""
    return [a for a in anomalies if a.get("severity") == "error"]


def anomaly_summary(anomalies: list[dict], *, include_hard: bool = False) -> str:
    """异常列表 → quality_issues 追加串；默认兼容旧行为，仅汇总 warn。"""
    selected = [
        a for a in anomalies
        if a.get("severity") == "warn"
        or (include_hard and a.get("severity") in {"error", "hard"})
    ]
    if not selected:
        return ""
    parts = [f"quality_anomaly:{a['check']}" for a in selected]
    return f"quality_anomalies({len(selected)}):" + ",".join(parts)
