"""IncrementalIndexer — 增量知识索引器。

文档级增量同步: 扫描磁盘 → SHA256 diff → 分类处理(新增/修改/删除/跳过)。
保持现有 Retriever 和 RAG API 完全不变。

Trace 集成（Phase 1）：
  每个 _index_file() 启动一棵 indexer trace，6 个标准 span:
    index_upload → index_parse → index_chunk → index_embed
                  → index_vector_db → index_metadata
  每文件一棵 span 树；嵌入失败的 chunk 单独 child span（默认聚合）。

用法:
    indexer = IncrementalIndexer(docs_dir, vectordb, doc_db, embedding, registry)
    result = indexer.sync()
    # result.added, result.modified, result.deleted, result.skipped
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document

from backend.rag.tracer import trace_collector, WorkflowKind, SpanKind
from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.shared.logger import logger
from backend.shared.async_utils import run_async as _run_async

# 单 chunk 嵌入失败时的重试上限
EMBED_RETRY_MAX = 3


@dataclass
class SyncResult:
    """增量同步结果。"""
    added: int = 0
    modified: int = 0
    deleted: int = 0
    skipped: int = 0

    @property
    def total_changed(self) -> int:
        return self.added + self.modified + self.deleted

    def __repr__(self) -> str:
        return (f"SyncResult(added={self.added}, modified={self.modified}, "
                f"deleted={self.deleted}, skipped={self.skipped})")


class IncrementalIndexer:
    """增量索引器。

    流程:
    1. _scan_disk() → {path: (sha256, size, mtime)}
    2. _compute_delta() → 对比 registry，分类为 ADDED/MODIFIED/DELETED/UNCHANGED
    3. _apply_delta() → 逐文件处理
       - ADDED: 加载 → 分块 → metadata → embed → 写入
       - MODIFIED: 删除旧向量 → 重新加载+写入 → 更新 registry
       - DELETED: 删除向量 → registry 标记 deleted
       - UNCHANGED: 跳过
    """

    SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx"}

    def __init__(
        self,
        docs_dir: str,
        vectordb: Any,        # KnowledgeStore (chunk 级)
        doc_db: Any,          # KnowledgeStore (doc 级)
        embedding: Any,       # HuggingFaceEmbeddings
        registry: Any,        # DocumentRegistry
    ):
        self.docs_dir = Path(docs_dir)
        self.vectordb = vectordb
        self.doc_db = doc_db
        self.embedding = embedding
        self.registry = registry

    # ---- 主入口 ----

    def sync(self) -> SyncResult:
        """执行一次增量同步。

        首次运行（registry 为空）→ 所有文件视为 ADDED。
        后续运行 → 按 SHA256 diff。
        """
        disk_files = self._scan_disk()
        registry_rows = self.registry.list_all()

        # 只考虑 active 且路径在磁盘上的（排除已标记 deleted 的）
        active_registry = {
            p: r for p, r in registry_rows.items()
            if r.get("status") == "active"
        }

        delta = self._compute_delta(disk_files, active_registry)
        self._apply_delta(delta, disk_files, active_registry)

        result = SyncResult(
            added=len(delta.added),
            modified=len(delta.modified),
            deleted=len(delta.deleted),
            skipped=len(delta.unchanged),
        )
        logger.info(f"增量索引完成: {result}")
        return result

    # ---- 磁盘扫描 ----

    def _scan_disk(self) -> dict[str, tuple[str, int, float]]:
        """递归遍历 docs_dir，计算每个文件的 SHA256。

        Returns:
            {file_path: (sha256_hex, file_size, file_mtime)}
        """
        result: dict[str, tuple[str, int, float]] = {}

        for root, _dirs, files in os.walk(self.docs_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in self.SUPPORTED_EXTS:
                    continue

                file_path = os.path.join(root, fname)
                try:
                    stat = os.stat(file_path)
                    file_hash = self._sha256(file_path)
                    result[file_path] = (file_hash, stat.st_size, stat.st_mtime)
                except OSError as e:
                    logger.warning(f"无法读取文件 {file_path}: {e}")

        return result

    @staticmethod
    def _sha256(file_path: str) -> str:
        """计算文件的 SHA256 哈希。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    # ---- Diff 计算 ----

    def _compute_delta(
        self,
        disk: dict[str, tuple[str, int, float]],
        registry: dict[str, dict],
    ) -> Delta:
        """对比磁盘和注册表，分类文档。"""
        disk_paths = set(disk.keys())
        registry_paths = set(registry.keys())

        added = disk_paths - registry_paths
        deleted = registry_paths - disk_paths

        modified: set[str] = set()
        unchanged: set[str] = set()
        for p in disk_paths & registry_paths:
            if disk[p][0] != registry[p]["file_hash"]:
                modified.add(p)
            else:
                unchanged.add(p)

        return Delta(
            added=sorted(added),
            modified=sorted(modified),
            deleted=sorted(deleted),
            unchanged=sorted(unchanged),
        )

    # ---- Delta 应用 ----

    def _apply_delta(
        self,
        delta: Delta,
        disk_files: dict,
        registry: dict,
    ):
        """逐文件处理增量变更。"""
        # 删除
        for path in delta.deleted:
            row = registry.get(path, {})
            doc_id = row.get("doc_id", "")
            if doc_id:
                self._remove_document(doc_id)
            self.registry.mark_deleted(path)
            logger.info(f"[DELETED] {os.path.basename(path)}")

        # 修改: 先删后加
        for path in delta.modified:
            row = registry.get(path, {})
            doc_id = row.get("doc_id", "")
            if doc_id:
                self._remove_document(doc_id)
            self._index_file(path)
            logger.info(f"[MODIFIED] {os.path.basename(path)}")

        # 新增
        for path in delta.added:
            self._index_file(path)
            logger.info(f"[ADDED] {os.path.basename(path)}")

    # ---- 单文件索引 ----

    def _index_file(self, file_path: str):
        """索引单篇文档: 加载 → 解析 → 清洗 → 去重 → 分块 → 元数据 → embed → 写入。

        Trace 树（每文件一棵）：
          index_upload (root)
          ├── index_load
          ├── index_parse
          ├── index_clean
          ├── index_dedup
          ├── index_chunk
          ├── index_metadata（LLM 标注 → 注入 chunk → 再 embed）
          ├── index_embed（成功静默，失败单独 child span）
          └── index_vector_db（chunks 带完整 metadata 写入）
        """
        kb_id = self._derive_kb_id(file_path)
        doc_id = hashlib.md5(os.path.basename(file_path).encode()).hexdigest()[:10]
        file_hash = self._sha256(file_path)

        # ── 启动 indexer trace ──
        trace = trace_collector.start(
            question=os.path.basename(file_path),
            session_id="",
            workflow_name="knowledge_index",
            workflow_kind=WorkflowKind.KNOWLEDGE_INDEX.value,
        )
        trace.tags.update({"kb_id": kb_id, "doc_id": doc_id, "file_ext":
                          os.path.splitext(file_path)[1].lower()})

        # ── ① upload (root) ──
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0
        upload_span = trace_collector.start_span(
            "index_upload",
            parent_id=None,
            name=f"Index {os.path.basename(file_path)}",
            type="workflow",
            kind=SpanKind.INDEX_UPLOAD.value,
            input={"file_path": file_path, "size_bytes": file_size},
        )
        try:
            self._index_file_inner(file_path, kb_id, doc_id, file_hash)
            trace_collector.end_span(upload_span,
                metrics={"doc_id": doc_id, "kb_id": kb_id})
            trace_collector.finish(trace, os.path.basename(file_path),
                                   upload_span.duration_ms, "", "")
            return trace.id
        except Exception as e:
            trace_collector.end_span(upload_span, status="error",
                metrics={"error": str(e)[:200]})
            try:
                trace_collector.finish(trace, "[ERROR]", upload_span.duration_ms, "", "")
            except Exception:
                pass
            raise

    def _index_file_inner(self, file_path: str, kb_id: str, doc_id: str, file_hash: str):
        """_index_file 的实际工作，被 index_upload span 包裹。

        新流程: load → parse → clean → dedup → chunk → metadata → embed → vector_db
        （metadata 移到 embed 之前，标注注入 chunk 后再进向量库）
        """
        ext = os.path.splitext(file_path)[1].lower()

        # ── ① load（文件读取/元数据收集）──
        load_span = trace_collector.start_span(
            "index_load",
            parent_id="index_upload",
            name=f"Load {os.path.basename(file_path)}",
            type="load",
            kind=SpanKind.INDEX_LOAD.value,
            input={"file_path": file_path, "ext": ext},
        )
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0
        trace_collector.end_span(load_span,
            metrics={"file_size": file_size, "ext": ext})

        # ── ② parse（格式解析：PDF/DOCX/TXT → Documents）──
        parse_span = trace_collector.start_span(
            "index_parse",
            parent_id="index_upload",
            name=f"Parse {os.path.basename(file_path)}",
            type="parse",
            kind=SpanKind.INDEX_PARSE.value,
            input={"file_path": file_path, "ext": ext},
        )
        parse_failed = False
        parse_error_msg = ""
        try:
            if ext == ".pdf":
                try:
                    from langchain_community.document_loaders import PyPDFLoader
                    loader = PyPDFLoader(file_path)
                    raw_docs = loader.load()
                except Exception as e:
                    logger.error(f"PDF 加载失败 {file_path}: {e}")
                    parse_failed = True
                    parse_error_msg = str(e)[:200]
                    raw_docs = []
            elif ext == ".docx":
                try:
                    from langchain_community.document_loaders import Docx2txtLoader
                    loader = Docx2txtLoader(file_path)
                    raw_docs = loader.load()
                except Exception as e:
                    logger.error(f"DOCX 加载失败 {file_path}: {e}")
                    parse_failed = True
                    parse_error_msg = str(e)[:200]
                    raw_docs = []
            else:
                loader = TextLoader(file_path, encoding="utf-8")
                raw_docs = loader.load()

            if parse_failed:
                trace_collector.end_span(parse_span, status="error",
                    metrics={"error": parse_error_msg})
                raise RuntimeError(f"parse failed: {parse_error_msg}")

            if not raw_docs:
                logger.warning(f"文件为空，跳过: {file_path}")
                trace_collector.end_span(parse_span,
                    metrics={"doc_count": 0}, status="skipped")
                return

            for d in raw_docs:
                d.metadata["kb_id"] = kb_id

            trace_collector.end_span(parse_span,
                metrics={"doc_count": len(raw_docs),
                         "page_count": len(raw_docs)})
        except RuntimeError:
            # parse 失败已记录 + raise，让 _index_file wrapper 标 index_upload=error
            raise
        except Exception as e:
            trace_collector.end_span(parse_span, status="error",
                metrics={"error": str(e)[:200]})
            raise

        # ── ②.5 clean（文本清洗：控制字符/全角半角/HTML/PDF页眉页脚等）──
        clean_span = trace_collector.start_span(
            "index_clean",
            parent_id="index_upload",
            name=f"Clean {os.path.basename(file_path)}",
            type="clean",
            kind=SpanKind.INDEX_CLEAN.value,
            input={"doc_count": len(raw_docs)},
        )
        try:
            ext = os.path.splitext(file_path)[1].lower()
            source_type = "pdf" if ext == ".pdf" else "text"
            cleaner = DocumentCleaner()
            clean_changes: list[str] = []
            total_chars_before = 0
            total_chars_after = 0
            for d in raw_docs:
                total_chars_before += len(d.page_content)
                result = cleaner.clean(d.page_content, source_type=source_type)
                d.page_content = result.text
                total_chars_after += len(result.text)
                clean_changes.extend(result.changes)
            trace_collector.end_span(clean_span,
                metrics={"docs_cleaned": len(raw_docs),
                         "chars_before": total_chars_before,
                         "chars_after": total_chars_after,
                         "operations": ", ".join(clean_changes) if clean_changes else "none"},
            )
        except Exception as e:
            trace_collector.end_span(clean_span, status="error",
                metrics={"error": str(e)[:200]})
            # 清洗失败不阻塞后续流程，使用原始文本继续
            logger.warning(f"[Clean] 清洗失败，继续使用原始文本: {e}")

        # ── ④ dedup（SHA256 缓存检查）──
        dedup_span = trace_collector.start_span(
            "index_dedup",
            parent_id="index_upload",
            name=f"Check {os.path.basename(file_path)}",
            type="dedup",
            kind=SpanKind.INDEX_DEDUP.value,
            input={"file_hash": file_hash},
        )
        dup_check = self.registry.get_by_path(file_path)
        if dup_check and dup_check.get("file_hash") == file_hash and dup_check.get("status") == "active":
            trace_collector.end_span(dedup_span,
                metrics={"cached": True, "existing_doc_id": dup_check.get("doc_id", "")})
            logger.info(f"[Dedup] 文件未变更，跳过索引: {file_path}")
            return
        trace_collector.end_span(dedup_span,
            metrics={"cached": False})

        # ── ⑤ chunk（文本分块 + 质量过滤）──
        chunk_span = trace_collector.start_span(
            "index_chunk",
            parent_id="index_upload",
            name=f"Chunk {os.path.basename(file_path)}",
            type="chunk",
            kind=SpanKind.INDEX_CHUNK.value,
        )
        try:
            from backend.rag.preprocessing.loader import split_documents
            chunks = split_documents(raw_docs, file_path)
            for i, ch in enumerate(chunks):
                ch.metadata["doc_id"] = doc_id
                ch.metadata["chunk_index"] = i
                ch.metadata["source_file"] = os.path.basename(file_path)
                ch.metadata["file_path"] = file_path

            from backend.rag.preprocessing.filter import ChunkFilter
            chunk_filter = ChunkFilter()
            filtered_chunks = []
            filtered_count = 0
            for chunk in chunks:
                ok, reason = chunk_filter.should_keep(chunk.page_content, chunk.metadata)
                if ok:
                    if chunk.metadata.get("pii_masked"):
                        chunk.page_content = ChunkFilter.apply_pii_mask(chunk.page_content)
                    filtered_chunks.append(chunk)
                else:
                    filtered_count += 1
                    logger.debug(f"[Filter] 拒绝 chunk: {reason} (doc={file_path})")
            if filtered_count > 0:
                logger.info(f"[Filter] {file_path}: 过滤 {filtered_count}/{len(chunks)} 个 chunk")
            chunks = filtered_chunks

            trace_collector.end_span(chunk_span,
                metrics={"raw_chunks": len(chunks),
                         "kept_chunks": len(filtered_chunks),
                         "filtered_out": filtered_count},
                output={"preview": [c.page_content[:100] for c in filtered_chunks[:3]],
                        "total": len(filtered_chunks)})
        except Exception as e:
            trace_collector.end_span(chunk_span, status="error",
                metrics={"error": str(e)[:200]})
            raise

        # ── ⑥ metadata（LLM 元数据生成 — 移到 embed 之前，注入 chunk 再进向量库）──
        meta_span = trace_collector.start_span(
            "index_metadata",
            parent_id="index_upload",
            name="Build metadata",
            type="llm",
            kind=SpanKind.INDEX_METADATA.value,
        )
        full_text = "\n\n".join(d.page_content for d in raw_docs)
        doc_meta = {
            "doc_id": doc_id,
            "source_file": os.path.basename(file_path),
            "file_path": file_path,
            "kb_id": kb_id,
            "doc_type": "general",
            "person_names": "",
        }
        try:
            meta_result = _run_async(self._build_doc_metadata(full_text, doc_meta))
            doc_meta.update(meta_result)
        except Exception as e:
            logger.warning(f"元数据构建失败（使用默认值）: {e}")

        # 注入 chunk metadata — 分层：
        #   - doc_type / person_names → 继承文档级（用于 filter）
        #   - chunk_keywords → 该 chunk 自己的规则关键词（不污染其他 chunk）
        #   - chunk_llm_keywords → Qwen 提取（仅 LLM_FORCED_TYPES 文档）
        from backend.rag.preprocessing.keyword import extract_rule_keywords as _chunk_kw
        from backend.rag.preprocessing.keyword import LLM_FORCED_TYPES as _CHUNK_LLM_TYPES
        doc_type_val = doc_meta.get("doc_type", "general")
        person_val = doc_meta.get("person_names", "")
        use_chunk_llm = doc_type_val in _CHUNK_LLM_TYPES
        chunk_llm_model = ""
        chunk_llm_count = 0

        if use_chunk_llm:
            from backend.rag.preprocessing.keyword import extract_chunk_keywords_qwen
            logger.info(f"[Chunk] 高价值文档({doc_type_val})，启用 Qwen chunk 关键词提取")

        for ch in chunks:
            ch.metadata["doc_type"] = doc_type_val
            ch.metadata["person_names"] = person_val
            chunk_kws = _chunk_kw(ch.page_content, doc_type=doc_type_val)
            ch.metadata["chunk_keywords"] = ", ".join(chunk_kws) if chunk_kws else ""

            # Qwen chunk 关键词（仅高价值文档）
            if use_chunk_llm:
                qwen_kws, qwen_model = extract_chunk_keywords_qwen(ch.page_content)
                if qwen_kws:
                    ch.metadata["chunk_llm_keywords"] = ", ".join(qwen_kws)
                    ch.metadata["chunk_llm_model"] = qwen_model
                    chunk_llm_model = qwen_model
                    chunk_llm_count += 1

        # 记录到 trace（index_metadata span 的 metrics 里）
        if use_chunk_llm:
            chunk_llm_model = chunk_llm_model or "qwen2.5:3b"
            logger.info(f"[Chunk] Qwen 完成：{chunk_llm_count}/{len(chunks)} chunks 成功，模型={chunk_llm_model}")

        # ── 写入 chunk 文本到 SQLite（供 trace 详情页查看完整 chunk 内容）──
        try:
            from backend.rag.indexing.chunk_store import get_chunk_store
            cs = get_chunk_store()
            cs.delete_by_doc_id(doc_id)  # reindex 时先清旧数据
            cs.insert_batch(doc_id, [
                {"chunk_index": i, "content": ch.page_content,
                 "keywords": ch.metadata.get("chunk_keywords", ""),
                 "llm_keywords": ch.metadata.get("chunk_llm_keywords", ""),
                 "llm_model": ch.metadata.get("chunk_llm_model", "")}
                for i, ch in enumerate(chunks)
            ])
        except Exception as e:
            logger.warning(f"Chunk 文本写入失败（非致命）: {e}")

        # ── 构建 metadata output（独立于 doc_db 写入，确保 trace 中始终可见）──
        kws_all = doc_meta.get("doc_keywords", [])
        kws_rule = doc_meta.get("keywords_rule", [])
        kws_llm = doc_meta.get("keywords_llm", [])
        llm_tokens = doc_meta.get("llm_tokens", {})
        llm_used = doc_meta.get("llm_used", False)
        # 展平复杂对象（ChromaDB 不支持嵌套 dict）
        complexity_val = doc_meta.get("complexity", {})
        time_refs_val = doc_meta.get("time_refs", [])
        # 写入 doc_db 前做深拷贝并展平嵌套字段（ChromaDB 不支持 dict/list metadata）
        doc_db_meta = {}
        for k, v in doc_meta.items():
            if isinstance(v, (dict, list)):
                doc_db_meta[k] = json.dumps(v, ensure_ascii=False) if v else ""
            else:
                doc_db_meta[k] = v

        doc_db_id = ""
        try:
            ids = self.doc_db.add_texts(texts=[full_text], metadatas=[doc_db_meta]) if full_text else []
            doc_db_id = ids[0] if ids else ""
        except Exception as e:
            logger.warning(f"Doc 级写入失败（非致命）: {e}")
            # 不阻塞 — trace span 仍会包含完整 output

        metrics = {
            "doc_type": doc_meta.get("doc_type", ""),
            "keywords_rule": len(kws_rule) if isinstance(kws_rule, list) else 0,
            "keywords_llm": len(kws_llm) if isinstance(kws_llm, list) else 0,
            "keywords_total": len(kws_all) if isinstance(kws_all, list) else 0,
            "person_count": len(doc_meta.get("person_names", "").split(",")) if doc_meta.get("person_names") else 0,
            "doc_db_id": doc_db_id,
        }
        if llm_used:
            metrics["llm_prompt_tokens"] = llm_tokens.get("prompt_tokens", 0)
            metrics["llm_completion_tokens"] = llm_tokens.get("completion_tokens", 0)
            metrics["llm_cost_usd"] = llm_tokens.get("cost_usd", 0)
        if chunk_llm_count > 0:
            metrics["chunk_llm_count"] = chunk_llm_count
            metrics["chunk_llm_model"] = chunk_llm_model
        if not doc_db_id:
            metrics["doc_db_write"] = "failed"

        # 始终输出完整 metadata（无论 doc_db 写入是否成功）
        trace_collector.end_span(meta_span, metrics=metrics,
            status="success" if doc_db_id else "skipped",
            output={
                "rule_metadata": {
                    "doc_type": doc_meta.get("doc_type", ""),
                    "confidence": doc_meta.get("confidence", 0),
                    "business_domain": doc_meta.get("business_domain", ""),
                    "person_names": doc_meta.get("person_names", ""),
                    "complexity": complexity_val,
                    "time_refs": time_refs_val,
                    "keywords_rule": kws_rule if isinstance(kws_rule, list) else [],
                },
                "llm_metadata": {
                    "llm_used": llm_used,
                    "llm_strategy": doc_meta.get("llm_strategy", ""),
                    "llm_decision": doc_meta.get("llm_decision", {}),
                    "llm_tokens": llm_tokens,
                    "keywords_llm": kws_llm if isinstance(kws_llm, list) else [],
                },
                "keywords_all": kws_all if isinstance(kws_all, list) else [],
                "doc_type": doc_meta.get("doc_type", ""),
                "business_domain": doc_meta.get("business_domain", ""),
                "person_names": doc_meta.get("person_names", ""),
            })

        # ── ⑦ embed（聚合；chunks 已带完整 metadata）──
        embed_span = trace_collector.start_span(
            "index_embed",
            parent_id="index_upload",
            name=f"Embed {len(chunks)} chunks",
            type="embedding",
            kind=SpanKind.INDEX_EMBED.value,
        )
        embed_span.metrics["chunk_count"] = len(chunks)
        chunk_ids = self._embed_with_retry(chunks, embed_span)
        try:
            trace_collector.end_span(embed_span,
                metrics={"attempted": len(chunks),
                         "succeeded": len(chunk_ids),
                         "failed": len(chunks) - len(chunk_ids)})
        except Exception as e:
            trace_collector.end_span(embed_span, status="error",
                metrics={"error": str(e)[:200]})
            raise

        # ── ⑤ vector_db ──
        vdb_span = trace_collector.start_span(
            "index_vector_db",
            parent_id="index_upload",
            name="Write to vector DB",
            type="vector_db",
            kind=SpanKind.INDEX_VECTOR_DB.value,
        )
        try:
            if chunks:
                chunk_ids = self.vectordb.add_documents(chunks) or []
            else:
                chunk_ids = []
            trace_collector.end_span(vdb_span,
                metrics={"written": len(chunk_ids),
                         "table": getattr(self.vectordb, "_collection_name", "")})
        except Exception as e:
            logger.error(f"Chunk 写入失败: {e}")
            trace_collector.end_span(vdb_span, status="error",
                metrics={"error": str(e)[:200]})
            return

        # ── ⑨ registry（始终执行，含 metadata 用于操作日志追溯）──
        self.registry.register(
            file_path=file_path,
            doc_id=doc_id,
            file_hash=file_hash,
            kb_id=kb_id,
            chunk_ids=chunk_ids,
            doc_db_id=doc_db_id,
            metadata={
                "doc_type": doc_meta.get("doc_type", "general"),
                "confidence": doc_meta.get("confidence", 0),
                "llm_used": doc_meta.get("llm_used", False),
            },
        )

    def _embed_with_retry(self, chunks, parent_span) -> list[str]:
        """逐 chunk 嵌入；成功静默，失败单独 child span 记录，重试 EMBED_RETRY_MAX 次。

        Returns: 成功嵌入的 chunk id 列表（失败的 chunk 不在此列）。
        """
        succeeded = []
        for i, chunk in enumerate(chunks):
            last_err = None
            for attempt in range(EMBED_RETRY_MAX):
                try:
                    cid = self.embedding.embed_query(chunk.page_content)
                    succeeded.append(cid)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
            else:
                # 所有重试都失败 → 创建 child span 记录失败
                logger.error(f"[Embed] chunk {i} 嵌入失败 {EMBED_RETRY_MAX} 次: {last_err}")
                chunk_span = trace_collector.start_span(
                    f"embed_chunk_{i}",
                    parent_id="index_embed",
                    name=f"Embed chunk {i} FAILED",
                    type="embedding",
                    kind=SpanKind.INDEX_EMBED.value,
                    input={"chunk_index": i,
                           "doc_id": chunk.metadata.get("doc_id", "")},
                )
                trace_collector.end_span(chunk_span, status="error",
                    metrics={"error": str(last_err)[:100] if last_err else "unknown",
                             "retry_count": EMBED_RETRY_MAX})
        return succeeded

    async def _build_doc_metadata(self, full_text: str, base_meta: dict) -> dict:
        """异步构建文档级元数据 — LLM Decision Router 评分决策。"""
        try:
            from backend.rag.preprocessing.metadata import (
                classify_with_confidence, analyze_complexity,
                extract_time_refs, detect_business_domain,
            )
            from backend.rag.preprocessing.keyword import extract_doc_keywords_typed
            from backend.rag.preprocessing.entity import extract_person_names
        except ImportError:
            return {}

        try:
            fname = base_meta.get("source_file", "")
            fpath = base_meta.get("file_path", "")
            doc_type, confidence = classify_with_confidence(full_text, filename=fname, file_path=fpath)
            time_refs = extract_time_refs(full_text)
            domain = detect_business_domain(full_text)
            # 先跑规则关键词获取数量，用于复杂度分析
            from backend.rag.preprocessing.keyword import extract_rule_keywords
            rule_kws_preview = extract_rule_keywords(full_text, doc_type=doc_type)
            complexity = analyze_complexity(full_text, len(rule_kws_preview), confidence)
            kw_result = extract_doc_keywords_typed(full_text, doc_type=doc_type,
                                                    confidence=confidence, complexity=complexity)
            person_names = extract_person_names(full_text)
        except Exception:
            return {"doc_type": "general"}

        # 合并关键词（兼容旧字段，新字段已是对象数组）
        kws_rule_objs = kw_result.rule_keywords  # [{"word": ..., "source": "rule"}, ...]
        kws_llm_objs = kw_result.llm_keywords    # [{"word": ..., "source": "llm"}, ...]
        kws_all_words = [k["word"] for k in kws_rule_objs + kws_llm_objs]

        # LLM 决策信息
        llm_decision = kw_result.llm_decision if hasattr(kw_result, 'llm_decision') else {}

        return {
            "doc_type": doc_type,
            "confidence": confidence,
            "business_domain": domain,
            "time_refs": time_refs,
            "complexity": complexity,
            "doc_keywords": kws_all_words,
            "keywords_rule": kws_rule_objs,
            "keywords_llm": kws_llm_objs,
            "llm_tokens": kw_result.llm_tokens,
            "llm_used": bool(kws_llm_objs),
            "llm_strategy": kw_result.llm_strategy,
            "llm_decision": llm_decision,
            "person_names": ", ".join(person_names) if isinstance(person_names, list)
                           else str(person_names),
        }

    # ---- 公开重索引 ----

    def reindex_file(self, file_path: str) -> dict:
        """公开的单文件重索引 — 删除旧向量后重新加载/分块/Embedding/写入。

        复用 _remove_document() + _index_file()，不重复实现索引逻辑。

        Returns:
            {"doc_id": str, "chunk_count": int, "file_hash": str, "status": str}
        """
        row = self.registry.get_by_path(file_path)
        old_doc_id = row.get("doc_id", "") if row else ""

        # 1. 删除旧向量
        if old_doc_id:
            self._remove_document(old_doc_id)
            logger.info(f"[REINDEX] 已清理旧向量: {old_doc_id}")

        # 2. 重新索引（_index_file 返回 trace_id，供操作日志关联链路追踪）
        trace_id = self._index_file(file_path)

        # 3. 获取更新后的信息
        updated = self.registry.get_by_path(file_path) or {}
        return {
            "doc_id": updated.get("doc_id", ""),
            "chunk_count": updated.get("chunk_count", 0),
            "file_hash": updated.get("file_hash", ""),
            "status": updated.get("status", "active"),
            "trace_id": trace_id or "",
        }

    # ---- 删除 ----

    def _remove_document(self, doc_id: str):
        """从两个向量库中删除文档的所有数据。"""
        if not doc_id:
            return
        try:
            self.vectordb.delete(where={"doc_id": doc_id})
        except Exception as e:
            logger.warning(f"删除 chunk 向量失败 (doc_id={doc_id}): {e}")
        try:
            self.doc_db.delete(where={"doc_id": doc_id})
        except Exception as e:
            logger.warning(f"删除 doc 向量失败 (doc_id={doc_id}): {e}")

    # ---- KB ID 推导 ----

    def _derive_kb_id(self, file_path: str) -> str:
        """从文件路径推导 kb_id（与 loader.py 逻辑一致）。

        第一级子目录名 = kb_id，根目录 = 'default'。
        """
        rel = os.path.relpath(file_path, self.docs_dir)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) > 1:
            return parts[0]
        return "default"


@dataclass
class Delta:
    """增量 diff 结果。"""
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
