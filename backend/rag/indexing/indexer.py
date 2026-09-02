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

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from backend.observability.tracer import trace_collector, WorkflowKind, SpanKind
from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.parser import PARSABLE_EXTS
from backend.rag.indexing.models import SyncResult, Delta
from backend.rag.indexing.doc_id import derive_doc_id, parse_kb_dept_subpath_from_path
from backend.config.rag import BM25_SEARCH_K, EMBED_BATCH_SIZE
from backend.shared.logger import logger
from backend.infra.async_utils import run_async as _run_async

# 单 chunk 嵌入失败时的重试上限
EMBED_RETRY_MAX = 3


class ChunkingEmptyError(Exception):
    """文档解析/切片成功但最终未产出任何有效 chunk（chunk_count=0）。

    用于 P1-4：阻止"假装索引成功但实际 doc_db 为空"的隐性失败模式。
    - 触发场景：扫描件 PDF / 纯图片 / 结构解析失败 / embedding 全失败
    - 调用方（_run_index_background）应：
        1. emit SSE error 事件（前端能看到）
        2. 不删源文件（保护已有数据，便于排查）
        3. 留痕到 operation_log（result=failed）

    设计：继承 Exception 而非 RuntimeError，避免被 observability 框架的
    `except RuntimeError` 通用 catch 误处理（如 trace 上报中的 swallow 逻辑）。
    """


