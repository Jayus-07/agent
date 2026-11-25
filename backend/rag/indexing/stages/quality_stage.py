"""Quality Stage — 质量门禁、付费前完整性预检与质量记录落盘。

从 indexer.py 抽离，负责：
1. 付费前完整性预检（enforce_prepaid_integrity）：解析/切分/清洗阶段一旦确定内容丢失，立即阻断，不产生任何 LLM/embedding 费用；
2. Chunk 最终身份协议（apply_index_chunk_identity）：统一映射 chunk_id 并保证 parent_chunk_id 引用完整；
3. 付费后入库前质量门禁（audit_and_persist_quality）：类型化校验、覆盖度审计、质量记录落盘与硬异常阻断。
"""

from __future__ import annotations

import os
from typing import Any

from backend.observability.tracer import SpanKind
from backend.rag.indexing.stages.contracts import trace_collector
from backend.rag.indexing.stages.filter_stage import append_quality_issue
from backend.shared.logger import logger

PREPAID_INTEGRITY_ISSUE_PREFIX = "quality_anomaly:prepaid_integrity"

EXPECTED_SOFT_WARNING_CHECKS = frozenset({
    "ocr_degraded",
    "parser_pdf_visual_content_unparsed",
    "coverage_audit_sampled",
    "heavy_filtering",
    "table_split_nonempty",
})


class PrepaidContentIntegrityError(ValueError):
    """付费前完整性预检硬异常（P0-E）。"""

    def __init__(self, issue_codes: list[str]):
        self.issue_codes = list(issue_codes)
        super().__init__(
            f"付费前预检失败(付费前完整性预检): {','.join(self.issue_codes)}"
        )


def reviewable_anomalies(anomalies: list) -> list:
    """提取需要进入待复核软提醒的异常清单。"""
    reviewable = []
    for item in anomalies or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("check") or "")
        level = str(item.get("level") or item.get("severity") or "")
        if level == "warn" and code not in EXPECTED_SOFT_WARNING_CHECKS:
            reviewable.append(item)
    return reviewable


_COVERAGE_AUDIT_FIELDS = frozenset({
    "leaf_total", "audited_leaf_count", "covered", "uncovered_count",
    "table_rows_total", "table_rows_uncovered",
    "table_cells_total", "table_cells_uncovered",
    "coverage_rate", "sampled",
})


def coverage_quality_metrics(coverage: dict) -> dict:
    """覆盖审计 → 质量门禁指标（付费前预检与付费后门禁同口径）。

    只输出结构化指标，不带 uncovered 正文样本（Trace 不落未覆盖正文）。
    """
    if not isinstance(coverage, dict):
        return {
            "coverage_sampled": False,
            "coverage_leaf_total": 0,
            "coverage_audited_leaf_count": 0,
            "coverage_uncovered_count": 1,
            "coverage_audit_error": True,
            "coverage_issue": "coverage_audit_missing",
        }
    coverage_error = bool(
        coverage.get("error") or not _COVERAGE_AUDIT_FIELDS.issubset(coverage)
    )
    try:
        leaf_total = int(coverage.get("leaf_total") or 0)
        audited_count = int(coverage.get("audited_leaf_count") or 0)
        uncovered_count = int(coverage.get("uncovered_count") or 0)
        if (
            leaf_total < 0
            or audited_count < 0
            or uncovered_count < 0
            or audited_count > leaf_total
            or uncovered_count > audited_count
            or (audited_count < leaf_total and coverage.get("sampled") is not True)
            or not isinstance(coverage.get("sampled"), bool)
        ):
            coverage_error = True
    except (TypeError, ValueError):
        leaf_total = audited_count = uncovered_count = 0
        coverage_error = True
    return {
        "coverage_sampled": bool(coverage.get("sampled")),
        "coverage_leaf_total": leaf_total,
        "coverage_audited_leaf_count": audited_count,
        "coverage_uncovered_count": uncovered_count,
        "coverage_audit_error": coverage_error,
        "coverage_issue": (
            "coverage_audit_failed" if coverage_error
            else "coverage_gap" if uncovered_count > 0
            else ""
        ),
    }


