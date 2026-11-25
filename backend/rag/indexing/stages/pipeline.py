"""Indexing Pipeline — 索引全流程管道编排器。

将各个单一职责 Stage 组装为可测试、可观测、带原子事务保护的标准 Pipeline：
  1. Load & Parse Stage: 文件载入、多格式解析、OCR 追踪、AST 生成
  2. Filter Stage: SHA256 查重早退、语义切分过滤、PII 脱敏
  3. Quality Stage (Prepaid): 付费前内容完整性审计（0 费用阻断）与 Chunk 身份协议
  4. Metadata Stage: LLM 文档元数据构建、Near-dup 识别
  5. Quality Stage (Gate): 付费后入库前质量门禁与质量记录落盘
  6. Enrich Stage: 表格描述、实体/时间/模拟问题对齐、doc 级文本构建、向量预算预检
  7. Embedding Stage: 批量向量化、退避重试与实例级向量缓存
  8. Persist Stage: ChunkStore、DocDB、VectorDB 写入、BM25 快照发布、Registry 注册与异常原子回滚
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from backend.config.rag import _EVAL_FIXTURE_SETS
from backend.observability.tracer import trace_collector, SpanKind
from backend.rag.indexing.processing_lineage import ProcessingRunRecorder
from backend.rag.indexing.stages.embedding_stage import EmbeddingStage
from backend.rag.indexing.stages.enrich_stage import (
    EnrichStage,
    question_candidate_indexes,
    select_vector_chunks,
)
from backend.rag.indexing.stages.filter_stage import (
    FilterStage,
    append_quality_issue,
    filter_quality_summary,
)
from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.rag.indexing.stages.parse_stage import ParseStage
from backend.rag.indexing.stages.persist_stage import PersistStage
from backend.rag.indexing.stages.quality_stage import (
    QualityStage,
    apply_index_chunk_identity,
)
from backend.shared.logger import logger


class IndexingPipeline:
    """现代企业级分层索引管道编排器。"""

    def __init__(self, indexer: Any):
        self.indexer = indexer
        self.parse_stage = ParseStage(
            tenant_id=getattr(indexer, "tenant_id", "default"),
            ocr_page_budget=getattr(indexer, "ocr_page_budget", None),
        )
        self.filter_stage = FilterStage(
            registry=indexer.registry,
            candidate_mode=getattr(indexer, "candidate_mode", False),
        )
        self.quality_stage = QualityStage(
            registry=indexer.registry,
            candidate_mode=getattr(indexer, "candidate_mode", False),
        )
        self.enrich_stage = EnrichStage(indexer=indexer)
        self.persist_stage = PersistStage(indexer=indexer)

    def run(
        self,
        file_path: str,
        kb_id: str,
        doc_id: str,
        file_hash: str,
        reindex_ctx: dict | None = None,
        lineage_recorder: ProcessingRunRecorder | None = None,
        identity_path: str | None = None,
    ) -> dict:
        """执行完整索引管道。"""
        ext = os.path.splitext(file_path)[1].lower()
        path_id = identity_path or file_path
        display_name = os.path.basename(path_id) or os.path.basename(file_path)
        department = self.indexer._derive_department(path_id)

        # 读取 Registry 声明元数据
        raw_doc_row = self.indexer.registry.get_by_path(path_id)
        doc_row = raw_doc_row if isinstance(raw_doc_row, Mapping) else {}
        fixture_set = str(doc_row.get("fixture_set") or getattr(self.indexer, "fixture_set", "") or "") or None
        if fixture_set not in (None, *_EVAL_FIXTURE_SETS):
            raise ValueError(f"未知评测 fixture_set: {fixture_set}")
        permission_scope = (doc_row.get("permission_scope") or "general").strip() or "general"
        version_id = str(doc_row.get("version_id") or "")
        effective_from = str(doc_row.get("effective_from") or "")
        effective_to = str(doc_row.get("effective_to") or "")
        supersedes_version_id = str(doc_row.get("supersedes_version_id") or "")
        try:
            source_priority = int(doc_row.get("source_priority") or 0)
        except (TypeError, ValueError):
            source_priority = 0

        # ── 1. Load & Parse Stage ──
        empty_err_cls = getattr(self.indexer, "ChunkingEmptyError", None)
        chunks, _qc, file_size = self.parse_stage.execute(
            file_path=file_path,
            display_name=display_name,
            ext=ext,
            kb_id=kb_id,
            department=department,
            permission_scope=permission_scope,
            lineage_recorder=lineage_recorder,
            chunking_empty_error_cls=empty_err_cls,
        )
        # 暂存当前 chunks 供外部兼容性属性与单元测试断言访问
        self.indexer._current_chunks = chunks

        # ── 2. SHA256 查重早退检查 ──
        dedup_result = self.filter_stage.check_dedup(
            file_path=file_path,
            path_id=path_id,
            file_hash=file_hash,
            doc_id=doc_id,
            kb_id=kb_id,
            department=department,
            display_name=display_name,
        )
        if dedup_result is not None:
            return dedup_result

        # ── 3. Filter Stage（分块过滤与脱敏）──
        filtered_chunks, filtered_details = self.filter_stage.execute_filter(
            chunks=chunks,
            file_path=file_path,
            path_id=path_id,
            doc_id=doc_id,
            doc_type=str(_qc.get("doc_type") or ""),
            display_name=display_name,
            lineage_recorder=lineage_recorder,
        )
        chunks = filtered_chunks
        self.indexer._current_chunks = chunks

        # ── 4. Metadata 初始化与文档全文提取 ──
        meta_span = trace_collector.start_span(
            "index_metadata",
            parent_id="index_upload",
            name="Build metadata",
            type="llm",
            kind=SpanKind.INDEX_METADATA.value,
        )
        full_text = "\n\n".join(
            ch.page_content for ch in chunks
            if ch.metadata.get("granularity") != "parent"
        )
        from backend.rag.preprocessing.metadata import detect_document_language
        from backend.rag.preprocessing.pipeline import _filename_title

        document_title = str(_qc.get("document_title") or "").strip()
        if not document_title:
            document_title = next((
                str((chunk.metadata or {}).get("document_title") or "").strip()
                for chunk in chunks
                if str((chunk.metadata or {}).get("document_title") or "").strip()
            ), "")
        if os.path.normcase(os.path.abspath(path_id)) != os.path.normcase(os.path.abspath(file_path)):
            staged_name = os.path.basename(file_path)
            staged_title = _filename_title(file_path)
            staged_title_variants = {staged_name, os.path.splitext(staged_name)[0], staged_title}
            source_title = os.path.splitext(display_name)[0] or display_name
            if not document_title or document_title in staged_title_variants:
                document_title = source_title

        doc_meta = {
            "doc_id": doc_id,
            "source_file": os.path.basename(path_id),
            "file_path": path_id,
            "kb_id": kb_id,
            "fixture_set": fixture_set or "",
            "dataset": "rag_eval" if fixture_set else "",
            "department": department,
            "tenant_id": getattr(self.indexer, "tenant_id", "default"),
            "permission_scope": permission_scope,
            "version_id": version_id,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "supersedes_version_id": supersedes_version_id,
            "source_priority": source_priority,
            "quality_status": str(doc_row.get("quality_status") or "unknown"),
            "doc_type": "general",
            "person_names": [],
            "title": document_title,
            "language": detect_document_language(full_text),
            "source_format": str(_qc.get("source_format") or ""),
            "parser_version": str(_qc.get("parser_version") or ""),
            "chunk_policy_version": str(
                ":".join(
                    str(value) for value in (
                        (_qc.get("route_decision") or {}).get("strategy"),
                        (_qc.get("route_decision") or {}).get("strategy_version"),
                    ) if value
                )
            ),
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
        collection_name = str(getattr(self.indexer.vectordb, "_collection", "") or "")
        if "::cand:" in collection_name:
            doc_meta["index_generation"] = collection_name.split("::cand:", 1)[1]
        else:
            doc_meta["index_generation"] = str(doc_row.get("active_generation") or "")
        chunks_truncated = any(ch.metadata.get("chunks_truncated") for ch in chunks)
        doc_meta["chunks_truncated"] = chunks_truncated

        # ── 5. 付费前完整性预检（0 费用阻断）与身份协议绑定 ──
        self.quality_stage.enforce_prepaid_integrity(
            _qc, chunks, path_id, doc_meta,
            doc_id=doc_id, kb_id=kb_id, file_path=file_path,
            file_size=file_size, file_hash=file_hash,
            filtered_details=filtered_details,
            truncated=chunks_truncated,
        )
        apply_index_chunk_identity(chunks, doc_id)

        # ── 6. Metadata Stage（构建元数据）──
        metadata_lineage = None
        if lineage_recorder is not None:
            metadata_lineage = lineage_recorder.begin_stage(
                "metadata", role=None, engine_type="orchestrator",
                metadata={"chunk_count": len(chunks)},
            )
        question_idx_list = question_candidate_indexes(chunks)
        try:
            metadata_kwargs = {
                "parent_span_id": meta_span.span_id,
                "chunks_text": [
                    chunks[index].page_content
                    for index in question_idx_list
                ],
            }
            if lineage_recorder is not None:
                try:
                    params = inspect.signature(self.indexer._build_doc_metadata).parameters
                    supports_lineage = (
                        "processing_recorder" in params
                        or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
                    )
                except (TypeError, ValueError):
                    supports_lineage = True
                if supports_lineage:
                    metadata_kwargs["processing_recorder"] = lineage_recorder

            from backend.rag.indexing.indexer import _run_async
            meta_result = _run_async(self.indexer._build_doc_metadata(
                full_text, doc_meta, **metadata_kwargs
            ))
            doc_meta.update(meta_result)

            id_fn = getattr(self.indexer, "_embedding_model_identity", None)
            if id_fn:
                runtime_embedding = id_fn(self.indexer.embedding)
                if runtime_embedding.model_name:
                    doc_meta["embedding_model"] = runtime_embedding.model_name
                if runtime_embedding.model_revision:
                    doc_meta["embedding_version"] = runtime_embedding.model_revision
            if metadata_lineage is not None:
                lineage_recorder.finish_stage(
                    metadata_lineage[0], status="success",
                    started_at=metadata_lineage[1], input_count=1,
                    output_count=1,
                )
        except Exception as e:
            if metadata_lineage is not None:
                lineage_recorder.finish_stage(
                    metadata_lineage[0], status="fallback",
                    started_at=metadata_lineage[1], input_count=1,
                    output_count=1, fallback_reason="metadata_default",
                    error_message=str(e),
                )
            logger.warning(f"元数据构建失败（使用默认值）: {e}")

        if chunks_truncated:
            append_quality_issue(doc_meta, "chunks_truncated(超出单文档上限被截断,检索覆盖不完整)")
        if any(ch.metadata.get("ocr_triggered") for ch in chunks):
            ocr_pages = max(
                (int(ch.metadata.get("ocr_pages") or 0) for ch in chunks),
                default=0,
            )
            append_quality_issue(doc_meta, f"ocr_triggered(扫描件经OCR识别,{ocr_pages}页产出文本)")
        f_summary = filter_quality_summary(filtered_details)
        if f_summary:
            append_quality_issue(doc_meta, f_summary)

        # ── 7. Quality Stage（门禁审计与留痕）──
        self.quality_stage.audit_quality_gate(
            qc=_qc,
            chunks=chunks,
            path_id=path_id,
            doc_meta=doc_meta,
            file_path=file_path,
            file_size=file_size,
            file_hash=file_hash,
            doc_id=doc_id,
            kb_id=kb_id,
            filtered_details=filtered_details,
            chunks_truncated=chunks_truncated,
        )

        # ── 8. Enrich Stage（语义增强与预算预检）──
        doc_level_text = self.enrich_stage.execute(
            chunks=chunks,
            doc_meta=doc_meta,
            full_text=full_text,
            path_id=path_id,
            doc_id=doc_id,
            kb_id=kb_id,
            department=department,
            permission_scope=permission_scope,
            version_id=version_id,
            effective_from=effective_from,
            effective_to=effective_to,
            supersedes_version_id=supersedes_version_id,
            source_priority=source_priority,
            question_chunk_indexes=question_idx_list,
            _qc=_qc,
            lineage_recorder=lineage_recorder,
        )

        # 关闭 metadata span
        kws_all = doc_meta.get("doc_keywords", [])
        kws_rule = doc_meta.get("keywords_rule", [])
        kws_llm = doc_meta.get("keywords_llm", [])
        llm_tokens = doc_meta.get("llm_tokens", {})
        llm_used = doc_meta.get("llm_used", False)
        metrics = {
            "doc_type": doc_meta.get("doc_type", ""),
            "keywords_rule": len(kws_rule) if isinstance(kws_rule, list) else 0,
            "keywords_llm": len(kws_llm) if isinstance(kws_llm, list) else 0,
            "keywords_total": len(kws_all) if isinstance(kws_all, list) else 0,
            "person_count": len(doc_meta.get("person_names") or []) if isinstance(doc_meta.get("person_names"), (list, tuple))
                            else (len(str(doc_meta.get("person_names")).split(",")) if doc_meta.get("person_names") else 0),
        }
        if chunks_truncated:
            metrics["chunks_truncated"] = "true"
        if llm_used:
            metrics["llm_prompt_tokens"] = llm_tokens.get("prompt_tokens", 0)
            metrics["llm_completion_tokens"] = llm_tokens.get("completion_tokens", 0)
            metrics["llm_cost_usd"] = llm_tokens.get("cost_usd", 0)
        trace_collector.end_span(
            meta_span, metrics=metrics, status="success",
            output={
                "doc_type": doc_meta.get("doc_type", ""),
                "business_domain": doc_meta.get("business_domain", ""),
                "summary": doc_meta.get("summary", ""),
                "keywords_all": kws_all if isinstance(kws_all, list) else [],
            },
        )

        # ── 9. Embedding Stage（向量化计算）──
        vector_chunks = select_vector_chunks(chunks)
        non_vector_chunk_count = len(chunks) - len(vector_chunks)
        embed_span = trace_collector.start_span(
            "index_embed",
            parent_id="index_upload",
            name=f"Embed {len(vector_chunks)} chunks",
            type="embedding",
            kind=SpanKind.INDEX_EMBED.value,
        )
        embed_span.metrics.update({
            "chunk_count": len(vector_chunks),
            "context_only_chunk_count": non_vector_chunk_count,
        })
        embedding_lineage = None
        if lineage_recorder is not None:
            id_fn = getattr(self.indexer, "_embedding_model_identity", None)
            m_id = id_fn(self.indexer.embedding) if id_fn else None
            embedding_lineage = lineage_recorder.begin_stage(
                "embedding", role="embedding", engine_type="embedding",
                model=m_id,
                metadata={"chunk_count": len(vector_chunks)},
            )
        from contextlib import nullcontext
        with lineage_recorder.bind_stage(embedding_lineage[0]) if lineage_recorder and embedding_lineage else nullcontext():
            precomputed_vectors = self.indexer._embed_with_retry(
                vector_chunks, embed_span,
                doc_summary=doc_meta.get("summary", "") or "")
        if embedding_lineage is not None:
            embedding_status = "success" if len(precomputed_vectors) == len(vector_chunks) else "fallback"
            lineage_recorder.finish_stage(
                embedding_lineage[0], status=embedding_status,
                started_at=embedding_lineage[1], input_count=len(vector_chunks),
                output_count=len(precomputed_vectors),
                cache_status="hit" if embed_span.metrics.get("embedding_cache_hit", 0) == len(vector_chunks) else "miss",
            )
        trace_collector.end_span(
            embed_span,
            metrics={"attempted": len(vector_chunks),
                     "succeeded": len(precomputed_vectors),
                     "failed": len(vector_chunks) - len(precomputed_vectors),
                     "context_only_chunks": non_vector_chunk_count},
        )

        # ── 10. Persist Stage（持久化与原子提交）──
        return self.persist_stage.execute_persist(
            chunks=chunks,
            vector_chunks=vector_chunks,
            precomputed_vectors=precomputed_vectors,
            doc_level_text=doc_level_text,
            doc_meta=doc_meta,
            full_text=full_text,
            file_path=file_path,
            path_id=path_id,
            doc_id=doc_id,
            kb_id=kb_id,
            department=department,
            file_hash=file_hash,
            ext=ext,
            reindex_ctx=reindex_ctx,
            lineage_recorder=lineage_recorder,
            chunking_empty_error_cls=empty_err_cls,
        )