# 摘要采样：按文档长度自适应，保证头尾关键信息不丢
def _sample_for_summary(text: str) -> str:
    n = len(text)
    if n <= 2000:
        return text           # 短文档全文
    if n <= 8000:
        cut = int(n * 0.6)
        return text[:cut] + "\n...(中略)...\n" + text[-int(n * 0.4):]  # 头60%+尾40%
    MAX_SAFE = 50000
    if n > MAX_SAFE:
        return text[:20000] + "\n...(中间大量细则略)...\n" + text[-20000:]  # 极端超长安全绳
    return text               # 8KB~50KB 全文（DeepSeek 1M context 完全够）


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

    # F6: 从解析器注册表派生（单一来源）；旧硬编码名单缺 .markdown，
    # 导致磁盘扫描路径与解析能力不一致
    SUPPORTED_EXTS = set(PARSABLE_EXTS)

    def __init__(
        self,
        docs_dir: str,
        vectordb: Any,
        doc_db: Any,
        embedding: Any,
        registry: Any,
        kb_id: str = "policy_general",
        department: str = "general",
        bm25_store: Any = None,
    ):
        self.docs_dir = Path(docs_dir).resolve()
        self.vectordb = vectordb
        self.doc_db = doc_db
        self.embedding = embedding
        self.registry = registry
        self.kb_id = kb_id
        self.department = department
        # 上传/重索引后立即同步 BM25（避免"上传成功但 BM25 未更新"）。
        # 启动期 sync 时 bm25_store 尚未构建（pipeline 先增量索引后建 BM25），传入 None 即跳过。
        self.bm25_store = bm25_store

    # ---- 主入口 ----

    def sync(self) -> SyncResult:
        """执行一次增量同步。

        首次运行（registry 为空）→ 所有文件视为 ADDED。
        后续运行 → 按 SHA256 diff。
        """
        disk_files = self._scan_disk()
        registry_rows = self.registry.list_all()

        # 只考虑 active 的条目（排除已标记 deleted 的）
        # 归一化路径为绝对路径：旧数据可能有相对路径，与 disk_files 的绝对路径不匹配
        active_registry = {}
        for p, r in registry_rows.items():
            if r.get("status") == "active":
                norm_path = os.path.abspath(p)
                active_registry[norm_path] = r

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

    def _index_file(self, file_path: str, file_hash: str | None = None):
        """索引单篇文档: 加载 → 解析 → 清洗 → 去重 → 分块 → 元数据 → embed → 写入。

        Args:
            file_path: 文档路径。
            file_hash: 调用方已算好的 SHA256(如上传路由 duplicate 检测时算过)。
                传入可避免对大文件重复全盘读取;缺省时内部计算。

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
        kb_id = self.kb_id if self.kb_id != "default" else self._derive_kb_id(file_path)
        file_hash = file_hash or self._sha256(file_path)
        doc_id = self._derive_doc_id(file_path, file_hash, kb_id)

        # ── 启动 indexer trace ──
        trace = trace_collector.start(
            question=os.path.basename(file_path),
            session_id="",
            workflow_name="knowledge_index",
            workflow_kind=WorkflowKind.KNOWLEDGE_INDEX.value,
        )
        trace.tags.update({"kb_id": kb_id, "doc_id": doc_id, "file_ext":
                          os.path.splitext(file_path)[1].lower(),
                          "embedding_model": os.path.basename(getattr(self.embedding, "model_name", "")) or
                                             os.path.basename(str(getattr(self.embedding, "model", ""))) or "—"})

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
            inner_result = self._index_file_inner(file_path, kb_id, doc_id, file_hash)
            trace_collector.end_span(upload_span,
                metrics={"doc_id": doc_id, "kb_id": kb_id})
            trace_collector.finish(trace, os.path.basename(file_path),
                                   upload_span.duration_ms, "", "")
            # P2-2:返回 dict 含 trace_id + chunk_count + doc_db_id,
            # 让 reindex_file 直接消费,不再反查 registry
            return {
                "trace_id": trace.id,
                "doc_id": doc_id,  # 本次派生的真实 doc_id(新文件也有值)
                "chunk_count": inner_result.get("chunk_count", 0),
                "doc_db_id": inner_result.get("doc_db_id", ""),
                "file_hash": inner_result.get("file_hash", file_hash),
                "status": "active",
            }
        except Exception as e:
            trace_collector.end_span(upload_span, status="error",
                metrics={"error": str(e)[:200]})
            try:
                trace_collector.finish(trace, "[ERROR]", upload_span.duration_ms, "", "")
            except Exception as cleanup_e:
                # 已在异常处理路径：trace 收尾失败只记录，不再覆盖原始异常
                logger.error(
                    "[Indexer] 异常路径 trace 收尾失败: %s", cleanup_e, exc_info=True,
                )
            raise

    def _index_file_inner(self, file_path: str, kb_id: str, doc_id: str, file_hash: str) -> dict:
        """_index_file 的实际工作，被 index_upload span 包裹。

        新流程: load → parse → clean → dedup → chunk → metadata → embed → vector_db
        （metadata 移到 embed 之前，标注注入 chunk 后再进向量库）

        Returns:
            dict 含 chunk_count / doc_db_id / file_hash，供 _index_file 包装返回给调用方。
            P2-2：消除 reindex_file 反查 registry 的需要。

        Raises:
            ChunkingEmptyError: 解析或 chunking 产出 0 chunk（P1-4）
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

        # ── ② parse（统一走新流水线 parse_and_chunk）──
        parse_span = trace_collector.start_span(
            "index_parse",
            parent_id="index_upload",
            name=f"Parse {os.path.basename(file_path)}",
            type="parse",
            kind=SpanKind.INDEX_PARSE.value,
            input={"file_path": file_path, "ext": ext},
        )
        chunks: list = []
        try:
            from backend.rag.preprocessing.pipeline import parse_and_chunk
            chunks = parse_and_chunk(file_path)
            if not chunks:
                # 空 chunks → 视为"无可索引内容"。
                # 改用 ChunkingEmptyError(P1-4)而非 RuntimeError,让调用方能区分：
                #   - ChunkingEmptyError = 业务失败(内容不支持/扫描件/结构损坏)
                #   - RuntimeError       = 程序 bug
                error_msg = (
                    f"{os.path.basename(file_path)} produced 0 chunks "
                    f"(ext={ext}, parser=parse_and_chunk)"
                )
                trace_collector.end_span(parse_span, status="error",
                    metrics={"error": error_msg[:200],
                             "doc_count": 0, "page_count": 0,
                             "loader": "pipeline", "ext": ext})
                logger.warning(f"[indexer] {error_msg}")
                raise ChunkingEmptyError(error_msg)
            for ch in chunks:
                ch.metadata["kb_id"] = kb_id
            # 共享给后续 clean / chunk / metadata 段使用，避免重复调用 parse_and_chunk
            self._current_chunks = chunks
            trace_collector.end_span(parse_span,
                metrics={"doc_count": len(chunks),
                         "page_count": len(chunks),
                         "loader": "pipeline", "ext": ext})
        except Exception as e:
            # P1-4:ChunkingEmptyError 是业务失败信号,直接透传给调用方做差异化处理,
            # 不被包装成 RuntimeError(避免 observability 误捕 + 丢失语义)
            if isinstance(e, ChunkingEmptyError):
                raise
            error_msg = str(e)[:200]
            trace_collector.end_span(parse_span, status="error",
                metrics={"error": error_msg, "loader": "pipeline"})
            raise RuntimeError(f"parse failed: {error_msg}") from e

        # ── ②.5 clean（文本清洗：控制字符/全角半角/HTML/PDF页眉页脚等）──
        clean_span = trace_collector.start_span(
            "index_clean",
            parent_id="index_upload",
            name=f"Clean {os.path.basename(file_path)}",
            type="clean",
            kind=SpanKind.INDEX_CLEAN.value,
            input={"doc_count": len(chunks)},
        )
        try:
            ext = os.path.splitext(file_path)[1].lower()
            source_type = "pdf" if ext == ".pdf" else "text"
            cleaner = DocumentCleaner()
            clean_changes: list[str] = []
            total_chars_before = 0
            total_chars_after = 0
            for ch in chunks:
                total_chars_before += len(ch.page_content)
                result = cleaner.clean(ch.page_content, source_type=source_type)
                ch.page_content = result.text
                total_chars_after += len(result.text)
                clean_changes.extend(result.changes)
            trace_collector.end_span(clean_span,
                metrics={"docs_cleaned": len(chunks),
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
            from backend.config import LEAF_CHUNK_TOKENS
            # 复用 parse 段的 chunks，避免重复调用 parse_and_chunk
            chunks = getattr(self, "_current_chunks", None) or []
            strategy_name = "pipeline"   # 具体策略名由 pipeline 日志输出
            chunk_size = LEAF_CHUNK_TOKENS
            chunk_overlap = 50
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
                         "filtered_out": filtered_count,
                         "chunk_size": chunk_size,
                         "chunk_overlap": chunk_overlap},
                output={"preview": [c.page_content[:100] for c in filtered_chunks[:3]],
                        "total": len(filtered_chunks),
                        "strategy": strategy_name,
                        "chunk_size": chunk_size,
                        "chunk_overlap": chunk_overlap})
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
        full_text = "\n\n".join(ch.page_content for ch in chunks)
        doc_meta = {
            "doc_id": doc_id,
            "source_file": os.path.basename(file_path),
            "file_path": file_path,
            "kb_id": kb_id,  # 用派生的 kb_id 参数，而非 self.kb_id（否则 kb 隔离失效）
            "department": self.department,
            "doc_type": "general",
            "person_names": "",
        }
        try:
            meta_result = _run_async(self._build_doc_metadata(
                full_text, doc_meta,
                parent_span_id=meta_span.span_id,
                chunks_text=[ch.page_content for ch in chunks],
            ))
            doc_meta.update(meta_result)
        except Exception as e:
            logger.warning(f"元数据构建失败（使用默认值）: {e}")

        # 注入 chunk metadata — 分层：
        #   - doc_type / person_names → 继承文档级（用于 filter）
        #   - chunk_keywords → 该 chunk 自己的规则关键词（不污染其他 chunk）
        #   - chunk_llm_keywords → Qwen 提取（仅 LLM_FORCED_TYPES 文档）
        #   - section_title → chunk 所属章节（从前面的 section 标题推导）
        from backend.rag.preprocessing.keyword import extract_rule_keywords as _chunk_kw
        from backend.rag.preprocessing.keyword import LLM_FORCED_TYPES as _CHUNK_LLM_TYPES
        doc_type_val = doc_meta.get("doc_type", "general")
        person_val = doc_meta.get("person_names", "")
        use_chunk_llm = doc_type_val in _CHUNK_LLM_TYPES
        chunk_llm_model = ""
        chunk_llm_count = 0

        # 章节映射：找出每个 section 标题在全文中的位置
        doc_sections = doc_meta.get("sections", []) or []
        section_positions: list[tuple[int, str]] = []
        for sec_title in doc_sections:
            idx = full_text.find(sec_title)
            if idx >= 0:
                section_positions.append((idx, sec_title))
        section_positions.sort(key=lambda x: x[0])

        # Qwen chunk 批量调用 — 已关闭（文档级 DeepSeek 关键词已覆盖，省去本地推理耗时）
        qwen_kws_per_chunk: list[list[str]] = [[] for _ in chunks]
        use_chunk_llm = False  # 关闭 Qwen chunk 级关键词，统一用文档级关键词
        if use_chunk_llm:  # 保留代码，后续按需开启
            from backend.rag.preprocessing.keyword import extract_chunk_keywords_qwen_batch
            logger.info(f"[Chunk] 高价值文档({doc_type_val})，启用 Qwen chunk 批量提取（{len(chunks)} chunks）")
            qwen_kws_per_chunk, chunk_llm_model = extract_chunk_keywords_qwen_batch(
                [ch.page_content for ch in chunks]
            )
            chunk_llm_count = sum(1 for kws in qwen_kws_per_chunk if kws)
            chunk_llm_model = chunk_llm_model or "qwen2.5:3b"

        kb_id_val = doc_meta.get("kb_id", self.kb_id)
        domain_val = doc_meta.get("business_domain", "") or "general"

        # 模拟问题（从 doc_meta 拿；metadata 构建阶段已写入 questions_by_chunk）
        questions_by_chunk = doc_meta.get("questions_by_chunk", []) or []

        for i, ch in enumerate(chunks):
            ch.metadata["doc_type"] = doc_type_val
            ch.metadata["person_names"] = person_val
            ch.metadata["kb_id"] = kb_id_val
            ch.metadata["business_domain"] = domain_val
            ch.metadata["department"] = self.department
            # 以 indexer 派生的 doc_id 为权威，覆盖 loader 注入的值，
            # 保证 chroma chunk.doc_id 与 doc_registry/chunk_store 完全一致
            # （避免 loader 与 indexer 两路派生分歧导致评测 doc_id 失配）。
            # 已存在文档走"复用 active 记录"分支 → doc_id 仍是旧协议 id，
            # 故重索引旧文档不会让现有评测集 relevant_docs 失效。
            ch.metadata["doc_id"] = doc_id
            ch.metadata["chunk_id"] = f"{doc_id}_{i}"
            chunk_kws = _chunk_kw(ch.page_content, doc_type=doc_type_val)
            ch.metadata["chunk_keywords"] = ", ".join(chunk_kws) if chunk_kws else ""
            # 模拟问题（按 chunk 索引对齐；空列表不写入 metadata，避免 ChromaDB 非空列表校验报错）
            _sq = questions_by_chunk[i] if i < len(questions_by_chunk) else []
            if _sq:
                ch.metadata["simulated_questions"] = _sq

            # 章节归属
            if section_positions:
                chunk_start = full_text.find(ch.page_content[:80])
                section_title = ""
                for pos, title in section_positions:
                    if pos <= chunk_start >= 0:
                        section_title = title
                if section_title:
                    ch.metadata["section_title"] = section_title

            # Qwen 关键词（从批量结果取）
            if use_chunk_llm and i < len(qwen_kws_per_chunk) and qwen_kws_per_chunk[i]:
                ch.metadata["chunk_llm_keywords"] = ", ".join(qwen_kws_per_chunk[i])
                ch.metadata["chunk_llm_model"] = chunk_llm_model

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
                 "llm_model": ch.metadata.get("chunk_llm_model", ""),
                 "section_title": ch.metadata.get("section_title", ""),
                 "doc_type": doc_type_val,
                 "kb_id": kb_id_val,
                 "department": self.department,
                 "simulated_questions": ch.metadata.get("simulated_questions", [])}
                for i, ch in enumerate(chunks)
            ])
        except Exception as e:
            logger.error(f"Chunk 文本写入失败: {e}")
            raise

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
            logger.error(f"Doc 级写入失败: {e}")
            try:
                from backend.rag.indexing.chunk_store import get_chunk_store
                get_chunk_store().delete_by_doc_id(doc_id)
            except Exception as cleanup_error:
                logger.warning(f"Doc 级失败后清理 chunk_store 失败: {cleanup_error}")
            raise

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
                    "domain_classify": doc_meta.get("domain_detail", {}),
                    "person_names": doc_meta.get("person_names", ""),
                    "complexity": complexity_val,
                    "time_refs": time_refs_val,
                    "keywords_rule": kws_rule if isinstance(kws_rule, list) else [],
                    "summary": doc_meta.get("summary", ""),
                    "sections": doc_meta.get("sections", []),
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
            self._remove_document(doc_id)
            raise

        # ── ⑤.5 BM25 同步（P0-1：上传/重索引后立即同步，避免"上传成功但 BM25 未更新"）──
        # 仅在显式传入 bm25_store 时执行（上传/重索引路径）；pipeline 启动 sync 不传（BM25 随后全量重建）。
        # 注意：chunk metadata 需含 doc_id/source_file 等字段（BM25 删除/去重依赖），_enrich 与上方注入已提供。
        if self.bm25_store is not None and chunks:
            try:
                self.bm25_store.add_documents(chunks, k=BM25_SEARCH_K)
                logger.info(
                    f"[BM25] 文档已同步 {len(chunks)} chunks: {os.path.basename(file_path)}"
                )
            except Exception as e:
                logger.error(f"[BM25] 同步失败 (doc_id={doc_id}): {e}")

        # ── ⑨ registry（始终执行，含 metadata 用于操作日志追溯）──
        # P1-4：阻止 chunk_count=0 的"假成功"入库。
        # 若直接 register 空 chunk_ids，前端 SSE 会推 done，但 doc_db 实际为空，
        # 后续 retrieve 永远召不回该文档（隐性故障）。这里 raise 让 _run_index_background
        # 走 error 分支并 emit SSE error。
        if not chunk_ids:
            error_msg = (
                f"{os.path.basename(file_path)} produced 0 chunks "
                f"(ext={ext}, parsed={len(getattr(self, '_current_chunks', []))})"
            )
            logger.warning(f"[indexer] {error_msg} — 索引失败,不上传空 doc")
            raise ChunkingEmptyError(error_msg)
        try:
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
                    "quality_score": doc_meta.get("quality_score", 0),
                    "quality_issues": doc_meta.get("quality_issues", ""),
                    "embedding_model": doc_meta.get("embedding_model", ""),
                    "minhash_sig": doc_meta.get("minhash_sig", ""),
                    "near_dup_id": doc_meta.get("near_dup_id", ""),
                    "summary": doc_meta.get("summary", ""),
                    "keywords": json.dumps(doc_meta.get("keywords") or [], ensure_ascii=False),
                    "time_refs": json.dumps(doc_meta.get("time_refs") or [], ensure_ascii=False),
                    "business_domain": doc_meta.get("business_domain", ""),
                    "complexity": json.dumps(doc_meta.get("complexity") or {}, ensure_ascii=False),
                    "metadata_fingerprint": doc_meta.get("metadata_fingerprint", ""),
                    "doc_version": doc_meta.get("doc_version", 1),
                    "kb_version": doc_meta.get("kb_version", "v1"),
                    "department": doc_meta.get("department", ""),
                },
            )
        except Exception:
            self._remove_document(doc_id)
            raise

        # P2-2:返回 dict 给 _index_file wrapper,消除 reindex_file 反查 registry 的需要
        return {
            "chunk_count": len(chunk_ids),
            "doc_db_id": doc_db_id,
            "file_hash": file_hash,
        }

    @staticmethod
    def _embed_text_for(chunk) -> str:
        """构造 embedding 文本：模拟问题前缀（Document Expansion）+ 正文。"""
        questions = chunk.metadata.get("simulated_questions", [])
        if questions:
            return "【相关问题】" + " | ".join(questions) + "\n\n" + chunk.page_content
        return chunk.page_content

    def _embed_single_with_retry(self, i: int, chunk, embed_text: str):
        """逐条 embedding（含重试 + 失败/重试 span），成功返回向量，失败返回 None。

        供批量化降级路径与不支持 embed_documents 的 embedding 实现复用，
        保留旧版"逐 chunk 失败 span"语义。
        """
        last_err = None
        for attempt in range(EMBED_RETRY_MAX):
            try:
                vec = self.embedding.embed_query(embed_text)
                if attempt > 0:
                    chunk_span = trace_collector.start_span(
                        f"embed_chunk_{i}",
                        parent_id="index_embed",
                        name=f"Embed chunk {i} retried",
                        type="embedding",
                        kind=SpanKind.INDEX_EMBED.value,
                        input={"chunk_index": i,
                               "doc_id": chunk.metadata.get("doc_id", "")},
                    )
                    chunk_span.retry_count = attempt
                    trace_collector.end_span(
                        chunk_span,
                        metrics={"attempt": attempt + 1,
                                 "retry_count": attempt},
                    )
                return vec
            except Exception as e:
                last_err = e
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
        chunk_span.retry_count = EMBED_RETRY_MAX
        trace_collector.end_span(chunk_span, status="error",
            metrics={"error": str(last_err)[:100] if last_err else "unknown",
                     "retry_count": EMBED_RETRY_MAX})
        return None

    def _embed_with_retry(self, chunks, parent_span) -> list:
        """批量嵌入（P2 批量化）；成功静默，失败单独 child span 记录。

        - 每批 EMBED_BATCH_SIZE 条调 embed_documents：本地模型批推理走矩阵
          运算，比逐条 embed_query 快数倍（对齐 chunking._embed_sentences_batched 模式）
        - 每批重试 EMBED_RETRY_MAX 次；耗尽重试的批降级逐条 embed_query，
          隔离失败点，保留逐 chunk 失败 span 语义
        - embedding 实现无 embed_documents → 直接逐条路径

        Returns: 成功嵌入的向量列表（失败的 chunk 不在此列）。

        P1: 每个 chunk 在 embedding 前拼接"模拟问题前缀"（Document Expansion），
        召回率 +10-15%（口语化提问 ↔ 书面文档的语义鸿沟）。
        """
        if not chunks:
            return []
        texts = [self._embed_text_for(c) for c in chunks]
        succeeded: list = []

        batch_embed = getattr(self.embedding, "embed_documents", None)
        if not callable(batch_embed):
            for i, chunk in enumerate(chunks):
                vec = self._embed_single_with_retry(i, chunk, texts[i])
                if vec is not None:
                    succeeded.append(vec)
            return succeeded

        for start in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch_chunks = chunks[start:start + EMBED_BATCH_SIZE]
            batch_texts = texts[start:start + EMBED_BATCH_SIZE]
            last_err = None
            batch_ok = False
            for _attempt in range(EMBED_RETRY_MAX):
                try:
                    vecs = batch_embed(batch_texts)
                    if not isinstance(vecs, (list, tuple)) or len(vecs) != len(batch_texts):
                        raise ValueError(
                            f"embed_documents 返回非法: type={type(vecs).__name__}, "
                            f"期望 {len(batch_texts)} 条向量"
                        )
                    succeeded.extend(vecs)
                    batch_ok = True
                    break
                except Exception as e:
                    last_err = e
            if batch_ok:
                continue
            # 整批耗尽重试 → 降级逐条，隔离单点失败（旧语义保留）
            logger.warning(
                f"[Embed] 批次 {start // EMBED_BATCH_SIZE}（{len(batch_texts)} chunks）"
                f"重试 {EMBED_RETRY_MAX} 次全失败 ({last_err})，降级逐条"
            )
            for j, chunk in enumerate(batch_chunks):
                vec = self._embed_single_with_retry(start + j, chunk, batch_texts[j])
                if vec is not None:
                    succeeded.append(vec)
        return succeeded


    async def _build_doc_metadata(self, full_text: str, base_meta: dict, parent_span_id: str = "", chunks_text: list[str] | None = None) -> dict:
        """异步构建文档级元数据 — LLM Decision Router 评分决策。
        
        P2-2: LLM 计算异步批处理 —— 摘要、关键词、实体抽取并发执行
        """
        try:
            from backend.rag.preprocessing.metadata import (
                classify_with_confidence, analyze_complexity,
                extract_time_refs, detect_business_domain,
            )
            from backend.config.rag import METADATA_SCHEMA_FINGERPRINT as _metadata_fp
            from backend.rag.preprocessing.keyword import extract_doc_keywords_typed
            from backend.rag.preprocessing.entity import extract_entities, extract_person_names
        except ImportError:
            return {}

        try:
            fname = base_meta.get("source_file", "")
            fpath = base_meta.get("file_path", "")
            cls_detail: dict | None = None
            domain_detail: dict | None = None

            # 质量门禁（P1）
            from backend.rag.preprocessing.metadata import assess_quality
            if parent_span_id:
                quality_span = trace_collector.start_span(
                    'quality', parent_id=parent_span_id, name="Quality check",
                    type="llm", kind=SpanKind.INDEX_QUALITY_CHECK.value,
                )
            quality = assess_quality(full_text)
            if parent_span_id:
                trace_collector.end_span(quality_span,
                    metrics={"score": quality.get("score", 0), "status": quality.get("status", "?"), "issues": quality.get("issues", [])},
                    output=quality.get("dimensions", {}))

            if not quality["passed"]:
                logger.warning(f"[Quality] 文档未通过质量门禁: {quality['issues']}")

            if parent_span_id:
                classify_span = trace_collector.start_span(
                    'classify', parent_id=parent_span_id, name="Classify",
                    type="llm", kind=SpanKind.INDEX_CLASSIFY.value,
                )
            doc_type, confidence, cls_detail = classify_with_confidence(full_text, filename=fname, file_path=fpath, return_detail=True)
            if parent_span_id:
                trace_collector.end_span(classify_span, metrics={"doc_type": doc_type, "confidence": round(confidence, 3)},
                    output=locals().get("cls_detail", {}))


            # P2-1: MinHash 语义去重 — 检查同类型文档的近似内容
            from backend.rag.preprocessing.metadata import compute_minhash, minhash_similarity, _SIMILARITY_THRESHOLD
            if parent_span_id:
                dedup_minhash_span = trace_collector.start_span(
                    'dedup_minhash', parent_id=parent_span_id, name="MinHash dedup",
                    type="llm", kind=SpanKind.INDEX_DEDUP_MINHASH.value,
                )
            minhash_sig = compute_minhash(full_text)
            if parent_span_id:
                trace_collector.end_span(dedup_minhash_span, metrics={"near_dup_id": "(see below)"})

            existing_same_type = self.registry.list_by_doc_type(doc_type)
            near_dup_id = ""
            for existing in existing_same_type:
                if existing.get("doc_id") == base_meta.get("doc_id", ""):
                    continue
                existing_sig = existing.get("minhash_sig", "")
                if existing_sig:
                    try:
                        existing_sig = json.loads(existing_sig) if isinstance(existing_sig, str) else existing_sig
                        sim = minhash_similarity(minhash_sig, existing_sig)
                        if sim > _SIMILARITY_THRESHOLD:
                            near_dup_id = existing.get("doc_id", "")
                            logger.warning(f"[MinHash] 检测到近似文档: sim={sim:.2f}, existing={near_dup_id}")
                            break
                    except Exception as e:
                        # 单个已有文档签名损坏/格式异常 → 跳过该文档继续比对（软降级），留痕
                        logger.debug(f"[MinHash] 单文档签名比对失败，跳过: {e}", exc_info=True)

            if parent_span_id:
                rule_extract_span = trace_collector.start_span(
                    'rule_extract', parent_id=parent_span_id, name="Rule extract",
                    type="llm", kind=SpanKind.INDEX_KEYWORD_RULE.value,
                )
            time_refs = extract_time_refs(full_text)
            if parent_span_id:
                trace_collector.end_span(rule_extract_span, metrics={"time_refs_count": len(time_refs or []), "domain": "(computed below)"})

            if parent_span_id:
                domain_span = trace_collector.start_span(
                    'domain_classify', parent_id=parent_span_id, name="Domain classify",
                    type="llm", kind=SpanKind.INDEX_DOMAIN_CLASSIFY.value,
                )
            domain_result = detect_business_domain(full_text, return_detail=True)
            # P1: domain_result 返回 3 元组 (primary, alternatives, detail) 当 return_detail=True
            domain, _alternatives, domain_detail = domain_result
            domain_detail = domain_detail or {}
            if parent_span_id:
                trace_collector.end_span(domain_span, metrics={"domain": domain},
                    output=domain_detail or {})
            # 低置信 LLM 复验（前置：必须在关键词/复杂度之前确定最终 doc_type）
            if confidence < 0.3 and doc_type == "general":
                try:
                    from backend.config.rag import DOC_LLM_MODEL
                    doc_type_prompt = f"""请判断以下文档的类型，从以下 14 种类型中选择一个最匹配的：
policy(制度), sop(操作流程), ad_policy(广告政策), compliance(合规), legal(法律),
contract_template(合同模板), security(安全), financial(财务), customer_data(客户数据),
product_spec(商品规格), listing(商品上架), faq(常见问题), training(培训), general(通用)

只输出类型名，不要解释。

文档开头：
{full_text[:1500]}"""
                    if DOC_LLM_MODEL:
                        from langchain_ollama import ChatOllama
                        llm_l = ChatOllama(model=DOC_LLM_MODEL, temperature=0.0, num_ctx=2048, request_timeout=20)
                        llm_type = llm_l.invoke(doc_type_prompt).content.strip()
                    else:
                        from backend.infra.llm import llm
                        llm_type = llm.invoke(doc_type_prompt).content.strip()
                    valid_types = {"policy", "sop", "ad_policy", "compliance", "legal",
                                   "contract_template", "security", "financial", "customer_data",
                                   "product_spec", "listing", "faq", "training", "general"}
                    if llm_type and llm_type.lower() in valid_types:
                        doc_type = llm_type.lower()
                        confidence = 0.7
                        logger.info(f"[Classify] LLM 复验: {doc_type}")
                except Exception as e:
                    logger.warning(f"[Classify] LLM 复验失败: {e}")

            # 规则关键词 + 复杂度 + LLM关键词（在最终 doc_type 确定之后）
            from backend.rag.preprocessing.keyword import extract_rule_keywords
            rule_kws_preview = extract_rule_keywords(full_text, doc_type=doc_type)
            complexity = analyze_complexity(full_text, len(rule_kws_preview), confidence)
            kw_result = extract_doc_keywords_typed(full_text, doc_type=doc_type,
                                                    confidence=confidence, complexity=complexity)
            person_names = extract_person_names(full_text)
            entities_nested = extract_entities(full_text)
        except Exception as e:
            logger.warning(f"[Metadata] 6步预处理失败,fallback general: {e}")
            return {"doc_type": "general"}

        # 合并关键词（兼容旧字段，新字段已是对象数组）
        kws_rule_objs = kw_result.rule_keywords  # [{"word": ..., "source": "rule"}, ...]
        kws_llm_objs = kw_result.llm_keywords    # [{"word": ..., "source": "llm"}, ...]
        kws_all_words = [k["word"] for k in kws_rule_objs + kws_llm_objs]

        # LLM 决策信息
        llm_decision = kw_result.llm_decision if hasattr(kw_result, 'llm_decision') else {}

        # ⑧ 文档摘要 + 关键词合并（自适应采样）+ P2-2: 并发执行摘要/关键词/实体
        summary = ""
        need_llm_keywords = bool(kws_llm_objs)
        need_llm_summary = len(full_text) >= 1000  # <1KB 全文当摘要，不调 LLM

        llm_generate_span = None
        if parent_span_id:
            llm_generate_span = trace_collector.start_span(
                "llm_generate", parent_id=parent_span_id, name="LLM generate (keywords+summary+entities)",
                type="llm", kind=SpanKind.INDEX_LLM_GENERATE.value,
            )

        # LLM 未调用时为空列表（向后兼容；非 LLM 路径不生成问题）
        questions_by_chunk: list[list[str]] = []
        
        # F1: 旧合并调用路径（enrich_metadata_llm 一次性产出 keywords/summary/entities/questions）
        # 已被上方并行任务取代；enriched 保持 None 使合并分支为死分支，保留待后续清理。
        enriched: dict | None = None
        if need_llm_keywords or need_llm_summary:
            # P2-2: 并发执行三个重型任务
            # F1: build_llm_summary 定义于 metadata.py（带缓存），此前导入名单遗漏导致 NameError
            from backend.rag.preprocessing.metadata import (
                enrich_metadata_llm, _extract_first_sentences, build_llm_summary,
            )
            sample = _sample_for_summary(full_text)
            
            # 定义待执行的 LLM 任务
            async def task_summary():
                """LLM 摘要生成"""
                return await build_llm_summary(sample) if len(sample) >= 2000 else (_extract_first_sentences(sample, 2), [])
            
            async def task_keywords():
                """LLM 关键词提取（已在前一步调用过 extract_doc_keywords_typed）"""
                return kw_result.llm_keywords, kw_result.llm_tokens
            
            async def task_entities():
                """实体抽取"""
                return extract_entities(full_text)
            
            # 并发执行（只调用有任务的）
            tasks = []
            if need_llm_summary:
                tasks.append(task_summary())
            if need_llm_keywords:
                tasks.append(task_keywords())
            tasks.append(task_entities())  # 始终执行实体抽取
            
            # P2-2: 并行执行，总耗时 = max(各任务耗时) 而非 sum
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 解析结果
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"[Metadata] 并发任务失败 (task={i}): {result}")
                    continue
                
                if i == 0 and need_llm_summary:  # 摘要任务
                    summ, persons = result
                    if summ:
                        summary = summ
                        if not person_names:
                            person_names = persons
                elif i == 1 and need_llm_keywords:  # 关键词任务
                    kws_llm_objs, tokens = result
                    kw_result.llm_keywords = kws_llm_objs
                    kw_result.llm_tokens = tokens
                elif i == 2 or (len(tasks) > 2 and i >= 2):  # 实体任务
                    entities_nested = result
            
            if llm_generate_span:
                total_time = sum([s.duration_ms for s in trace_collector.current().spans if hasattr(s, 'duration_ms')])
                trace_collector.end_span(llm_generate_span, status="success",
                    metrics={
                        "strategy": "parallel", "doc_size": len(full_text),
                        "need_llm_summary": need_llm_summary,
                        "need_llm_keywords": need_llm_keywords,
                        "parallel_execution": True,
                        "estimated_speedup": "3x (summary+keywords+entities concurrent)",
                    })

            if enriched:
                merged_kws = enriched.get("keywords", [])
                merged_summary = enriched.get("summary", "")
                merged_entities = enriched.get("entities", [])
                merged_tokens = enriched.get("tokens", {})
                questions_by_chunk = enriched.get("questions_by_chunk", [])
                if merged_kws:
                    kws_llm_objs = [{"word": w, "source": "llm"} for w in merged_kws]
                    kws_all_words = [k["word"] for k in kws_rule_objs + kws_llm_objs]
                    kw_result.llm_tokens = merged_tokens
                if merged_summary:
                    summary = merged_summary
                if merged_entities and not person_names:
                    person_names = [e.get("name", "") for e in merged_entities if e.get("name")]
                logger.info(f"[Enrich Merged] 合并调用成功: {len(merged_kws)}kw + summary + {len(questions_by_chunk)}chunks问题")
            # F1: enriched 恒为 None 时不再走合并分支——原 else 的"合并失败"警告
            # 在合并调用从未发起时会误报，已移除；真实失败由上方并行任务的
            # "[Metadata] 并发任务失败" 留痕。
        else:
            if llm_generate_span:
                trace_collector.end_span(llm_generate_span, status="skipped",
                    metrics={
                        "strategy": "skipped",
                        "reason": "doc < 2048 bytes, use full text as summary",
                        "doc_size": len(full_text), "threshold_bytes": 2048,
                    })

        # 兜底：<1KB 全文当摘要 / 没生成出来的剥 markdown 取前几句
        if not summary and len(full_text) <= 1000:
            summary = full_text.strip()
        elif not summary:
            from backend.rag.preprocessing.metadata import _extract_first_sentences
            summary = _extract_first_sentences(full_text, 3) or ""

        # ⑨ 章节提取（纯正则，零成本，所有文档都做）
        sections = []
        try:
            from backend.rag.preprocessing.metadata import extract_sections
            if parent_span_id:
                section_span = trace_collector.start_span(
                    'section', parent_id=parent_span_id, name="Section extract",
                    type="llm", kind=SpanKind.INDEX_SECTION.value,
                )
            sections = extract_sections(full_text, max_sections=15)
            if parent_span_id:
                trace_collector.end_span(section_span, metrics={"sections_count": len(sections or [])})

        except Exception as e:
            # 章节提取失败 → 跳过 sections 元数据（软降级），留痕
            logger.debug(f"[Indexer] 章节提取失败，跳过: {e}", exc_info=True)

        return {
            "doc_type": doc_type,
            "confidence": confidence,
            "business_domain": domain,
            "domain_detail": domain_detail,
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
            "entities": entities_nested,   # P1: 结构化实体 {person, org, regulation, ...}
            "summary": summary,
            "sections": list(sections),
            "quality_score": quality.get("score", 0),
            "quality_issues": ", ".join(quality.get("issues", [])),
            "embedding_model": os.path.basename(getattr(self.embedding, "model_name", "") or
                                                 str(getattr(self.embedding, "model", ""))) or "",
            "minhash_sig": json.dumps(minhash_sig),
            "near_dup_id": near_dup_id,
            "metadata_fingerprint": _metadata_fp,
            "doc_version": 1,
            "kb_version": "v1",
            "department": self.department,
            "questions_by_chunk": questions_by_chunk,
        }

    # ---- 公开重索引 ----

    def reindex_file(self, file_path: str, file_hash: str | None = None) -> dict:
        """公开的单文件重索引 — 删除旧向量后重新加载/分块/Embedding/写入。

        复用 _remove_document() + _index_file(),不重复实现索引逻辑。

        Args:
            file_path: 待重索引文件。
            file_hash: 调用方已算好的 SHA256,透传给 _index_file 避免重复全盘读取。

        P2-2 + P2-3 整改:
          - 不再额外 get_by_path() 反查 chunk_count(P2-2):从 _index_file() 返回 dict 取
          - 不再访问 registry._lock / _conn()(P2-3):改用 bump_doc_version 公开方法

        Returns:
            {"doc_id": str, "chunk_count": int, "file_hash": str, "status": str, "stage_elapsed": dict}
        """
        row = self.registry.get_by_path(file_path)
        old_doc_id = row.get("doc_id", "") if row else ""
        old_doc_type = row.get("doc_type", "") if row else ""

        # 1. 处理旧数据：财务文档走版本快照，其他类型直接删除
        if old_doc_id:
            if self._should_use_version_snapshot(old_doc_type, file_path):
                # 版本快照：旧 chunk 标记 is_latest=False，保留历史版本向量
                try:
                    updated = self.vectordb.update_metadata_where(
                        where={"doc_id": old_doc_id},
                        metadata_update={"is_latest": False},
                    )
                    logger.info(
                        f"[REINDEX] 版本快照: doc_id={old_doc_id}, "
                        f"标记 {updated} 个旧版 chunk is_latest=False"
                    )
                except Exception as e:
                    # 快照失败 → 兑底删除旧数据，不影响新索引
                    logger.warning(
                        f"[REINDEX] 版本快照失败，兑底删除旧数据: {e}"
                    )
                    self._remove_document(old_doc_id)
                    self.registry.mark_deleted_by_doc_id(old_doc_id)
            else:
                self._remove_document(old_doc_id)

                # 按 doc_id 软删所有行（修复重复路径导致的残余 active 行）
                deleted = self.registry.mark_deleted_by_doc_id(old_doc_id)
                logger.info(f"[REINDEX] 已清理旧数据: doc_id={old_doc_id}, rows={deleted}")

        # 2. 重新索引（_index_file 返回完整 dict，含 trace_id/chunk_count/doc_db_id）
        index_result = self._index_file(file_path, file_hash=file_hash)
        trace_id = index_result.get("trace_id", "")
        new_chunk_count = index_result.get("chunk_count", 0)
        new_doc_db_id = index_result.get("doc_db_id", "")
        new_file_hash = index_result.get("file_hash", "")

        # 2.5 bump doc_version（重索引 +1）— P2-3 用公开方法替代私有 cursor 访问
        new_version = 0
        if old_doc_id:
            new_version = self.registry.bump_doc_version(old_doc_id, delta=1)

        # 3. 汇总每阶段真实耗时（取自本次 trace 的 span），供前端展示
        stage_elapsed: dict[str, int] = {}
        try:
            from backend.observability.tracer import trace_collector as _tc
            if trace_id:
                tr = next((t for t in _tc.list(50) if t.id == trace_id), None)
                if tr:
                    for sp in tr.spans:
                        stage_elapsed[sp.span_id] = int(sp.duration_ms or 0)
        except Exception as e:
            # 阶段耗时汇总失败 → 返回空 dict（软降级），留痕；不影响索引结果
            logger.debug(f"[Indexer] 阶段耗时汇总失败: {e}", exc_info=True)
        return {
            # 优先用 _index_file 派生的真实 doc_id;旧实现返回 old_doc_id,
            # 新文件首次索引时为空串,导致操作日志/返回值丢失 doc_id
            "doc_id": index_result.get("doc_id") or old_doc_id,
            "chunk_count": new_chunk_count,
            "doc_db_id": new_doc_db_id,
            "file_hash": new_file_hash,
            "doc_version": new_version,
            "status": "active",
            "trace_id": trace_id or "",
            "stage_elapsed": stage_elapsed,
        }

    @staticmethod
    def _should_use_version_snapshot(doc_type: str, file_path: str) -> bool:
        """判断是否应使用版本快照模式（保留旧版本向量）。

        条件：
          1. doc_type == 'financial'（仅财务报表走版本快照）
          2. 文件名含报告期模式（如 2026-Q3 / 2025年第一季度）
             —— 无报告期的文件不具时间维度，快照无意义
        """
        if doc_type != "financial":
            return False
        try:
            from backend.rag.preprocessing.financial_normalizer import (
                extract_reporting_period,
            )
            reporting_period, _ = extract_reporting_period(file_path)
            return bool(reporting_period)
        except Exception:
            return False

    # ---- 删除 ----

    def _remove_document(self, doc_id: str):
        """从向量库 + chunk_store 中删除文档的所有数据。"""
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
        try:
            from backend.rag.indexing.chunk_store import get_chunk_store
            get_chunk_store().delete_by_doc_id(doc_id)
        except Exception as e:
            logger.warning(f"删除 chunk_store 失败 (doc_id={doc_id}): {e}")

    def _derive_doc_id(self, file_path: str, file_hash: str, kb_id: str) -> str:
        """生成稳定且按 (知识库, 部门, 子目录) 严格隔离的文档 ID。

        已注册的旧路径优先复用原 ID，避免升级后历史 Trace、删除链接失效。
        新文档统一使用命名空间化协议：
          md5(f"{kb_id}|{department}|[subpath|]{basename}")[:10]
          - 与 loader 注入（derive_doc_id_from_path）、评测集 relevant_docs 三方一致；
          - 彻底消除"跨目录同名文件共享 doc_id"的隐患（原 md5(basename)[:10] 协议
            被 dev 注释为"当前部署可接受"，本改动落实其预留的"叠加 kb 前缀派生"方向）；
          - 2026-09-02 子目录扩展：部门目录以下的相对子路径纳入派生，
            消除 {kb}/{dept}/子目录/ 内同名文件与平铺同名文件的碰撞
            （线上事故：写作规范反例/README.md 每次重索引都撞回顶层 README 的
            doc_id，产生重复 active 行 + 向量混淆）。平铺文件（无子目录）
            的 doc_id 与历史协议完全一致，存量不受影响。
        注意：同一 (kb_id, department, subpath) 内同名文件 = 同一物理文件 = 同一 doc（正确）；
              不同 (kb_id, department) 或不同子目录的同名文件 → 不同 doc_id（严格隔离）。
        """
        try:
            existing = self.registry.get_by_path(file_path)
        except (AttributeError, OSError, RuntimeError):
            existing = None
        # 仅复用 active 记录：deleted 记录复用会残留旧 doc_id（如清理后重传
        # 会沿用旧 md5 协议），导致与命名空间协议分裂
        if isinstance(existing, dict) and existing.get("doc_id") \
                and existing.get("status", "active") == "active":
            return str(existing["doc_id"])
        _, _, subpath = parse_kb_dept_subpath_from_path(file_path, str(self.docs_dir))
        return derive_doc_id(
            kb_id=kb_id,
            department=self.department,
            basename=os.path.basename(file_path),
            subpath=subpath,
        )

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


# Delta 已迁至 models.py
