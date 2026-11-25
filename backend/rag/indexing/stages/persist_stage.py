"""Persist Stage — 多存储协同持久化、BM25 快照发布、Registry 注册与原子回滚。

从 indexer.py 抽离，负责：
1. ChunkStore 写入（SQLite / Postgres 文本备份）；
2. DocDB 写入（文档级向量与摘要入库）；
3. VectorDB 写入（分块向量入库）；
4. BM25 同步重建（以向量库为唯一真源的集合快照发布）；
5. 空 Chunk 防伪成功门禁拦截；
6. Registry 最终注册与代次元数据更新；
7. 写入失败时的原子逆向回滚（_cleanup_partial_write）。
"""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from typing import Any

from langchain_core.documents import Document

from backend.config.rag import BM25_CANDIDATE_K
from backend.rag.indexing.stages.contracts import DOC_LEVEL_TEXT_MAX_CHARS
from backend.observability.tracer import trace_collector, SpanKind
from backend.rag.indexing.processing_lineage import ModelIdentity
from backend.rag.indexing.stages.enrich_stage import select_vector_chunks
from backend.shared.logger import logger


def serialize_registry_keywords(doc_meta: dict) -> str:
    """序列化注册表展示关键词。"""
    raw = doc_meta.get("keywords")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    kws = doc_meta.get("doc_keywords") or []
    if isinstance(kws, list):
        return ", ".join(str(item).strip() for item in kws if str(item).strip())
    return ""


def add_doc_level_vector_with_lineage(
    doc_db: Any,
    text: str,
    metadata: dict,
    *,
    lineage_recorder: Any = None,
    embedding: Any = None,
    embedding_model_identity_fn: Any = None,
) -> list[str]:
    """写入文档级向量，并把真实嵌入调用绑定到当前处理运行。"""
    if not text:
        return []

    lineage = None
    if lineage_recorder is not None:
        model_id = (
            embedding_model_identity_fn(embedding)
            if embedding_model_identity_fn
            else ModelIdentity()
        )
        lineage = lineage_recorder.begin_stage(
            "document_summary_embedding",
            role="embedding",
            engine_type="embedding",
            model=model_id,
            metadata={
                "index_scope": "document_summary",
                "text_chars": len(text),
            },
        )

    step_id, started_at = lineage if lineage else (None, 0.0)
    try:
        with (
            lineage_recorder.bind_stage(step_id)
            if lineage_recorder is not None and step_id
            else nullcontext()
        ):
            ids = doc_db.add_texts(texts=[text], metadatas=[metadata])
    except Exception as exc:
        if lineage_recorder is not None and step_id:
            lineage_recorder.finish_stage(
                step_id,
                status="failed",
                started_at=started_at,
                input_count=1,
                error_message=(
                    f"{type(exc).__name__}: document_summary_embedding_failed"
                ),
            )
        raise

    if lineage_recorder is not None and step_id:
        lineage_recorder.finish_stage(
            step_id,
            status="success" if ids else "skipped",
            started_at=started_at,
            input_count=1,
            output_count=len(ids),
            skip_reason=None if ids else "no_document_summary_vector",
        )
    return ids or []


