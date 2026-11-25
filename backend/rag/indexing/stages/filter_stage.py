"""Filter Stage — 查重校验、分块身份赋予、质量过滤与 PII 脱敏。

从 indexer.py 抽离，负责：
1. SHA256 查重与 registry 占位行（register_in_progress）；
2. 命中活跃文档缓存时提前返回（跳过已索引文档）；
3. 文本分块初加工与 doc_id / source_file 身份注入；
4. ChunkFilter 规则过滤、表格结构脱敏、敏感信息屏蔽与保留理由回退；
5. 过滤留痕统计与 Trace/Lineage 上报。
"""

from __future__ import annotations

import os
from typing import Any

from backend.config import CHUNK_OVERLAP, LEAF_CHUNK_TOKENS
from backend.observability.tracer import trace_collector, SpanKind
from backend.shared.logger import logger


def mask_index_text_for_pii(text: str, doc_type: str, chunk_filter) -> str:
    """对索引阶段各层文本做统一 PII 脱敏（正文/摘要/doc 级检索文本）。"""
    if not text:
        return text
    from backend.rag.preprocessing.chunking import is_table_chunk
    from backend.rag.preprocessing.filter import _mask_financial_pii, _mask_pii

    is_financial = doc_type == "financial" or is_table_chunk(doc_type)
    if not (chunk_filter.enable_pii or (is_financial and chunk_filter.financial_pii_force)):
        return text
    masked_text, _ = _mask_pii(text)
    if is_financial and chunk_filter.financial_pii_force:
        masked_text, _ = _mask_financial_pii(masked_text)
    return masked_text


