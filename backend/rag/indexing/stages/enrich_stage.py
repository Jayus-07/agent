"""Enrich Stage — Chunk 语义增强、实体/时间/模拟问题注入与向量预算预检。

从 indexer.py 抽离，负责：
1. 识别需向量化的 chunk 清单与模拟问题候选分块；
2. 表格行语义描述生成（table_describe）；
3. 实体抽取、时间引用、章节标题反向映射与模拟问题对齐；
4. 构造用于 doc 级检索的全文摘要文本（build_doc_level_text）；
5. 向量预算预检（preflight_index_embedding_budget）。
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import nullcontext
from typing import Any, Mapping

from langchain_core.documents import Document

from backend.rag.indexing.stages.contracts import (
    DOC_LEVEL_TEXT_MAX_CHARS,
    trace_collector,
)
from backend.observability.tracer import SpanKind
from backend.rag.indexing.stages.embedding_stage import EmbeddingStage
from backend.rag.indexing.stages.filter_stage import mask_index_text_for_pii
from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.shared.logger import logger

_DOC_LEVEL_BODY_MIN_CHARS = 2000


def select_vector_chunks(chunks: list[Document]) -> list[Document]:
    """过滤只用于上下文恢复、不应独立向量化的 Parent。"""
    return [chunk for chunk in chunks if chunk.metadata.get("vectorize") is not False]


def question_candidate_indexes(chunks: list[Document]) -> list[int]:
    """合同模拟问题只选择较长 Leaf；Parent 与短条款不产生重复问题。"""
    is_contract = any(
        (chunk.metadata or {}).get("chunk_type") == "contract_context"
        for chunk in chunks
    )
    if not is_contract:
        return list(range(len(chunks)))

    minimum_tokens = 80
    candidates = []
    for index, chunk in enumerate(chunks):
        metadata = chunk.metadata or {}
        try:
            token_count = int(metadata.get("chunk_tokens") or 0)
        except (TypeError, ValueError):
            token_count = 0
        if (
            metadata.get("granularity") == "leaf"
            and metadata.get("vectorize") is not False
            and token_count >= minimum_tokens
        ):
            candidates.append(index)
    return candidates or list(range(len(chunks)))


def build_doc_level_text(full_text: str, doc_meta: dict) -> str:
    """构造 doc 级入库文本：summary/章节头部 + 全文头 N 字。"""
    if not full_text:
        return ""
    header_parts: list[str] = []
    summary = (doc_meta.get("summary") or "").strip()
    if summary:
        header_parts.append(summary)
    sections = doc_meta.get("sections") or []
    if isinstance(sections, list) and sections:
        joined = "、".join(str(s) for s in sections[:15] if str(s).strip())
        if joined:
            header_parts.append("章节：" + joined)
    header = "\n".join(header_parts)
    if not header:
        return full_text[:DOC_LEVEL_TEXT_MAX_CHARS]
    body_budget = max(DOC_LEVEL_TEXT_MAX_CHARS - len(header), _DOC_LEVEL_BODY_MIN_CHARS)
    return header + "\n\n" + full_text[:body_budget]


def build_fixture_chunk_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """构建评测/版本隔离元数据字段。"""
    fixture_set = str(metadata.get("fixture_set") or "").strip()
    version_id = str(metadata.get("version_id") or "").strip()
    result: dict[str, Any] = {
        "fixture_set": fixture_set,
        "dataset": "rag_eval" if fixture_set else "",
    }
    if version_id:
        result["version_id"] = version_id
    return result


def preflight_index_embedding_budget(
    indexer,
    chunks: list[Document],
    doc_level_text: str,
    doc_summary: str,
    *,
    filename: str,
) -> None:
    """向量化前按实际待嵌入文本估算预算，避免写入一半后才超限。"""
    from backend.infra.llm.budget import (
        RequestBudgetExceeded,
        current_request_budget,
    )

    budget = current_request_budget()
    if budget is None:
        return

    from backend.rag.preprocessing.token_counter import count_tokens

    vector_chunks = select_vector_chunks(chunks)
    embedding_texts = [
        EmbeddingStage.embedding_text_for(chunk, doc_summary=doc_summary)
        for chunk in vector_chunks
    ]
    try:
        cached_vectors = indexer._get_embed_cache().get_many(embedding_texts)
    except Exception:
        cached_vectors = [None] * len(embedding_texts)
    if len(cached_vectors) != len(embedding_texts):
        cached_vectors = [None] * len(embedding_texts)

    estimated_tokens = count_tokens(doc_level_text) if doc_level_text else 0
    estimated_tokens += sum(
        count_tokens(text)
        for text, cached in zip(embedding_texts, cached_vectors)
        if cached is None
    )
    span = trace_collector.start_span(
        "index_embedding_budget_preflight",
        parent_id="index_upload",
        name="向量 Token 预算预检",
        type="budget",
        kind=SpanKind.INDEX_EMBED.value,
        input={
            "filename": filename,
            "vector_chunk_count": len(vector_chunks),
            "doc_level_text_chars": len(doc_level_text),
        },
    )
    try:
        budget.preflight_estimated_tokens(
            estimated_tokens,
            stage="向量化",
        )
    except RequestBudgetExceeded as exc:
        trace_collector.end_span(
            span,
            status="error",
            metrics={
                "estimated_embedding_tokens": estimated_tokens,
                "cached_chunk_count": sum(
                    cached is not None for cached in cached_vectors
                ),
                "vector_chunk_count": len(vector_chunks),
                "error": str(exc)[:240],
            },
        )
        raise
    trace_collector.end_span(
        span,
        status="success",
        metrics={
            "estimated_embedding_tokens": estimated_tokens,
            "cached_chunk_count": sum(
                cached is not None for cached in cached_vectors
            ),
            "vector_chunk_count": len(vector_chunks),
        },
    )


class EnrichStage:
    """元数据注入与 Chunk 语义增强阶段。"""

    def __init__(self, indexer: Any):
        self.indexer = indexer

    def execute(
        self,
        chunks: list[Document],
        doc_meta: dict,
        full_text: str,
        path_id: str,
        doc_id: str,
        kb_id: str,
        department: str,
        permission_scope: str,
        version_id: str,
        effective_from: str,
        effective_to: str,
        supersedes_version_id: str,
        source_priority: int,
        question_chunk_indexes: list[int],
        _qc: dict,
        lineage_recorder: Any = None,
    ) -> str:
        """增强 Chunk 元数据并完成预算预检。

        Returns:
            doc_level_text (用于后续 doc_db 向量写入)
        """
        from backend.rag.preprocessing.filter import ChunkFilter
        chunk_filter = ChunkFilter()

        doc_type_val = doc_meta.get("doc_type", "general")
        pii_safe_summary = mask_index_text_for_pii(
            str(doc_meta.get("summary") or ""), doc_type_val, chunk_filter
        )
        if doc_meta.get("summary"):
            doc_meta["summary"] = pii_safe_summary
        if isinstance(doc_meta.get("sections"), list):
            doc_meta["sections"] = [
                mask_index_text_for_pii(str(section), doc_type_val, chunk_filter)
                for section in doc_meta["sections"]
            ]
        person_val = doc_meta.get("person_names", "")

        # 章节映射：找出每个 section 标题在全文中的位置
        doc_sections = doc_meta.get("sections", []) or []
        section_positions: list[tuple[int, str]] = []
        for sec_title in doc_sections:
            idx = full_text.find(sec_title)
            if idx >= 0:
                section_positions.append((idx, sec_title))
        section_positions.sort(key=lambda x: x[0])

        kb_id_val = doc_meta.get("kb_id", kb_id)
        fixture_set_val = doc_meta.get("fixture_set") or None
        domain_val = doc_meta.get("business_domain", "") or "general"

        # 模拟问题
        generated_questions = doc_meta.get("questions_by_chunk", []) or []
        questions_by_chunk_index = {
            chunk_index: generated_questions[position]
            for position, chunk_index in enumerate(question_chunk_indexes)
            if position < len(generated_questions)
        }

        # 表格行描述
        table_row_indexes = [
            i for i, ch in enumerate(chunks)
            if ch.metadata.get("chunk_type") == "table_row"
        ]
        table_lineage = None
        if lineage_recorder is not None and not table_row_indexes:
            table_lineage = lineage_recorder.begin_stage(
                "table_describe", role="table_describe", engine_type="llm",
                skip_reason="no_table_rows",
            )
            lineage_recorder.persist_stage(table_lineage[0])
        if table_row_indexes:
            if lineage_recorder is not None:
                table_lineage = lineage_recorder.begin_stage(
                    "table_describe", role="table_describe", engine_type="llm",
                    metadata={"row_count": len(table_row_indexes)},
                )
            try:
                from backend.rag.preprocessing.table_describe import (
                    generate_table_descriptions,
                    get_last_generation_meta,
                )
                with lineage_recorder.bind_stage(table_lineage[0]) if lineage_recorder and table_lineage else nullcontext():
                    row_descs = generate_table_descriptions(
                        [chunks[i].page_content for i in table_row_indexes],
                        table_summary=doc_meta.get("summary", "") or "",
                    )
                table_result_meta = get_last_generation_meta()
                for i, desc in row_descs.items():
                    if 0 <= i < len(table_row_indexes):
                        chunks[table_row_indexes[i]].metadata["table_desc"] = desc
                if lineage_recorder is not None and table_lineage is not None:
                    table_status = str(table_result_meta.get("status") or "success")
                    if table_status not in {"success", "cached", "fallback", "skipped"}:
                        table_status = "success"
                    if table_status != "skipped":
                        lineage_recorder.set_stage_model(
                            table_lineage[0],
                            MetadataStage._role_model_identity(
                                "table_describe",
                                str(table_result_meta.get("model") or ""),
                            ),
                        )
                    from backend.config.rag import (
                        METADATA_SCHEMA_FINGERPRINT,
                        TABLE_DESC_PROMPT_VERSION,
                    )
                    lineage_recorder.finish_stage(
                        table_lineage[0], status=table_status,
                        started_at=table_lineage[1],
                        input_count=len(table_row_indexes),
                        output_count=len(row_descs),
                        cache_status=str(table_result_meta.get("cache_status") or (
                            "hit" if table_status == "cached" else "miss"
                        )),
                        fallback_reason=str(
                            table_result_meta.get("fallback_reason") or ""
                        ) or None,
                        skip_reason=str(
                            table_result_meta.get("skip_reason") or ""
                        ) or None,
                        prompt_key="rag.preprocessing.table_describe",
                        prompt_version=TABLE_DESC_PROMPT_VERSION,
                        schema_fingerprint=METADATA_SCHEMA_FINGERPRINT,
                    )
            except Exception as e:
                if lineage_recorder is not None and table_lineage is not None:
                    lineage_recorder.finish_stage(
                        table_lineage[0], status="failed",
                        started_at=table_lineage[1],
                        input_count=len(table_row_indexes),
                        error_message=str(e),
                    )
                logger.warning(f"[TableDesc] 生成失败（无描述降级）: {e}")

        # 实体与时间引用
        from backend.rag.preprocessing.entity import extract_exact_terms
        time_refs_val = doc_meta.get("time_refs") or []
        time_refs_str = ", ".join(str(t) for t in time_refs_val[:10]) if isinstance(time_refs_val, list) else str(time_refs_val)[:200]

        for i, ch in enumerate(chunks):
            ch.metadata["chunk_index"] = i
            ch.metadata["document_title"] = str(doc_meta.get("title") or "")
            ch.metadata["source_format"] = str(doc_meta.get("source_format") or "")
            ch.metadata["parser_version"] = str(doc_meta.get("parser_version") or "")
            ch.metadata["chunk_policy_version"] = str(
                doc_meta.get("chunk_policy_version") or ""
            )
            if doc_meta.get("index_generation"):
                ch.metadata["index_generation"] = str(doc_meta["index_generation"])
            ch.metadata["indexed_at"] = str(doc_meta.get("indexed_at") or "")
            if doc_meta.get("embedding_model"):
                ch.metadata["embedding_model"] = str(doc_meta["embedding_model"])
            if doc_meta.get("embedding_version"):
                ch.metadata["embedding_version"] = str(doc_meta["embedding_version"])
            ch.metadata["content_hash"] = hashlib.sha256(
                ch.page_content.encode("utf-8"),
            ).hexdigest()
            ch.metadata["doc_type"] = doc_type_val
            _chunking_type = str(_qc.get("doc_type") or "")
            if _chunking_type and _chunking_type != doc_type_val:
                ch.metadata["chunking_doc_type"] = _chunking_type
            ch.metadata["person_names"] = person_val
            ch.metadata["kb_id"] = kb_id_val
            ch.metadata.update(
                build_fixture_chunk_metadata(
                    {
                        "doc_id": doc_id,
                        "kb_id": kb_id_val,
                        "fixture_set": fixture_set_val,
                        "version_id": version_id,
                    }
                )
            )
            ch.metadata["business_domain"] = domain_val
            ch.metadata["department"] = department
            ch.metadata["tenant_id"] = str(
                doc_meta.get("tenant_id") or getattr(self.indexer, "tenant_id", "default") or "default"
            )
            ch.metadata["permission_scope"] = permission_scope
            ch.metadata["version_id"] = version_id
            ch.metadata["effective_from"] = effective_from
            ch.metadata["effective_to"] = effective_to
            ch.metadata["supersedes_version_id"] = supersedes_version_id
            ch.metadata["source_priority"] = source_priority
            ch.metadata["quality_status"] = str(
                doc_meta.get("quality_status") or "unknown"
            )
            ch.metadata["doc_id"] = doc_id
            ch.metadata["chunk_keywords"] = ""
            _sq = questions_by_chunk_index.get(i, [])
            if _sq:
                ch.metadata["simulated_questions"] = _sq
            ch.metadata["review_status"] = (
                "pending_review" if doc_meta.get("near_dup_id") else "active"
            )
            exact_terms = extract_exact_terms(ch.page_content)
            if exact_terms:
                ch.metadata["exact_terms"] = json.dumps(
                    exact_terms, ensure_ascii=False, separators=(",", ":"),
                )
            chunk_entities: dict[str, list[str]] = {}
            raw_entities = doc_meta.get("entities") or {}
            if isinstance(raw_entities, dict):
                content_folded = ch.page_content.casefold()
                for entity_type, values in raw_entities.items():
                    verified = [
                        str(value).strip()[:120]
                        for value in values or []
                        if str(value).strip()
                        and str(value).strip().casefold() in content_folded
                    ]
                    if verified:
                        chunk_entities[str(entity_type)] = verified[:32]
            if chunk_entities:
                ch.metadata["entities"] = json.dumps(
                    chunk_entities, ensure_ascii=False, separators=(",", ":"),
                )
            if time_refs_str:
                ch.metadata["time_refs"] = time_refs_str

            if section_positions and not (ch.metadata.get("section_title") or "").strip():
                chunk_start = full_text.find(ch.page_content[:80])
                section_title = ""
                for pos, title in section_positions:
                    if pos <= chunk_start >= 0:
                        section_title = title
                if section_title:
                    ch.metadata["section_title"] = section_title

        doc_level_text = mask_index_text_for_pii(
            build_doc_level_text(full_text, doc_meta),
            doc_type_val,
            chunk_filter,
        )
        preflight_index_embedding_budget(
            self.indexer,
            chunks,
            doc_level_text,
            doc_meta.get("summary", "") or "",
            filename=os.path.basename(path_id),
        )
        return doc_level_text