class PersistStage:
    """持久化阶段。"""

    def __init__(self, indexer: Any):
        self.indexer = indexer

    def execute_persist(
        self,
        chunks: list[Document],
        vector_chunks: list[Document],
        precomputed_vectors: list,
        doc_level_text: str,
        doc_meta: dict,
        full_text: str,
        file_path: str,
        path_id: str,
        doc_id: str,
        kb_id: str,
        department: str,
        file_hash: str,
        ext: str,
        reindex_ctx: dict | None,
        lineage_recorder: Any = None,
        chunking_empty_error_cls: type[Exception] | None = None,
    ) -> dict:
        """完成 ChunkStore、DocDB、VectorDB、BM25 和 Registry 写入。"""
        doc_type_val = doc_meta.get("doc_type", "general")
        kb_id_val = doc_meta.get("kb_id", kb_id)
        fixture_set_val = doc_meta.get("fixture_set") or None
        chunks_truncated = bool(doc_meta.get("chunks_truncated"))

        # ── 写入 chunk 文本到 SQLite / PG ──
        try:
            cs = self.indexer._cs()
            cs.delete_by_doc_id(doc_id)
            cs.insert_batch(doc_id, [
                {"chunk_index": i, "chunk_id": ch.metadata.get("chunk_id", ""),
                 "granularity": ch.metadata.get("granularity", ""),
                 "parent_chunk_id": ch.metadata.get("parent_chunk_id", ""),
                 "chunk_metadata": dict(ch.metadata),
                 "content": ch.page_content,
                 "keywords": ch.metadata.get("chunk_keywords", ""),
                 "llm_keywords": ch.metadata.get("chunk_llm_keywords", ""),
                 "llm_model": ch.metadata.get("chunk_llm_model", ""),
                 "section_title": ch.metadata.get("section_title", ""),
                 "doc_type": doc_type_val,
                 "kb_id": kb_id_val,
                 "fixture_set": fixture_set_val or "",
                 "dataset": "rag_eval" if fixture_set_val else "",
                 "department": department,
                 "simulated_questions": ch.metadata.get("simulated_questions", [])}
                for i, ch in enumerate(chunks)
            ])
        except Exception as e:
            logger.error(f"Chunk 文本写入失败: {e}")
            raise

        # ── 写入 doc_db ──
        doc_db_meta = {}
        for k, v in doc_meta.items():
            if isinstance(v, (dict, list)):
                doc_db_meta[k] = json.dumps(v, ensure_ascii=False) if v else ""
            else:
                doc_db_meta[k] = v

        doc_db_id = ""
        try:
            if len(full_text) > DOC_LEVEL_TEXT_MAX_CHARS:
                doc_db_meta["doc_level_truncated"] = "true"
                doc_db_meta["doc_level_full_chars"] = len(full_text)
            doc_embed_span = trace_collector.start_span(
                "index_doc_embed",
                parent_id="index_upload",
                name="Embed document summary",
                type="embedding",
                kind=SpanKind.INDEX_EMBED.value,
                input={
                    "text_chars": len(doc_level_text),
                    "doc_level_truncated": (
                        doc_db_meta.get("doc_level_truncated") == "true"
                    ),
                },
            )
            try:
                embedding_id_fn = getattr(
                    self.indexer, "_embedding_model_identity", None
                )
                ids = add_doc_level_vector_with_lineage(
                    self.indexer.doc_db,
                    doc_level_text,
                    doc_db_meta,
                    lineage_recorder=lineage_recorder,
                    embedding=self.indexer.embedding,
                    embedding_model_identity_fn=embedding_id_fn,
                )
            except Exception as exc:
                trace_collector.end_span(
                    doc_embed_span,
                    status="error",
                    metrics={"error": f"{type(exc).__name__}: {exc}"[:240]},
                )
                raise
            else:
                trace_collector.end_span(
                    doc_embed_span,
                    status="success" if ids else "skipped",
                    metrics={
                        "doc_vector_count": len(ids),
                        "text_chars": len(doc_level_text),
                    },
                )
            doc_db_id = ids[0] if ids else ""
        except Exception as e:
            logger.error(f"Doc 级写入失败: {e}")
            try:
                self.indexer._cs().delete_by_doc_id(doc_id)
            except Exception as cleanup_error:
                logger.warning(f"Doc 级失败后清理 chunk_store 失败: {cleanup_error}")
            raise

        # ── 写入 vector_db ──
        non_vector_chunk_count = len(chunks) - len(vector_chunks)
        vdb_span = trace_collector.start_span(
            "index_vector_db",
            parent_id="index_upload",
            name="Write to vector DB",
            type="vector_db",
            kind=SpanKind.INDEX_VECTOR_DB.value,
        )
        vector_lineage = None
        if lineage_recorder is not None:
            vector_lineage = lineage_recorder.begin_stage(
                "vector_write", role=None, engine_type="vector_db",
                metadata={"collection": getattr(self.indexer.vectordb, "_collection_name", "")},
            )
        try:
            with lineage_recorder.bind_stage(vector_lineage[0]) if lineage_recorder and vector_lineage else nullcontext():
                if vector_chunks:
                    chunk_ids = self.indexer.vectordb.add_documents(
                        vector_chunks, embeddings=precomputed_vectors) or []
                else:
                    chunk_ids = []
            trace_collector.end_span(vdb_span,
                    metrics={"written": len(chunk_ids),
                         "context_only_chunks": non_vector_chunk_count,
                         "table": getattr(self.indexer.vectordb, "_collection_name", "")})
            if vector_lineage is not None:
                lineage_recorder.finish_stage(
                    vector_lineage[0], status="success",
                    started_at=vector_lineage[1], input_count=len(vector_chunks),
                    output_count=len(chunk_ids),
                )
        except Exception as e:
            logger.error(f"Chunk 写入失败: {e}")
            trace_collector.end_span(vdb_span, status="error",
                metrics={"error": str(e)[:200]})
            self.indexer._cleanup_partial_write(doc_id, file_path=file_path,
                                                reindex_ctx=reindex_ctx,
                                                new_doc_db_id=doc_db_id)
            raise

        # ── BM25 同步 ──
        if self.indexer.bm25_store is not None and vector_chunks:
            bm25_lineage = None
            if lineage_recorder is not None:
                bm25_lineage = lineage_recorder.begin_stage(
                    "bm25_write", role=None, engine_type="bm25",
                    metadata={
                        "collection": getattr(
                            self.indexer.vectordb, "_collection", ""
                        ),
                    },
                )
            try:
                rebuild = getattr(
                    self.indexer.bm25_store, "rebuild_from_vectorstore", None
                )
                if callable(rebuild):
                    old_chunk_ids = set(
                        str(item)
                        for item in (reindex_ctx or {}).get("old_chunk_ids", [])
                    )
                    old_chunk_ids.difference_update(str(item) for item in chunk_ids)
                    bm25_source = getattr(self.indexer, "_bm25_source_vectordb", None) or self.indexer.vectordb
                    rebuild(
                        bm25_source,
                        exclude_ids=old_chunk_ids,
                        k=BM25_CANDIDATE_K,
                    )
                else:
                    self.indexer.bm25_store.replace_documents(
                        vector_chunks, k=BM25_CANDIDATE_K,
                        doc_id=doc_id, file_path=file_path,
                    )
                if bm25_lineage is not None:
                    lineage_recorder.finish_stage(
                        bm25_lineage[0], status="success",
                        started_at=bm25_lineage[1], input_count=len(vector_chunks),
                        output_count=len(vector_chunks),
                    )
            except Exception as e:
                if bm25_lineage is not None:
                    lineage_recorder.finish_stage(
                        bm25_lineage[0], status="failed",
                        started_at=bm25_lineage[1],
                        error_message=str(e),
                    )
                logger.error(f"[BM25] 快照发布失败 (doc_id={doc_id}): {e}")
                self.indexer._cleanup_partial_write(
                    doc_id, file_path=file_path,
                    reindex_ctx=reindex_ctx, new_doc_db_id=doc_db_id,
                )
                raise

        # ── 阻止 chunk_count=0 的入库 ──
        if not chunk_ids:
            error_msg = (
                f"{os.path.basename(file_path)} produced 0 chunks "
                f"(ext={ext}, parsed={len(getattr(self.indexer, '_current_chunks', []))})"
            )
            logger.warning(f"[indexer] {error_msg} — 索引失败,不上传空 doc")
            err_cls = chunking_empty_error_cls or ValueError
            raise err_cls(error_msg)

        # ── Registry 注册 ──
        lineage_registry_meta: dict[str, Any] = {}
        if lineage_recorder is not None:
            lineage_snapshot = lineage_recorder.context.snapshot()
            lineage_steps = {
                item.stage: item for item in lineage_snapshot.steps
            }
            ocr_step = lineage_steps.get("ocr")
            metadata_step = lineage_steps.get("metadata_extract")
            lineage_registry_meta = {
                "last_processing_run_id": lineage_snapshot.run_id,
                "pipeline_version": lineage_snapshot.pipeline_version,
                "metadata_route": doc_meta.get("llm_strategy", ""),
                "ocr_used": bool(ocr_step and ocr_step.status != "skipped"),
                "ocr_model": (ocr_step.model_name if ocr_step else "") or "",
                "metadata_model": (metadata_step.model_name if metadata_step else "") or "",
                "model_count": len(lineage_snapshot.model_summary),
                "processing_status": "active",
            }
        registry_meta = {
            "doc_type": doc_meta.get("doc_type", "general"),
            "confidence": doc_meta.get("confidence", 0),
            "llm_used": doc_meta.get("llm_used", False),
            "quality_score": doc_meta.get("quality_score", 0),
            "quality_issues": doc_meta.get("quality_issues", ""),
            "embedding_model": doc_meta.get("embedding_model", ""),
            "embedding_version": doc_meta.get("embedding_version", ""),
            "title": doc_meta.get("title", ""),
            "language": doc_meta.get("language", ""),
            "source_format": doc_meta.get("source_format", ""),
            "parser_version": doc_meta.get("parser_version", ""),
            "chunk_policy_version": doc_meta.get("chunk_policy_version", ""),
            "index_generation": doc_meta.get("index_generation", ""),
            "indexed_at": doc_meta.get("indexed_at", ""),
            "minhash_sig": doc_meta.get("minhash_sig", ""),
            "near_dup_id": doc_meta.get("near_dup_id", ""),
            "summary": doc_meta.get("summary", ""),
            "keywords": serialize_registry_keywords(doc_meta),
            "time_refs": json.dumps(doc_meta.get("time_refs") or [], ensure_ascii=False),
            "business_domain": doc_meta.get("business_domain", ""),
            "metadata_fingerprint": doc_meta.get("metadata_fingerprint", ""),
            "doc_version": doc_meta.get("doc_version", 1),
            "kb_version": doc_meta.get("kb_version", "v1"),
            "department": doc_meta.get("department", "") or department,
            "tenant_id": doc_meta.get("tenant_id") or getattr(self.indexer, "tenant_id", "default") or "default",
            "permission_scope": doc_meta.get("permission_scope", "general"),
            "version_id": doc_meta.get("version_id", ""),
            "effective_from": doc_meta.get("effective_from") or None,
            "effective_to": doc_meta.get("effective_to") or None,
            "supersedes_version_id": doc_meta.get("supersedes_version_id", ""),
            "source_priority": doc_meta.get("source_priority", 0),
            "quality_status": doc_meta.get("quality_status", "unknown"),
            "fixture_set": fixture_set_val or "",
            "dataset": doc_meta.get("dataset", ""),
            **lineage_registry_meta,
        }
        if getattr(self.indexer, "candidate_mode", False):
            return {
                "chunk_count": len(chunk_ids),
                "doc_db_id": doc_db_id,
                "file_hash": file_hash,
                "candidate": True,
                "chunk_ids": chunk_ids,
                "doc_type": doc_meta.get("doc_type", "general"),
                "registry_metadata": registry_meta,
            }
        try:
            self.indexer.registry.register(
                file_path=path_id,
                doc_id=doc_id,
                file_hash=file_hash,
                kb_id=kb_id,
                chunk_ids=chunk_ids,
                doc_db_id=doc_db_id,
                metadata=registry_meta,
            )
        except Exception:
            self.indexer._cleanup_partial_write(
                doc_id, file_path=file_path,
                reindex_ctx=reindex_ctx, new_doc_db_id=doc_db_id,
            )
            raise

        return {
            "chunk_count": len(chunk_ids),
            "chunk_ids": chunk_ids,
            "doc_db_id": doc_db_id,
            "file_hash": file_hash,
        }