def filter_quality_summary(filtered_details: list[dict]) -> str:
    """将过滤统计压缩为一行质量留痕摘要。"""
    if not filtered_details:
        return ""
    counts: dict[str, int] = {}
    for item in filtered_details:
        reason = str(item.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    parts = [f"{reason}={count}" for reason, count in sorted(counts.items())]
    return f"filtered_chunks({len(filtered_details)}: {', '.join(parts)})"


def append_quality_issue(doc_meta: dict, issue: str) -> None:
    """向 doc_meta 安全追加 quality_issues（去重且防 None 覆盖）。"""
    if not issue:
        return
    current = str(doc_meta.get("quality_issues") or "").strip()
    if not current:
        doc_meta["quality_issues"] = issue
        return
    existing = [part.strip() for part in current.split(";") if part.strip()]
    if issue not in existing:
        existing.append(issue)
        doc_meta["quality_issues"] = "; ".join(existing)


def filter_index_chunks(
    chunks: list, chunk_filter, file_path: str, doc_type: str,
) -> tuple[list, list[dict]]:
    """过滤索引 Chunk；保留非空且仍被 Leaf 引用的 Parent 上下文。"""
    from backend.rag.preprocessing.chunking import is_table_chunk
    from backend.rag.preprocessing.filter import (
        ChunkFilter,
        _mask_financial_pii,
        _mask_pii,
    )

    referenced_parent_ids = {
        str((chunk.metadata or {}).get("parent_chunk_id") or "").strip()
        for chunk in chunks
        if (chunk.metadata or {}).get("granularity") == "leaf"
    }
    referenced_parent_ids.discard("")

    kept_chunks = []
    filtered_details: list[dict] = []

    def _mask_structured_table_metadata(chunk, metadata: dict) -> None:
        """Parent/Leaf 的表格结构与正文一起脱敏，保留 JSON 矩阵形状。"""
        fields = ("table_rows_json", "table_structures_json")
        if not any(metadata.get(key) for key in fields):
            return

        should_mask = (
            chunk_filter.enable_pii or chunk_filter.financial_pii_force
        )
        if not should_mask:
            return

        masked_types: list[str] = []
        masked_content, content_types = _mask_pii(chunk.page_content)
        if chunk_filter.financial_pii_force:
            masked_content, financial_types = _mask_financial_pii(masked_content)
            content_types.extend(financial_types)
        if content_types:
            chunk.page_content = masked_content
            masked_types.extend(content_types)

        for key in fields:
            value = metadata.get(key)
            if not isinstance(value, str) or not value:
                continue
            masked_value, found_types = _mask_pii(value)
            if chunk_filter.financial_pii_force:
                masked_value, financial_types = _mask_financial_pii(masked_value)
                found_types.extend(financial_types)
            if found_types:
                metadata[key] = masked_value
                masked_types.extend(found_types)

        if masked_types:
            existing = metadata.get("pii_masked") or []
            if isinstance(existing, str):
                existing = [existing]
            metadata["pii_masked"] = list(dict.fromkeys(
                [*existing, *masked_types],
            ))

    for chunk in chunks:
        metadata = chunk.metadata or {}
        if doc_type:
            metadata["doc_type"] = doc_type
        ok, reason = chunk_filter.should_keep(chunk.page_content, metadata)
        chunk_id = str(metadata.get("chunk_id") or "").strip()
        keep_referenced_parent = bool(
            not ok
            and metadata.get("granularity") == "parent"
            and chunk_id in referenced_parent_ids
            and (chunk.page_content or "").strip()
        )
        keep_short_source_leaf = bool(
            not ok
            and reason == "too_short"
            and metadata.get("granularity") == "leaf"
            and (chunk.page_content or "").strip()
        )
        if keep_referenced_parent or keep_short_source_leaf:
            if keep_referenced_parent:
                metadata["filter_status"] = "kept_referenced_parent"
                metadata["parent_filter_override_reason"] = reason
            else:
                metadata["filter_status"] = "kept_source_leaf"
                metadata["source_leaf_filter_override_reason"] = reason
            metadata["filter_reason"] = ""
            is_financial = doc_type == "financial" or is_table_chunk(
                metadata.get("chunk_type")
            )
            if chunk_filter.enable_pii or (
                is_financial and chunk_filter.financial_pii_force
            ):
                masked_text, masked_types = _mask_pii(chunk.page_content)
                if is_financial and chunk_filter.financial_pii_force:
                    masked_text, financial_types = _mask_financial_pii(
                        masked_text
                    )
                    masked_types.extend(financial_types)
                if masked_types:
                    chunk.page_content = masked_text
                    metadata["pii_masked"] = masked_types
            ok = True

        if ok:
            masked_text = metadata.pop("_masked_text", None)
            if isinstance(masked_text, str):
                chunk.page_content = masked_text
            elif metadata.get("pii_masked"):
                chunk.page_content = ChunkFilter.apply_pii_mask(
                    chunk.page_content
                )
            _mask_structured_table_metadata(chunk, metadata)
            kept_chunks.append(chunk)
            continue

        filtered_details.append({
            "chunk_index": metadata.get("chunk_index", len(kept_chunks)),
            "chunk_type": metadata.get("chunk_type"),
            "reason": reason,
            "preview": chunk.page_content[:80],
            "content": chunk.page_content,
        })
        logger.debug(f"[Filter] 拒绝 chunk: {reason} (doc={file_path})")
    return kept_chunks, filtered_details


class FilterStage:
    """查重与分块过滤阶段。"""

    def __init__(self, registry: Any, candidate_mode: bool = False):
        self.registry = registry
        self.candidate_mode = candidate_mode

    def check_dedup(
        self,
        file_path: str,
        path_id: str,
        file_hash: str,
        doc_id: str,
        kb_id: str,
        department: str,
        display_name: str,
    ) -> dict | None:
        """检查 SHA256 查重，若已有 active 则返回跳过 dict，否则写占位并返回 None。"""
        dedup_span = trace_collector.start_span(
            "index_dedup",
            parent_id="index_upload",
            name=f"Check {display_name}",
            type="dedup",
            kind=SpanKind.INDEX_DEDUP.value,
            input={"file_hash": file_hash},
        )
        if not self.candidate_mode:
            try:
                self.registry.register_in_progress(
                    path_id, doc_id=doc_id, file_hash=file_hash,
                    kb_id=kb_id, department=department,
                )
            except Exception as e:
                logger.debug(f"[Indexer] parsing 占位行写入失败（不影响索引）: {e}")

        dup_check = self.registry.get_by_path(path_id)
        if (not self.candidate_mode and dup_check
                and dup_check.get("file_hash") == file_hash
                and dup_check.get("status") == "active"):
            trace_collector.end_span(dedup_span,
                metrics={"cached": True, "existing_doc_id": dup_check.get("doc_id", "")})
            logger.info(f"[Dedup] 文件未变更，跳过索引: {file_path}")
            return {
                "chunk_count": int(dup_check.get("chunk_count") or 0),
                "doc_db_id": dup_check.get("doc_db_id", ""),
                "file_hash": file_hash,
                "skipped": True,
            }
        trace_collector.end_span(dedup_span, metrics={"cached": False})
        return None

    def execute_filter(
        self,
        chunks: list,
        file_path: str,
        path_id: str,
        doc_id: str,
        doc_type: str,
        display_name: str,
        lineage_recorder: Any = None,
    ) -> tuple[list, list[dict]]:
        """执行文本分块过滤、身份赋予与脱敏。"""
        chunk_span = trace_collector.start_span(
            "index_chunk",
            parent_id="index_upload",
            name=f"Chunk {display_name}",
            type="chunk",
            kind=SpanKind.INDEX_CHUNK.value,
        )
        chunk_lineage = None
        if lineage_recorder is not None:
            chunk_lineage = lineage_recorder.begin_stage(
                "semantic_chunk", role=None, engine_type="chunker",
                metadata={
                    "strategy": "pipeline",
                    "overlap": CHUNK_OVERLAP,
                },
            )
        try:
            for i, ch in enumerate(chunks):
                ch.metadata["doc_id"] = doc_id
                ch.metadata["chunk_index"] = i
                ch.metadata["source_file"] = os.path.basename(path_id)
                ch.metadata["file_path"] = path_id

            from backend.rag.preprocessing.filter import ChunkFilter
            chunk_filter = ChunkFilter()
            total_before_filter = len(chunks)
            filtered_chunks, filtered_details = filter_index_chunks(
                chunks, chunk_filter, file_path, doc_type,
            )
            filtered_count = len(filtered_details)
            if filtered_count > 0:
                reason_breakdown: dict[str, int] = {}
                for d in filtered_details:
                    reason_breakdown[d["reason"]] = reason_breakdown.get(d["reason"], 0) + 1
                logger.info(
                    f"[Filter] {file_path}: 过滤 {filtered_count}/{total_before_filter} "
                    f"个 chunk ({reason_breakdown})"
                )

            trace_collector.end_span(chunk_span,
                metrics={"raw_chunks": total_before_filter,
                         "kept_chunks": len(filtered_chunks),
                         "filtered_out": filtered_count,
                         "chunk_size": LEAF_CHUNK_TOKENS,
                         "chunk_overlap": CHUNK_OVERLAP},
                output={"preview": [c.page_content[:100] for c in filtered_chunks[:3]],
                        "total": len(filtered_chunks),
                        "strategy": "pipeline",
                        "chunk_size": LEAF_CHUNK_TOKENS,
                        "chunk_overlap": CHUNK_OVERLAP,
                        "filtered_details": filtered_details[:20]})
            if chunk_lineage is not None:
                lineage_recorder.finish_stage(
                    chunk_lineage[0], status="success",
                    started_at=chunk_lineage[1],
                    input_count=total_before_filter,
                    output_count=len(filtered_chunks),
                )
            return filtered_chunks, filtered_details
        except Exception as e:
            if chunk_lineage is not None:
                lineage_recorder.finish_stage(
                    chunk_lineage[0], status="failed",
                    started_at=chunk_lineage[1],
                    error_message=str(e),
                )
            trace_collector.end_span(chunk_span, status="error",
                metrics={"error": str(e)[:200]})
            raise