def quality_index_metadata(doc_meta: dict) -> dict:
    """为质量记录抽取快照元数据。"""
    return {
        key: doc_meta[key]
        for key in (
            "doc_id", "kb_id", "department", "tenant_id", "fixture_set",
            "doc_type", "title", "language", "source_format", "parser_version",
            "chunk_policy_version", "indexed_at",
        )
        if key in doc_meta
    }


def apply_index_chunk_identity(chunks: list, doc_id: str) -> None:
    """索引期 chunk 身份协议：覆盖 chunk_id 并同步 parent_chunk_id。"""
    id_map = {
        str(chunk.metadata.get("chunk_id") or ""): f"{doc_id}_{index}"
        for index, chunk in enumerate(chunks)
        if str(chunk.metadata.get("chunk_id") or "")
    }
    for index, ch in enumerate(chunks):
        ch.metadata["chunk_id"] = f"{doc_id}_{index}"
        pid = str(ch.metadata.get("parent_chunk_id") or "")
        if pid:
            ch.metadata["parent_chunk_id"] = id_map.get(pid, "")


class QualityStage:
    """质量审计与门禁阶段。"""

    def __init__(self, registry: Any, candidate_mode: bool = False):
        self.registry = registry
        self.candidate_mode = candidate_mode

    def enforce_prepaid_integrity(
        self,
        qc: dict,
        chunks: list,
        path_id: str,
        doc_meta: dict,
        *,
        doc_id: str,
        kb_id: str,
        file_path: str,
        file_size: int,
        file_hash: str,
        filtered_details: list[dict] | None = None,
        truncated: bool = False,
    ) -> dict | None:
        """付费前免费完整性预检。"""
        from backend.rag.preprocessing.chunking import coverage_audit, is_table_chunk
        from backend.rag.preprocessing.quality_gate import (
            audit_chunk_integrity,
            cleaning_coverage_audit,
        )

        raw_ast = qc.get("raw_ast")
        if raw_ast is None:
            return None
        audit_ast = qc.get("normalized_ast") or raw_ast
        pii_normalized = any(
            bool((chunk.metadata or {}).get("pii_masked"))
            for chunk in chunks
            if (chunk.metadata or {}).get("granularity") == "leaf"
        )
        coverage = coverage_audit(
            audit_ast, chunks, normalize_pii=pii_normalized,
            excluded_texts=[
                d["content"]
                for d in (filtered_details or [])
                if d.get("content")
                and not (
                    isinstance(d.get("chunk_type"), str)
                    and is_table_chunk(d["chunk_type"])
                )
            ],
        )
        qc["coverage"] = coverage
        chunk_integrity = audit_chunk_integrity(chunks)
        cleaning = qc.get("cleaning_coverage")
        if not isinstance(cleaning, dict):
            cleaning = cleaning_coverage_audit(raw_ast)
            qc["cleaning_coverage"] = cleaning

        cov_metrics = coverage_quality_metrics(coverage)
        failures: list[str] = []
        if cov_metrics["coverage_audit_error"]:
            failures.append("coverage_audit_failed")
        elif cov_metrics["coverage_uncovered_count"] > 0:
            failures.append("coverage_gap")
        if int(chunk_integrity.get("valid_leaf_chunk_count") or 0) < 1:
            failures.append("chunks_present")
        elif (
            int(chunk_integrity.get("empty_chunk_count") or 0)
            or int(chunk_integrity.get("missing_chunk_id_count") or 0)
            or int(chunk_integrity.get("duplicate_chunk_id_count") or 0)
        ):
            failures.append("chunk_identifiers")
        elif int(chunk_integrity.get("invalid_parent_reference_count") or 0):
            failures.append("parent_references")
        if cleaning.get("error"):
            failures.append("cleaning_audit_failed")
        elif int(cleaning.get("uncovered_count") or 0) > 0:
            failures.append("cleaner_content_loss")

        record = None
        anomalies: list[dict] = []
        try:
            from backend.rag.preprocessing.quality_gate import (
                build_quality_record,
                hard_anomalies,
                run_typed_validation,
            )

            record = build_quality_record(
                file_path=file_path, doc_id=doc_id, kb_id=kb_id,
                raw_ast=raw_ast, chunks=chunks,
                file_size=file_size, file_hash=file_hash or "",
                doc_type=qc.get("doc_type", ""),
                strategy_name=qc.get("strategy_name", ""),
                completeness=qc.get("completeness"),
                filtered_details=filtered_details,
                truncated=truncated,
                ocr_triggered=bool(getattr(raw_ast, "ocr_triggered", False)),
                ocr_pages=int(getattr(raw_ast, "ocr_pages", 0) or 0),
                route_decision=qc.get("route_decision"),
                section_tree=qc.get("section_tree"),
                index_metadata=quality_index_metadata(doc_meta),
                document_title=str(doc_meta.get("title") or ""),
                language=str(doc_meta.get("language") or ""),
                coverage=coverage,
                cleaning_coverage=cleaning,
            )
            anomalies = run_typed_validation(record, raw_ast)
            for item in hard_anomalies(anomalies):
                code = str(item.get("check") or "")
                if code and code not in failures:
                    failures.append(code)
        except Exception:
            logger.warning("[Indexer] 付费前类型化校验构建失败（退化为基础判据）", exc_info=True)

        if not failures:
            return {
                "coverage": coverage,
                "chunk_integrity": chunk_integrity,
                "cleaning_coverage": cleaning,
            }

        detail = ",".join(failures)
        doc_meta["quality_status"] = "failed"
        append_quality_issue(
            doc_meta, f"{PREPAID_INTEGRITY_ISSUE_PREFIX}({detail},付费前拦截)"
        )
        quality_score = None
        quality_metrics = {}
        try:
            from backend.rag.preprocessing.quality_gate import (
                build_quality_record,
                persist_quality_record,
                quality_score_metrics,
                run_typed_validation,
            )

            if record is None:
                record = build_quality_record(
                    file_path=file_path, doc_id=doc_id, kb_id=kb_id,
                    raw_ast=raw_ast, chunks=chunks,
                    file_size=file_size, file_hash=file_hash or "",
                    doc_type=qc.get("doc_type", ""),
                    strategy_name=qc.get("strategy_name", ""),
                    completeness=qc.get("completeness"),
                    filtered_details=filtered_details,
                    truncated=truncated,
                    ocr_triggered=bool(getattr(raw_ast, "ocr_triggered", False)),
                    ocr_pages=int(getattr(raw_ast, "ocr_pages", 0) or 0),
                    route_decision=qc.get("route_decision"),
                    section_tree=qc.get("section_tree"),
                    coverage=coverage,
                    cleaning_coverage=cleaning,
                )
            if not anomalies:
                anomalies = run_typed_validation(record, raw_ast)
            quality_metrics = quality_score_metrics(anomalies)
            quality_score = quality_metrics["quality_score"]
            doc_meta["quality_score"] = quality_score
            record["quality_issues"] = doc_meta.get("quality_issues", "")
            record.setdefault("validation", {})["score"] = quality_score
            doc_meta["quality_record_path"] = persist_quality_record(record)
        except Exception:
            logger.warning("[Indexer] 付费前完整性预检质量记录落盘失败", exc_info=True)

        try:
            q_span = trace_collector.start_span(
                "index_quality",
                parent_id="index_upload",
                name="Quality record (prepaid)",
                type="audit",
                kind=SpanKind.INDEX_PARSE.value,
                input={
                    "file_path": file_path,
                    "doc_id": doc_id,
                    "coverage": cov_metrics,
                    "chunk_integrity": chunk_integrity,
                },
            )
            trace_collector.end_span(
                q_span, status="error",
                metrics={
                    **quality_metrics,
                    "record_path": doc_meta.get("quality_record_path", ""),
                    **cov_metrics,
                    **chunk_integrity,
                },
            )
        except Exception:
            logger.warning("[Indexer] 付费前预检 Trace 留痕失败", exc_info=True)

        if not self.candidate_mode:
            registry_fields = {
                "quality_issues": doc_meta.get("quality_issues", ""),
                "quality_status": "failed",
            }
            if quality_score is not None:
                registry_fields["quality_score"] = quality_score
            try:
                self.registry.update_fields(path_id, registry_fields)
            except Exception:
                logger.warning("[Indexer] 付费前预检留痕写入 registry 失败", exc_info=True)
        raise PrepaidContentIntegrityError(failures)

    def audit_quality_gate(
        self,
        qc: dict,
        chunks: list,
        path_id: str,
        doc_meta: dict,
        file_path: str,
        file_size: int,
        file_hash: str,
        doc_id: str,
        kb_id: str,
        filtered_details: list[dict],
        chunks_truncated: bool,
    ) -> None:
        """付费后入库前质量门禁审计与留痕。"""
        raw_ast = qc.get("raw_ast")
        if raw_ast is None:
            return

        from backend.rag.preprocessing.chunking import coverage_audit
        from backend.rag.preprocessing.quality_gate import (
            audit_chunk_integrity,
            build_quality_record,
            hard_anomalies,
            persist_quality_record,
            quality_score_metrics,
            run_typed_validation,
        )

        audit_ast = qc.get("normalized_ast") or raw_ast
        cov_pii = any(
            bool((chunk.metadata or {}).get("pii_masked"))
            for chunk in chunks
            if (chunk.metadata or {}).get("granularity") == "leaf"
        )
        coverage = qc.get("coverage")
        if not isinstance(coverage, dict):
            coverage = coverage_audit(audit_ast, chunks, normalize_pii=cov_pii)
            qc["coverage"] = coverage

        cov_metrics = coverage_quality_metrics(coverage)
        cov_error = cov_metrics["coverage_audit_error"]
        cov_uncovered = cov_metrics["coverage_uncovered_count"]
        cov_issue = cov_metrics["coverage_issue"]
        cov_req_fail = bool(cov_error or cov_uncovered > 0)

        chunk_integrity = audit_chunk_integrity(chunks)
        integ_req_fail = bool(
            chunk_integrity["valid_leaf_chunk_count"] < 1
            or chunk_integrity["empty_chunk_count"]
            or chunk_integrity["missing_chunk_id_count"]
            or chunk_integrity["duplicate_chunk_id_count"]
            or chunk_integrity["invalid_parent_reference_count"]
        )
        integ_issue = (
            "chunks_present"
            if chunk_integrity["valid_leaf_chunk_count"] < 1
            else "chunk_identifiers"
            if (
                chunk_integrity["empty_chunk_count"]
                or chunk_integrity["missing_chunk_id_count"]
                or chunk_integrity["duplicate_chunk_id_count"]
            )
            else "parent_references"
            if chunk_integrity["invalid_parent_reference_count"]
            else ""
        )
        quality_req_fail = bool(cov_req_fail or integ_req_fail)

        q_span = trace_collector.start_span(
            "index_quality",
            parent_id="index_upload",
            name="Quality record",
            type="audit",
            kind=SpanKind.INDEX_PARSE.value,
            input={
                "file_path": file_path,
                "doc_id": doc_id,
                "coverage": cov_metrics,
                "chunk_integrity": chunk_integrity,
            },
        )
        quality_metrics = {}
        record_saved = False
        try:
            record = build_quality_record(
                file_path=file_path, doc_id=doc_id, kb_id=kb_id,
                raw_ast=raw_ast, chunks=chunks,
                file_size=file_size, file_hash=file_hash or "",
                doc_type=doc_meta.get("doc_type", qc.get("doc_type", "")),
                strategy_name=qc.get("strategy_name", ""),
                completeness=qc.get("completeness"),
                filtered_details=filtered_details,
                truncated=chunks_truncated,
                ocr_triggered=bool(getattr(raw_ast, "ocr_triggered", False)),
                ocr_pages=int(getattr(raw_ast, "ocr_pages", 0) or 0),
                route_decision=qc.get("route_decision"),
                section_tree=qc.get("section_tree"),
                index_metadata=quality_index_metadata(doc_meta),
                document_title=str(doc_meta.get("title") or ""),
                language=str(doc_meta.get("language") or ""),
                coverage=coverage,
                cleaning_coverage=qc.get("cleaning_coverage"),
            )
            anomalies = run_typed_validation(record, raw_ast)
            hard = hard_anomalies(anomalies)
            quality_metrics = quality_score_metrics(anomalies)
            doc_meta["quality_score"] = quality_metrics["quality_score"]
            record["quality_issues"] = doc_meta.get("quality_issues", "")
            record.setdefault("validation", {})["score"] = quality_metrics["quality_score"]

            qpath = persist_quality_record(record)
            record_saved = True
            doc_meta["quality_record_path"] = qpath
            if hard:
                doc_meta["quality_status"] = "failed"
                if not self.candidate_mode:
                    self.registry.update_fields(
                        path_id,
                        {
                            "quality_issues": doc_meta.get("quality_issues", ""),
                            "quality_score": doc_meta["quality_score"],
                            "quality_status": "failed",
                        },
                    )
                raise ValueError(
                    f"质量门禁硬异常: {hard[0]['check']}({hard[0]['detail']})"
                )
            doc_meta["quality_status"] = (
                "soft_warning" if reviewable_anomalies(anomalies) else "pass"
            )
            trace_collector.end_span(
                q_span,
                metrics={
                    **quality_metrics,
                    "record_path": qpath,
                    **cov_metrics,
                    **chunk_integrity,
                },
            )
        except ValueError:
            trace_collector.end_span(
                q_span, status="error",
                metrics={
                    "error": "quality_gate_hard_anomaly",
                    **quality_metrics,
                    **cov_metrics,
                    **chunk_integrity,
                },
            )
            if quality_req_fail and not record_saved and not self.candidate_mode:
                failure_issue = cov_issue or integ_issue
                if failure_issue not in str(doc_meta.get("quality_issues", "")):
                    append_quality_issue(doc_meta, "quality_anomaly:" + failure_issue)
                doc_meta["quality_status"] = "failed"
                try:
                    self.registry.update_fields(
                        path_id,
                        {
                            "quality_issues": doc_meta.get("quality_issues", ""),
                            "quality_status": "failed",
                        },
                    )
                except Exception:
                    logger.warning("[Indexer] 覆盖异常写入 registry 失败", exc_info=True)
            raise
        except Exception as e:
            trace_collector.end_span(
                q_span, status="error",
                metrics={
                    "error": str(e)[:200],
                    **quality_metrics,
                    **cov_metrics,
                    **chunk_integrity,
                },
            )
            if quality_req_fail:
                failure_issue = cov_issue or integ_issue
                if failure_issue not in str(doc_meta.get("quality_issues", "")):
                    append_quality_issue(doc_meta, "quality_anomaly:" + failure_issue)
                doc_meta["quality_status"] = "failed"
                if not self.candidate_mode:
                    try:
                        self.registry.update_fields(
                            path_id,
                            {
                                "quality_issues": doc_meta.get("quality_issues", ""),
                                "quality_status": "failed",
                            },
                        )
                    except Exception:
                        logger.warning("[Indexer] Chunk/Coverage 完整性异常写入 registry 失败", exc_info=True)
                raise ValueError(
                    "质量记录无法完成，且最终 Chunk/Coverage 完整性失败，拒绝将文档置为 active"
                ) from e
            logger.warning(f"[Indexer] 质量记录构建失败（不阻断索引）: {e}", exc_info=True)
