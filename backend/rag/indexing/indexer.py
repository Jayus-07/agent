"""IncrementalIndexer — 增量知识索引器。

文档级增量同步: 扫描磁盘 → SHA256 diff → 分类处理(新增/修改/删除/跳过)。
保持现有 Retriever 和 RAG API 完全不变。

Trace 集成（Phase 1）：
  每个 _index_file() 启动一棵 indexer trace，包含向量与 BM25 发布阶段:
    index_upload → index_parse → index_chunk → index_embed
                  → index_vector_db → bm25_write → index_metadata
  每文件一棵 span 树；嵌入失败的 chunk 单独 child span（默认聚合）。

用法:
    indexer = IncrementalIndexer(docs_dir, vectordb, doc_db, embedding, registry)
    result = indexer.sync()
    # result.added, result.modified, result.deleted, result.skipped
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from datetime import datetime, timezone
from dataclasses import replace
from contextlib import nullcontext
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from backend.observability.tracer import trace_collector, WorkflowKind, SpanKind
from backend.rag.preprocessing.parser import PARSABLE_EXTS
from backend.rag.indexing.models import SyncResult, Delta
from backend.rag.indexing.doc_id import derive_doc_id, parse_kb_dept_subpath_from_path
from backend.config.rag import (
    BM25_CANDIDATE_K,
)
from backend.shared.logger import logger
from backend.infra.async_utils import run_async as _run_async

# 阶段3+4 Stage 拆分：管线按单一职责原则（SRP）物理拆分到 stages/
from backend.rag.indexing.stages import (
    EmbeddingStage,
    EnrichStage,
    FilterStage,
    IndexingPipeline,
    MetadataStage,
    ParseStage,
    PersistStage,
    QualityStage,
)
from backend.rag.indexing.stages.enrich_stage import (
    build_doc_level_text as _build_doc_level_text,
    build_fixture_chunk_metadata,
    preflight_index_embedding_budget as _preflight_index_embedding_budget,
    question_candidate_indexes as _question_candidate_indexes,
    select_vector_chunks as _select_vector_chunks,
)
from backend.rag.indexing.stages.filter_stage import (
    append_quality_issue as _append_quality_issue,
    filter_index_chunks as _filter_index_chunks,
    filter_quality_summary as _filter_quality_summary,
    mask_index_text_for_pii as _mask_index_text_for_pii,
)
from backend.rag.indexing.stages.metadata_stage import _sample_for_summary
from backend.rag.indexing.stages.persist_stage import (
    add_doc_level_vector_with_lineage as _add_doc_level_vector_with_lineage,
    serialize_registry_keywords as _serialize_registry_keywords,
)
from backend.rag.indexing.stages.quality_stage import (
    PrepaidContentIntegrityError,
    apply_index_chunk_identity,
    coverage_quality_metrics as _coverage_quality_metrics,
    quality_index_metadata as _quality_index_metadata,
    reviewable_anomalies as _reviewable_anomalies,
)
from backend.rag.indexing.processing_lineage import ModelIdentity, ProcessingRunContext
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder

# 索引中途崩溃/重启后视为"待恢复"的 registry 状态（任务持久化的 recovery 口径）
INTERRUPTED_STATUSES = ("uploading", "parsing", "embedding")

# doc 级全文入库的文本长度上限：doc_db 单条 embedding 超长会被模型截断/报错，
# 且大文件下内存峰值翻倍；Stage1 doc 级检索只需头部语义信息即可定位文档
DOC_LEVEL_TEXT_MAX_CHARS = 16000

# doc 级文本增强（1.2）：摘要/章节头部最少保住的正文预算——
# header 过长时正文不至于被挤没，保证 doc 级仍含原始语义
_DOC_LEVEL_BODY_MIN_CHARS = 2000

_EVAL_FIXTURE_SETS = frozenset({"baseline", "expanded_100", "scale_20k"})


# ============================================================
# 辅助函数委托层（全部迁入 stages/ 对应模块，此处保留导出兼容存量测试）
# ============================================================

def _parse_trace_page_metrics(
    chunks: list[Document], ext: str, raw_ast: Any = None,
) -> tuple[int, bool]:
    pages_applicable = ext.lower() == ".pdf"
    if not pages_applicable:
        return 0, False
    page_count = 0
    for chunk in chunks:
        value = (chunk.metadata or {}).get("page_count")
        if isinstance(value, int) and value > page_count:
            page_count = value
    try:
        pdf_page_count = int(getattr(raw_ast, "pdf_page_count", 0) or 0)
    except (TypeError, ValueError):
        pdf_page_count = 0
    return max(page_count, pdf_page_count), True


def _apply_fixture_metadata(metadata: dict[str, Any], fixture_set: str | None) -> None:
    if not fixture_set:
        return
    if fixture_set not in _EVAL_FIXTURE_SETS:
        raise ValueError(f"未知评测 fixture_set: {fixture_set}")
    metadata["fixture_set"] = fixture_set
    metadata["dataset"] = "rag_eval"


def _processing_config_snapshot() -> dict[str, Any]:
    from backend.config.rag import (
        CHUNK_POLICY_VERSION,
        EMBEDDING_BATCH_SIZE,
        EMBEDDING_MODEL,
        PARSER_VERSION,
    )
    return {
        "parser_version": PARSER_VERSION,
        "chunk_policy_version": CHUNK_POLICY_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_batch_size": EMBEDDING_BATCH_SIZE,
    }


def _embedding_model_identity(embedding: Any) -> ModelIdentity:
    if embedding is None:
        return ModelIdentity()
    name = str(getattr(embedding, "model", "") or getattr(embedding, "model_name", "") or "")
    rev = str(getattr(embedding, "model_version", "") or getattr(embedding, "version", "") or "")
    return ModelIdentity(model_name=name, model_revision=rev)


def _enforce_prepaid_content_integrity(
    indexer,
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
    qs = getattr(indexer, "_quality_stage_inst", None)
    if qs is None:
        qs = indexer._quality_stage_inst = QualityStage(
            registry=indexer.registry,
            candidate_mode=getattr(indexer, "candidate_mode", False),
        )
    return qs.enforce_prepaid_integrity(
        qc, chunks, path_id, doc_meta,
        doc_id=doc_id, kb_id=kb_id, file_path=file_path,
        file_size=file_size, file_hash=file_hash,
        filtered_details=filtered_details, truncated=truncated,
    )


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


# 摘要采样：委托共享实现（P1 元数据治理 2026-10-13——超长文档不再只取
# 头尾 20k 声称代表全文，改为 头+章节目录+分层采样+尾 且标注覆盖范围）
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
        fixture_set: str | None = None,
        processing_lineage_repository: Any = None,
        processing_task_id: str | None = None,
        processing_batch_id: str | None = None,
        candidate_mode: bool = False,
        chunk_store: Any = None,
        bm25_source_vectordb: Any = None,
        tenant_id: str = "default",
        ocr_page_budget: int | None = None,
    ):
        self.docs_dir = Path(docs_dir).resolve()
        self.vectordb = vectordb
        self.doc_db = doc_db
        self.embedding = embedding
        self.registry = registry
        self.kb_id = kb_id
        self.department = department
        self.tenant_id = str(tenant_id or "default")
        # 仅评测入库使用；生产索引保持 None，避免把测试范围字段误当授权字段。
        self.fixture_set = fixture_set
        # 上传/重索引后立即同步 BM25（避免"上传成功但 BM25 未更新"）。
        # 启动期 sync 时 bm25_store 尚未构建（pipeline 先增量索引后建 BM25），传入 None 即跳过。
        self.bm25_store = bm25_store
        # 运行血缘仓储由生产入口显式注入，测试/离线工具可不连接 PG；
        # 内存状态仍会生成，避免主索引链路依赖观测库可用性。
        self.processing_lineage_repository = processing_lineage_repository
        self.processing_task_id = processing_task_id
        self.processing_batch_id = processing_batch_id
        # None 沿用服务端全局 OCR 页数限制；仅管理员上传任务能显式覆盖。
        self.ocr_page_budget = ocr_page_budget
        # ── 候选版本模式（2026-10-01 B 阶段）──
        # candidate_mode=True：vectordb/doc_db/bm25_store 为候选代次隔离实例，
        # registry 只读（parsing 占位与最终 register 都由发布协议承担）；
        # chunk_store 为缓冲代理；bm25_source_vectordb 为主∪候选拼接视图。
        self.candidate_mode = candidate_mode
        self._chunk_store_override = chunk_store
        self._bm25_source_vectordb = bm25_source_vectordb

    def _cs(self):
        """chunk_store 访问口：候选模式用注入的缓冲代理，否则进程单例。"""
        if self._chunk_store_override is not None:
            return self._chunk_store_override
        from backend.rag.indexing.chunk_store import get_chunk_store
        return get_chunk_store()

    # ---- 主入口 ----

    def sync(self) -> SyncResult:
        """执行一次增量同步。

        首次运行（registry 为空）→ 所有文件视为 ADDED。
        后续运行 → 按 SHA256 diff。

        启动恢复（①）：索引任务在后台线程跑、进程不持久化任务队列，
        服务重启会把 registry 留在 uploading/parsing/embedding 的文档悬在
        半途。sync() 先显式恢复这批文档（文件还在 → 重索引；文件没了 →
        清理悬空行），再做常规增量 diff。

        STOP C（2026-09-28）：pending_review / failed 的**已知文件**不再按
        ADDED 无限重试。sync 只认 active 行，near-dup 待审与结构性失败
        （如 0-chunk CSV）的文件此前每次启动都会重走元数据 LLM（~20s/文件）
        ——实测一次启动重处理 89 个文件、烧 ~30 分钟并反复追加 failed 行
        （rag_eval_kb 20 个文件攒出 547 条 failed）。改为：hash 未变 → 跳过
        （计入 skipped，留待人工审核/显式重建）；hash 变了（内容真改了）→
        仍按 ADDED 重评。RAG_FORCE_REINDEX=true 强制全量重扫。
        """
        disk_files = self._scan_disk()
        self._recover_interrupted(disk_files)

        registry_rows = self.registry.list_all()

        # 只考虑 active 的条目（排除已标记 deleted 的）
        # 归一化路径为绝对路径：旧数据可能有相对路径，与 disk_files 的绝对路径不匹配
        active_registry = {}
        nonactive_registry: dict[str, dict] = {}
        for p, r in registry_rows.items():
            norm_path = os.path.abspath(p)
            if r.get("status") == "active":
                active_registry[norm_path] = r
            elif r.get("status") in ("pending_review", "failed", "deleted"):
                # deleted（D-7，2026-10-05）：删除级联只软删 + 清向量，磁盘源
                # 文件仍在——若不进防线，sync 会按 ADDED 重索引（已删文档复活
                # + 新 cand 代次向量）。hash 未变 → 跳过；hash 变了（同名新
                # 内容）→ 仍按 ADDED 重新入库（合法新文档，非复活）。
                nonactive_registry[norm_path] = r

        if os.getenv("RAG_FORCE_REINDEX", "false").lower() == "true":
            delta = Delta(
                added=sorted(disk_files.keys()),
                modified=[], deleted=[], unchanged=[],
            )
            logger.warning("RAG_FORCE_REINDEX=true：强制全量重扫（忽略增量 diff）")
        else:
            delta = self._compute_delta(disk_files, active_registry)
            # STOP C：非 active 已知文件 hash 未变 → 从 ADDED 剔除（跳过重试）
            retry_added, skipped_paths = self._filter_nonactive_additions(
                disk_files, nonactive_registry, delta.added)
            if skipped_paths:
                delta.unchanged = sorted(set(delta.unchanged) | skipped_paths)
                logger.info(
                    f"[Sync] 跳过 {len(skipped_paths)} 个 pending_review/failed/deleted "
                    "已知文件（hash 未变，不重试；deleted 不复活，D-7 防线）"
                )
            delta.added = retry_added
        failed_files = self._apply_delta(delta, disk_files, active_registry)

        result = SyncResult(
            added=len(delta.added),
            modified=len(delta.modified),
            deleted=len(delta.deleted),
            skipped=len(delta.unchanged),
            failed=len(failed_files),
            failed_files=failed_files,
        )
        if failed_files:
            logger.warning(
                f"增量索引完成（含 {len(failed_files)} 个失败文件，已跳过）: {result}"
            )
        else:
            logger.info(f"增量索引完成: {result}")
        return result

    # ---- 中断恢复（任务持久化的 recovery 路径）----

    def _recover_interrupted(self, disk_files: dict[str, tuple[str, int, float]]) -> dict[str, int]:
        """重索引 registry 中停留在非终态（uploading/parsing/embedding）的文档。

        背景：上传索引走 fire-and-forget 后台任务，进程重启即丢任务；
        恢复口依赖 registry 状态（_index_file_inner 开始时写入 parsing 占位行）。

        - 文件仍在磁盘 → 重新索引（幂等：成功后 register 覆盖为 active）
        - 文件已丢失 → 行标记 deleted（清理悬空占位）
        - 恢复失败 → 行标记 failed（下次 sync 仍会按 ADDED 重试）
        """
        list_by_statuses = getattr(self.registry, "list_by_statuses", None)
        if not callable(list_by_statuses):
            return {"recovered": 0, "cleaned": 0, "failed": 0}
        try:
            stuck_rows = list_by_statuses(INTERRUPTED_STATUSES)
        except Exception as e:
            logger.warning(f"[Recovery] 读取中断文档状态失败，跳过恢复: {e}")
            return {"recovered": 0, "cleaned": 0, "failed": 0}
        if not stuck_rows:
            return {"recovered": 0, "cleaned": 0, "failed": 0}

        logger.info(f"[Recovery] 发现 {len(stuck_rows)} 个中断文档，开始恢复")
        counts = {"recovered": 0, "cleaned": 0, "failed": 0}
        disk_abs = {os.path.abspath(p) for p in disk_files}
        for row in stuck_rows:
            path = row.get("file_path", "")
            if not path:
                continue
            if not (os.path.isfile(path) or os.path.abspath(path) in disk_abs):
                try:
                    self.registry.mark_deleted(path)
                    counts["cleaned"] += 1
                    logger.warning(f"[Recovery] 中断文档源文件已丢失，清理记录: {path}")
                except Exception as e:
                    logger.warning(f"[Recovery] 清理悬空记录失败 {path}: {e}")
                continue
            try:
                disk_row = disk_files.get(os.path.abspath(path)) or disk_files.get(path)
                file_hash = disk_row[0] if disk_row else None
                result = self._index_file(path, file_hash=file_hash)
                self._log_sync_operation(path, result or {}, "pipeline_recovery")
                counts["recovered"] += 1
                logger.info(f"[Recovery] 中断文档已恢复索引: {os.path.basename(path)}")
            except Exception as e:
                counts["failed"] += 1
                logger.warning(f"[Recovery] 中断文档恢复失败 {path}: {e}")
                try:
                    self.registry.update_status(path, "failed")
                except Exception as status_err:
                    logger.debug(f"[Recovery] 标记 failed 失败 {path}: {status_err}")
        logger.info(f"[Recovery] 恢复完成: {counts}")
        return counts

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

    @staticmethod
    def _filter_nonactive_additions(
        disk_files: dict[str, tuple[str, int, float]],
        nonactive_registry: dict[str, dict],
        added: list[str],
    ) -> tuple[list[str], set[str]]:
        """从 ADDED 中剔除 hash 未变的 pending_review/failed 已知文件。

        返回 (保留的 added, 被跳过的路径集合)。hash 变了 = 内容真改了，
        保留重评；hash 未变 = 启动重试无意义（STOP C，2026-09-28）。
        """
        keep: list[str] = []
        skipped: set[str] = set()
        for p in added:
            row = nonactive_registry.get(p)
            if row is not None and disk_files[p][0] == row.get("file_hash"):
                skipped.add(p)
            else:
                keep.append(p)
        return keep, skipped

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
    ) -> list[str]:
        """逐文件处理增量变更。

        per-file 容错：单个文件索引失败只记录并跳过，不再让整轮 sync 崩溃——
        此前一个坏文件（如 0 文本的扫描件）会触发 pipeline 回退全量重建
        （registry.clear() + doc_id 重派），造成灾难性状态抹除（2026-09-17 事故）。

        Returns:
            失败文件路径列表。
        """
        failed: list[str] = []

        # 删除
        for path in delta.deleted:
            row = registry.get(path, {})
            doc_id = row.get("doc_id", "")
            if doc_id:
                self._remove_document(doc_id, file_path=path)
            self.registry.mark_deleted(path)
            logger.info(f"[DELETED] {os.path.basename(path)}")

        # 修改: 先删后加
        for path in delta.modified:
            row = registry.get(path, {})
            doc_id = row.get("doc_id", "")
            if doc_id:
                self._remove_document(doc_id, file_path=path)
            try:
                result = self._index_file(path)
                self._log_sync_operation(path, result or {}, "pipeline_sync_modified")
                logger.info(f"[MODIFIED] {os.path.basename(path)}")
            except Exception as e:
                failed.append(path)
                logger.error(
                    f"[MODIFIED-FAILED] {os.path.basename(path)}: "
                    f"{type(e).__name__}: {e}（跳过，不中断本轮 sync）",
                    exc_info=True,
                )

        # 新增
        for path in delta.added:
            try:
                result = self._index_file(path)
                self._log_sync_operation(path, result or {}, "pipeline_sync_added")
                logger.info(f"[ADDED] {os.path.basename(path)}")
            except Exception as e:
                failed.append(path)
                logger.error(
                    f"[ADDED-FAILED] {os.path.basename(path)}: "
                    f"{type(e).__name__}: {e}（跳过，不中断本轮 sync）",
                    exc_info=True,
                )

        return failed

    # ---- 单文件索引 ----

    def _log_sync_operation(self, path: str, result: dict, source: str) -> None:
        """sync 触发的索引写操作日志——让 /knowledge/operations 页可见后台
        重建/增量同步活动（此前只有人工 API 操作才记，批量 sync 是盲区）。
        trace_id 来自 _index_file 返回值，operations 页可直接点进索引 trace 详情。
        失败只记 debug（审计日志不阻断索引）。
        """
        try:
            from backend.rag.indexing.operation_log_pg import (
                PostgresDocumentOperationLogger,
            )
            PostgresDocumentOperationLogger().log(
                doc_id=result.get("doc_id", ""),
                doc_name=os.path.basename(path),
                operation="reindex",
                user_id="system",
                source=source,
                trace_id=result.get("trace_id") or None,
                result="success",
                detail={"chunk_count": result.get("chunk_count", 0)},
            )
        except Exception as e:
            logger.debug(f"[Sync] 操作日志记录失败 ({os.path.basename(path)}): {e}")

    def _index_file(self, file_path: str, file_hash: str | None = None,
                    reindex_ctx: dict | None = None,
                    identity_path: str | None = None):
        """索引单篇文档: 加载 → 解析 → 清洗 → 去重 → 分块 → 元数据 → embed → 写入。

        Args:
            file_path: 文档路径。
            file_hash: 调用方已算好的 SHA256(如上传路由 duplicate 检测时算过)。
                传入可避免对大文件重复全盘读取;缺省时内部计算。
            reindex_ctx: 重索引上下文（F4 精确失败清理）。含旧版本定位信息：
                {"old_chunk_ids": [...], "old_doc_db_id": "..."}。传入后，
                vdb/registry 阶段失败只精确清理本次新写入的数据，旧版本
                向量原样保留；None（首次索引）维持 doc_id 全量清理。

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
        # identity_path（候选模式）：doc_id/KB/归属等派生与 registry 读取用
        # 正式路径；真实文件读取（load/parse）仍用 file_path（暂存文件）。
        path_id = identity_path or file_path
        display_name = os.path.basename(path_id) or os.path.basename(file_path)
        kb_id = self.kb_id if self.kb_id != "default" else self._derive_kb_id(path_id)
        file_hash = file_hash or self._sha256(file_path)
        doc_id = self._derive_doc_id(path_id, file_hash, kb_id)
        # ── 启动 indexer trace ──
        trace = trace_collector.start(
            question=display_name,
            session_id="",
            workflow_name="knowledge_index",
            workflow_kind=WorkflowKind.KNOWLEDGE_INDEX.value,
        )
        trace.tags.update({"kb_id": kb_id, "doc_id": doc_id, "file_ext":
                          os.path.splitext(file_path)[1].lower(),
                          "embedding_model": os.path.basename(getattr(self.embedding, "model_name", "")) or
                                             os.path.basename(str(getattr(self.embedding, "model", ""))) or "—"})
        # API7/N-7：upload_id（=processing_task_id，tasks.biz_id 同源）进 tags
        # —— REST 受理 → worker 执行 → trace 三元串接；空值（磁盘 sync 路径
        # 无任务上下文）不写。
        if self.processing_task_id:
            trace.tags["upload_id"] = self.processing_task_id
        if self.fixture_set:
            trace.tags["fixture_set"] = self.fixture_set

        lineage_recorder = None
        if self.processing_lineage_repository is not None:
            lineage_context = ProcessingRunContext.create(
                doc_id=doc_id,
                file_hash=file_hash,
                operation="reindex" if reindex_ctx else "upload",
                task_id=self.processing_task_id,
                batch_id=self.processing_batch_id,
                trace_id=trace.id,
                config_snapshot=_processing_config_snapshot(),
            )
            lineage_recorder = ProcessingRunRecorder(
                lineage_context, self.processing_lineage_repository
            )
            lineage_recorder.start()

        # ── ① upload (root) ──
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0
        upload_span = trace_collector.start_span(
            "index_upload",
            parent_id=None,
            name=f"Index {display_name}",
            type="workflow",
            kind=SpanKind.INDEX_UPLOAD.value,
            input={"file_path": file_path, "size_bytes": file_size},
        )
        try:
            inner_result = self._index_file_inner(
                file_path, kb_id, doc_id, file_hash,
                reindex_ctx=reindex_ctx,
                lineage_recorder=lineage_recorder,
                identity_path=path_id,
            )
            if lineage_recorder is not None:
                lineage_recorder.finish(
                    "duplicate" if inner_result.get("skipped") else "success"
                )
                lineage_snapshot = lineage_recorder.context.snapshot()
                try:
                    self.registry.update_fields(
                        file_path,
                        {
                            "last_processing_run_id": lineage_snapshot.run_id,
                            "pipeline_version": lineage_snapshot.pipeline_version,
                            "processing_status": lineage_snapshot.status,
                            "model_count": len(lineage_snapshot.model_summary),
                            "processing_finished_at": (
                                lineage_snapshot.finished_at.isoformat()
                                if lineage_snapshot.finished_at else None
                            ),
                        },
                    )
                except Exception as registry_error:
                    logger.warning(
                        "[ProcessingLineage] registry 摘要回填失败: %s",
                        registry_error,
                    )
            trace_collector.end_span(upload_span,
                metrics={"doc_id": doc_id, "kb_id": kb_id})
            trace_collector.finish(trace, display_name,
                                   upload_span.duration_ms, "", "")
            # P2-2:返回 dict 含 trace_id + chunk_count + doc_db_id,
            # 让 reindex_file 直接消费,不再反查 registry
            result = {
                "trace_id": trace.id,
                "doc_id": doc_id,  # 本次派生的真实 doc_id(新文件也有值)
                "chunk_count": inner_result.get("chunk_count", 0),
                "chunk_ids": inner_result.get("chunk_ids") or [],
                "doc_db_id": inner_result.get("doc_db_id", ""),
                "file_hash": inner_result.get("file_hash", file_hash),
                "status": "active",
                "processing_run_id": (
                    lineage_snapshot.run_id if lineage_recorder is not None else ""
                ),
                "model_summary": (
                    lineage_snapshot.model_summary if lineage_recorder is not None else []
                ),
            }
            if self.candidate_mode and not inner_result.get("skipped"):
                # 候选模式：发布清单（chunk_ids/registry_metadata）必须透传，
                # 落库由 publish_candidate 统一提交
                result.update({
                    "candidate": True,
                    "chunk_ids": inner_result.get("chunk_ids") or [],
                    "doc_type": inner_result.get("doc_type", "general"),
                    "registry_metadata": inner_result.get("registry_metadata") or {},
                })
            return result
        except Exception as e:
            if lineage_recorder is not None:
                try:
                    lineage_recorder.fail(str(e))
                except Exception as lineage_error:
                    logger.warning("[ProcessingLineage] 失败收口异常: %s", lineage_error)
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

    @property
    def _pipeline(self) -> IndexingPipeline:
        pipe = getattr(self, "_pipeline_inst", None)
        if pipe is None:
            pipe = self._pipeline_inst = IndexingPipeline(self)
        return pipe

    def _index_file_inner(self, file_path: str, kb_id: str, doc_id: str,
                          file_hash: str, reindex_ctx: dict | None = None,
                          lineage_recorder: ProcessingRunRecorder | None = None,
                          identity_path: str | None = None) -> dict:
        """_index_file 的实际工作，委托给 IndexingPipeline 执行。"""
        return self._pipeline.run(
            file_path=file_path,
            kb_id=kb_id,
            doc_id=doc_id,
            file_hash=file_hash,
            reindex_ctx=reindex_ctx,
            lineage_recorder=lineage_recorder,
            identity_path=identity_path,
        )

    def _embed_stage(self) -> EmbeddingStage:
        """embedding Stage（实例级缓存，跨文件复用 embed cache）。"""
        st = getattr(self, "_embed_stage_inst", None)
        if st is None:
            st = self._embed_stage_inst = EmbeddingStage(self.embedding)
        return st

    @staticmethod
    def _embed_text_for(chunk, doc_summary: str = "") -> str:
        return EmbeddingStage.embedding_text_for(chunk, doc_summary=doc_summary)

    def _embed_single_with_retry(self, i: int, chunk, embed_text: str):
        return self._embed_stage.single_with_retry(i, chunk, embed_text)

    def _embed_with_retry(self, chunks, parent_span, doc_summary: str = "") -> list:
        return self._embed_stage.batch_with_retry(
            chunks, parent_span, doc_summary=doc_summary)

    def _get_embed_cache(self):
        return self._embed_stage.cache()

    @staticmethod
    def _report_cache_metrics(parent_span, hits: int, total: int) -> None:
        EmbeddingStage.report_cache_metrics(parent_span, hits, total)

    async def _build_doc_metadata(self, full_text: str, base_meta: dict,
                                  parent_span_id: str = "",
                                  chunks_text: list[str] | None = None,
                                  processing_recorder=None) -> dict:
        """构建文档级元数据 — 委托 MetadataStage（stages/metadata_stage.py）。

        返回 dict 契约与拆分前完全一致（下游 chunk 注入 / doc_db 落库 /
        registry.register 零感知）。
        """
        return await self._meta_stage.build(
            full_text, base_meta,
            parent_span_id=parent_span_id, chunks_text=chunks_text,
            processing_recorder=processing_recorder)

    @property
    def _meta_stage(self) -> MetadataStage:
        """metadata Stage（实例级缓存，引用 registry/embedding/department）。"""
        st = getattr(self, "_meta_stage_inst", None)
        if st is None:
            st = self._meta_stage_inst = MetadataStage(
                self.registry, self.embedding, self.department)
        return st

    # ---- 统一抽取路径的收口（纯规则部分照旧计算）----

    @staticmethod
    def _detect_near_dup(registry, minhash_sig: list[int], doc_type: str,
                         exclude_doc_id: str = "") -> str:
        return MetadataStage.detect_near_dup(
            registry, minhash_sig, doc_type, exclude_doc_id=exclude_doc_id)

    async def _finalize_unified_metadata(self, full_text: str, base_meta: dict, unified: dict,
                                         parent_span_id: str = "", chunks_text: list[str] | None = None) -> dict:
        """统一 LLM 抽取收口 — 委托 MetadataStage.finalize_unified。"""
        return await self._meta_stage.finalize_unified(
            full_text, base_meta, unified,
            parent_span_id=parent_span_id, chunks_text=chunks_text)

    # ---- 公开重索引 ----

    def index_candidate(
        self,
        source_path: str,
        identity_path: str,
        file_hash: str,
        old_chunk_ids: list[str] | None = None,
    ) -> dict:
        """候选模式单文件索引（B 阶段发布协议的配套入口）。

        source_path：不可变暂存文件（实际读取）；identity_path：正式路径
        （doc_id/归属/registry 行/metadata 落值）。返回含 chunk_ids 与
        registry_metadata 的完整发布清单；registry/正式向量/正式 BM25 均
        不被触碰——落库由 publish_candidate 统一提交。old_chunk_ids 是当前
        Active registry 保存的物理向量 ID，用于候选 BM25 快照排除被替换内容。
        """
        if not self.candidate_mode:
            raise RuntimeError("index_candidate 只能在 candidate_mode=True 的实例上调用")
        reindex_ctx = (
            {"old_chunk_ids": list(old_chunk_ids)} if old_chunk_ids else None
        )
        return self._index_file(source_path, file_hash=file_hash,
                                reindex_ctx=reindex_ctx,
                                identity_path=identity_path)

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
        # 旧向量定位信息必须在重新索引前捕获 —— 成功后 register() 会用新
        # chunk_ids 覆盖同一行。chunk 向量 ID 由向量库生成（UUID），新旧必不冲突，
        # 因此"先写新、后按旧 ID 精确删"可行。
        old_chunk_ids: list[str] = []
        old_doc_db_id = ""
        if row:
            try:
                _raw = row.get("chunk_ids") or "[]"
                _parsed = json.loads(_raw) if isinstance(_raw, str) else _raw
                if isinstance(_parsed, list):
                    old_chunk_ids = [str(x) for x in _parsed]
            except (ValueError, TypeError):
                old_chunk_ids = []
            old_doc_db_id = row.get("doc_db_id") or ""

        # 1. 处理旧数据：财务文档走版本快照；其他类型"先写后删"——
        #    旧向量保留到新索引成功后再清理，消除删旧→写新之间的检索空窗
        #    （文档越大空窗越长，期间该文档完全查不到）；索引失败时旧数据
        #    原样保留，不再出现"失败即丢旧版本"。
        financial_snapshot = bool(
            old_doc_id and self._should_use_version_snapshot(old_doc_type, file_path)
        )
        if financial_snapshot and not old_chunk_ids:
            raise ValueError(
                "财务文档缺少当前分块 ID，无法安全重解析；原索引保持不变"
            )

        snapshot_ids_hidden = False
        if financial_snapshot:
            # 版本快照：旧 chunk 标记 is_latest=False，保留历史版本向量
            try:
                updated = self.vectordb.update_metadata_by_ids(
                    old_chunk_ids,
                    metadata_update={"is_latest": False},
                )
                snapshot_ids_hidden = True
                logger.info(
                    f"[REINDEX] 版本快照: doc_id={old_doc_id}, "
                    f"按旧 chunk ID 标记 {updated} 个当前 chunk is_latest=False"
                )
            except Exception as e:
                # 快照标记失败时必须保留旧向量和 Registry；删除旧数据会让
                # 财务文档在新索引失败时不可检索，因此在任何解析前安全退出。
                try:
                    self.vectordb.update_metadata_by_ids(
                        old_chunk_ids, {"is_latest": True},
                    )
                except Exception as restore_error:  # noqa: BLE001
                    logger.critical(
                        "[REINDEX] 财务快照标记失败，恢复旧 latest 标记也失败 "
                        "(doc_id=%s): %s",
                        old_doc_id,
                        restore_error,
                        exc_info=True,
                    )
                raise RuntimeError(
                    f"财务旧索引快照未能安全标记，已停止重解析: {e}"
                ) from e
        elif old_doc_id:
            logger.info(
                f"[REINDEX] 先写后删模式: 保留旧数据 doc_id={old_doc_id} "
                f"({len(old_chunk_ids)} chunks)，待新索引成功后清理"
            )

        # 2. 重新索引（_index_file 返回完整 dict，含 trace_id/chunk_count/doc_db_id）
        #    注意：doc_id 复用 active 记录（保持评测集/Trace 稳定）。传入 reindex_ctx
        #    后，vdb/registry 阶段失败走 F4 精确清理——只删本次新写入，旧版本向量
        #    原样保留（旧实现内部清理按 doc_id 条件删，会连带旧向量）；parse/chunk/
        #    embed 阶段失败本就不触碰存储，旧数据完整保留。
        reindex_ctx = None
        if old_doc_id:
            reindex_ctx = {
                "old_chunk_ids": old_chunk_ids,
                "old_doc_db_id": old_doc_db_id,
            }
        previous_status = str((row or {}).get("status") or "")
        previous_file_hash = str((row or {}).get("file_hash") or "")
        try:
            index_result = self._index_file(
                file_path, file_hash=file_hash, reindex_ctx=reindex_ctx)
        except Exception:
            # 真实路径里 _index_file → register_in_progress 会先把行改成
            # parsing + 新 file_hash。失败时旧向量仍按"先写后删"保留，但
            # status=parsing 会让检索可见性（status=active 正向校验）把旧
            # 版本整体隐藏，file_hash 也会指向未成功索引的内容。这里恢复
            # 更新前快照，兑现"失败的索引不能动旧 active 记录"契约
            # （test_indexer_recovery_and_reindex::test_failure_preserves_old_data）。
            if snapshot_ids_hidden and old_chunk_ids:
                try:
                    # 只恢复原登记行指向的当前向量，不能用 doc_id 条件更新，
                    # 否则会把更早的财务历史版本也错误地标成最新。
                    self.vectordb.update_metadata_by_ids(
                        old_chunk_ids, {"is_latest": True},
                    )
                except Exception as restore_error:  # noqa: BLE001
                    logger.critical(
                        "[REINDEX] 失败后恢复财务旧向量 latest 标记失败 "
                        "(doc_id=%s): %s",
                        old_doc_id,
                        restore_error,
                        exc_info=True,
                    )
            if old_doc_id and previous_status == "active" and old_chunk_ids:
                try:
                    self.registry.update_status(file_path, "active")
                    if previous_file_hash:
                        self.registry.update_fields(
                            file_path, {"file_hash": previous_file_hash},
                        )
                except Exception as restore_error:  # noqa: BLE001
                    logger.warning(
                        "[REINDEX] 失败后恢复旧 active 快照失败 "
                        f"(doc_id={old_doc_id}): {restore_error}"
                    )
            raise
        # dedup 命中（内容未变，如占位行写入失败的极端场景）→ 没有新数据，
        # 清理旧向量会造成数据丢失，直接原样返回
        skipped = bool(index_result.get("skipped"))
        if not skipped:
            # 2.25 清理被取代的旧向量（精确按旧 ID，不碰同 doc_id 的新 chunk）
            self._cleanup_superseded(
                old_doc_id, old_chunk_ids, old_doc_db_id,
                new_doc_db_id=index_result.get("doc_db_id", ""),
                new_chunk_ids=index_result.get("chunk_ids"),
                file_path=file_path,
            )
        trace_id = index_result.get("trace_id", "")
        new_chunk_count = index_result.get("chunk_count", 0)
        new_doc_db_id = index_result.get("doc_db_id", "")
        new_file_hash = index_result.get("file_hash", "")

        # 2.5 bump doc_version（重索引 +1）— P2-3 用公开方法替代私有 cursor 访问
        new_version = 0
        if old_doc_id and not skipped:
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

    def _cleanup_superseded(
        self,
        old_doc_id: str,
        old_chunk_ids: list[str],
        old_doc_db_id: str,
        new_doc_db_id: str = "",
        new_chunk_ids: list[str] | None = None,
        file_path: str = "",
    ):
        """重索引成功后清理被取代的旧向量（"先写后删"的删半边）。

        只按旧 ID 精确删除，绝不按 doc_id 条件删 —— 新旧 chunk 共享同一
        doc_id，条件删会把刚写入的新向量一起删掉。BM25 在写入阶段已经
        从当前向量集合重建，并通过 old_chunk_ids 排除了这些旧条目。
        """
        if not old_doc_id:
            return
        obsolete_chunk_ids = old_chunk_ids
        if new_chunk_ids is not None:
            new_id_set = set(new_chunk_ids)
            obsolete_chunk_ids = [
                chunk_id for chunk_id in old_chunk_ids if chunk_id not in new_id_set
            ]
        if obsolete_chunk_ids:
            try:
                self.vectordb.delete(ids=obsolete_chunk_ids)
                logger.info(
                    f"[REINDEX] 已清理旧 chunk 向量 {len(obsolete_chunk_ids)} 条 "
                    f"(doc_id={old_doc_id})"
                )
            except Exception as e:
                logger.warning(
                    f"[REINDEX] 旧 chunk 向量清理失败（残留孤儿向量，"
                    f"doc_id={old_doc_id}）: {e}"
                )
        elif old_chunk_ids:
            logger.info(
                f"[REINDEX] 旧 chunk ID 均被新索引复用，跳过清理 "
                f"(doc_id={old_doc_id})"
            )
        else:
            logger.warning(
                f"[REINDEX] 旧记录无 chunk_ids，跳过旧向量清理 "
                f"(doc_id={old_doc_id}) — 如持续出现请检查 registry 数据"
            )
        if old_doc_db_id and old_doc_db_id != new_doc_db_id:
            try:
                self.doc_db.delete(ids=[old_doc_db_id])
            except Exception as e:
                logger.warning(
                    f"[REINDEX] 旧 doc 级向量清理失败 (doc_db_id={old_doc_db_id}): {e}"
                )

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

    def _cleanup_partial_write(self, doc_id: str, file_path: str = "",
                               reindex_ctx: dict | None = None,
                               new_doc_db_id: str = ""):
        """索引中途失败（vdb/registry 阶段）后的清理（F4）。

        首次索引（reindex_ctx=None）：文档没有旧版本，按 doc_id 全量清理是正确行为。
        重索引（reindex_ctx 传入）：新旧 chunk 共享同一 doc_id，条件删会把旧版本
        向量一起删掉（破坏"先写后删"的失败语义）——改为精确删：
          - vectordb: get(where=doc_id) 取现有 id，与旧 chunk_ids 求差集后按 ids 删，
            只清本次新写入/部分写入的向量；旧 chunk_ids 未知（空）时**不删向量**，
            残留交由 Sweeper 对账（宁可多留，不可误删）；
          - doc_db: 只删本次运行分配的 doc_db_id（新 doc 级向量），旧版本 id 不同不受影响；
          - BM25/chunk_store: vdb 阶段失败时 BM25 尚未写入、旧数据完好；
            registry 阶段失败时 BM25 已被 replace_documents 换成新内容、无法回滚，
            与向量的内容差异由 Sweeper 计数对账收敛。chunk_store 为单版本覆盖写，
            旧文本在本段失败前已被替换，属既有行为。
        """
        if not doc_id:
            return
        if self.candidate_mode:
            # 候选模式的失败清理在候选层整体进行（drop 候选 collection +
            # staging 目录，见 publish.cleanup_candidate），数据全部在隔离
            # collection 里，无需逐 id 精确删。
            logger.info("[Indexer] 候选模式失败：向量/BM25 属候选代次，由候选层统一清理")
            return
        if not reindex_ctx:
            self._remove_document(doc_id, file_path=file_path)
            return
        old_chunk_ids = set(reindex_ctx.get("old_chunk_ids") or [])
        old_doc_db_id = reindex_ctx.get("old_doc_db_id") or ""
        # ── vectordb: 差集精确删本次新写入的 chunk ──
        if old_chunk_ids:
            try:
                res = self.vectordb.get(where={"doc_id": doc_id}) or {}
                current = [str(x) for x in (res.get("ids") or [])]
                stale = [x for x in current if x not in old_chunk_ids]
                if stale:
                    self.vectordb.delete(ids=stale)
                    logger.info(
                        f"[REINDEX] 失败精确清理: 删除本次新写入向量 {len(stale)} 条, "
                        f"保留旧向量 {len(current) - len(stale)} 条 (doc_id={doc_id})"
                    )
            except Exception as e:
                logger.warning(
                    f"[REINDEX] 失败精确清理异常（残留新向量待 Sweeper 对账, "
                    f"不做 doc_id 条件删以防误删旧版本）: {e}"
                )
        else:
            logger.warning(
                f"[REINDEX] 旧 chunk_ids 未知，失败清理跳过向量删除"
                f"（残留交由 Sweeper 对账, doc_id={doc_id}）"
            )
        # ── doc_db: 只删本次新写入的 doc 级向量 ──
        if new_doc_db_id:
            try:
                self.doc_db.delete(ids=[new_doc_db_id])
            except Exception as e:
                logger.warning(
                    f"[REINDEX] 失败清理新 doc 级向量失败 (id={new_doc_db_id}): {e}"
                )
        elif not old_doc_db_id:
            # 旧版本本就没有 doc 级向量 → doc_id 条件删不会误删旧数据
            try:
                self.doc_db.delete(where={"doc_id": doc_id})
            except Exception as e:
                logger.warning(f"失败清理 doc 级向量失败 (doc_id={doc_id}): {e}")

    def _remove_document(self, doc_id: str, file_path: str = ""):
        """从向量库 + chunk_store + BM25 中删除文档的所有数据。"""
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
            self._cs().delete_by_doc_id(doc_id)
        except Exception as e:
            logger.warning(f"删除 chunk_store 失败 (doc_id={doc_id}): {e}")
        if self.bm25_store is not None:
            try:
                rebuild = getattr(
                    self.bm25_store, "rebuild_from_vectorstore", None
                )
                if callable(rebuild):
                    rebuild(self.vectordb, k=BM25_CANDIDATE_K)
                else:
                    self.bm25_store.remove_documents(
                        [doc_id], file_paths=[file_path] if file_path else None,
                    )
            except Exception as e:
                logger.warning(f"删除 BM25 失败 (doc_id={doc_id}): {e}")

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
                and existing.get("status", "active") in ("active", *INTERRUPTED_STATUSES):
            return str(existing["doc_id"])
        _, _, subpath = parse_kb_dept_subpath_from_path(file_path, str(self.docs_dir))
        return derive_doc_id(
            kb_id=kb_id,
            department=self._derive_department(file_path),
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

    def _derive_department(self, file_path: str) -> str:
        """从文件路径推导 department（第二级子目录），与 doc_id 派生同源。

        批量 sync 时 indexer 是单实例跨多部门构建的，self.department 只是构造默认值
        （pipeline 未传即 "general"）；用它会把所有文档打成同一部门，既破坏部门隔离
        过滤，又让 md5(kb|dept|basename) 算出错误的 doc_id。
        """
        _, department, _ = parse_kb_dept_subpath_from_path(
            file_path, str(self.docs_dir)
        )
        return department or self.department


# Delta 已迁至 models.py
