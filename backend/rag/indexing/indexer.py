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

# 阶段3 Stage 拆分：元数据/embedding 实现迁至 stages/，本类保留薄委托层
# （_sample_for_summary 一并迁移，此处 re-export 兼容旧导入路径）
from backend.rag.indexing.stages.embedding_stage import EmbeddingStage
from backend.rag.indexing.stages.metadata_stage import MetadataStage, _sample_for_summary  # noqa: F401

# 索引中途崩溃/重启后视为"待恢复"的 registry 状态（任务持久化的 recovery 口径）
INTERRUPTED_STATUSES = ("uploading", "parsing", "embedding")

# doc 级全文入库的文本长度上限：doc_db 单条 embedding 超长会被模型截断/报错，
# 且大文件下内存峰值翻倍；Stage1 doc 级检索只需头部语义信息即可定位文档
DOC_LEVEL_TEXT_MAX_CHARS = 16000

# doc 级文本增强（1.2）：摘要/章节头部最少保住的正文预算——
# header 过长时正文不至于被挤没，保证 doc 级仍含原始语义
_DOC_LEVEL_BODY_MIN_CHARS = 2000

_EVAL_FIXTURE_SETS = frozenset({"baseline", "expanded_100", "scale_20k"})


def _apply_fixture_metadata(metadata: dict[str, Any], fixture_set: str | None) -> None:
    """把评测语料范围写入索引元数据；生产文档不强制携带该字段。"""
    if not fixture_set:
        return
    if fixture_set not in _EVAL_FIXTURE_SETS:
        raise ValueError(f"未知评测 fixture_set: {fixture_set}")
    # fixture_set 只用于评测范围和诊断，不参与生产授权裁决；授权仍由 kb/permission_scope 控制。
    metadata["fixture_set"] = fixture_set


def _build_doc_level_text(full_text: str, doc_meta: dict) -> str:
    """构造 doc 级入库文本：summary/章节头部 + 全文头 N 字（1.2 doc_db 增强）。

    背景：doc 级入库原本是"全文头 16K 字符"裸文本，元数据阶段花 LLM 成本
    生成的 summary/sections 没有参与 doc 级向量——Stage1 文档定位对
    「文档讲什么」的语义表达不完整。本函数把 header 拼到全文头部，
    总长仍受 DOC_LEVEL_TEXT_MAX_CHARS 约束（header 挤占正文预算，
    但正文最少保 _DOC_LEVEL_BODY_MIN_CHARS）。

    纯函数便于单测（tests/rag/test_contextual_prefix.py）。
    """
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


def _filter_quality_summary(filtered_details: list[dict]) -> str:
    """R-P1-3: 被过滤 chunk 的 quality_issues 汇总串（无过滤返回空串）。

    例: "filtered_chunks:3(empty=1,too_short=2)"。逐条明细（含 preview）
    持久化在 index_chunk span output.filtered_details，供 trace 详情页审计。
    """
    if not filtered_details:
        return ""
    reason_breakdown: dict[str, int] = {}
    for d in filtered_details:
        reason_breakdown[d["reason"]] = reason_breakdown.get(d["reason"], 0) + 1
    breakdown_str = ",".join(f"{k}={v}" for k, v in reason_breakdown.items())
    return f"filtered_chunks:{len(filtered_details)}({breakdown_str})"


def _append_quality_issue(doc_meta: dict, issue: str) -> None:
    """追加一条质量留痕到 doc_meta["quality_issues"]（逗号分隔，既有串保留）。"""
    prev = doc_meta.get("quality_issues", "")
    doc_meta["quality_issues"] = (f"{prev}, " if prev else "") + issue


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
        fixture_set: str | None = None,
    ):
        self.docs_dir = Path(docs_dir).resolve()
        self.vectordb = vectordb
        self.doc_db = doc_db
        self.embedding = embedding
        self.registry = registry
        self.kb_id = kb_id
        self.department = department
        # 仅评测入库使用；生产索引保持 None，避免把测试范围字段误当授权字段。
        self.fixture_set = fixture_set
        # 上传/重索引后立即同步 BM25（避免"上传成功但 BM25 未更新"）。
        # 启动期 sync 时 bm25_store 尚未构建（pipeline 先增量索引后建 BM25），传入 None 即跳过。
        self.bm25_store = bm25_store

    # ---- 主入口 ----

    def sync(self) -> SyncResult:
        """执行一次增量同步。

        首次运行（registry 为空）→ 所有文件视为 ADDED。
        后续运行 → 按 SHA256 diff。

        启动恢复（①）：索引任务在后台线程跑、进程不持久化任务队列，
        服务重启会把 registry 留在 uploading/parsing/embedding 的文档悬在
        半途。sync() 先显式恢复这批文档（文件还在 → 重索引；文件没了 →
        清理悬空行），再做常规增量 diff。
        """
        disk_files = self._scan_disk()
        self._recover_interrupted(disk_files)

        registry_rows = self.registry.list_all()

        # 只考虑 active 的条目（排除已标记 deleted 的）
        # 归一化路径为绝对路径：旧数据可能有相对路径，与 disk_files 的绝对路径不匹配
        active_registry = {}
        for p, r in registry_rows.items():
            if r.get("status") == "active":
                norm_path = os.path.abspath(p)
                active_registry[norm_path] = r

        delta = self._compute_delta(disk_files, active_registry)
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
                self._index_file(path, file_hash=file_hash)
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
                self._index_file(path)
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
                self._index_file(path)
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

    def _index_file(self, file_path: str, file_hash: str | None = None,
                    reindex_ctx: dict | None = None):
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
        if self.fixture_set:
            trace.tags["fixture_set"] = self.fixture_set

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
            inner_result = self._index_file_inner(
                file_path, kb_id, doc_id, file_hash, reindex_ctx=reindex_ctx)
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

    def _index_file_inner(self, file_path: str, kb_id: str, doc_id: str,
                          file_hash: str, reindex_ctx: dict | None = None) -> dict:
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
        # department 必须按文件路径派生，不能用 self.department：批量 sync 时
        # indexer 是单实例跨多部门构建的，self.department 只是构造默认值。
        department = self._derive_department(file_path)
        # §4 权限范围（2026-09-17）：文档访问所需权限从 registry 行读（上传/
        # 入库脚本在 register 时写入），缺省 general 开放。不用实例级值，
        # 理由同 department。
        # 兼容旧测试与轻量 mock：registry 正常返回 Mapping；非 Mapping
        # 返回值不能参与字段读取，否则 MagicMock 会被误识别成 fixture_set。
        raw_doc_row = self.registry.get_by_path(file_path)
        doc_row = raw_doc_row if isinstance(raw_doc_row, Mapping) else {}
        fixture_set = str(doc_row.get("fixture_set") or self.fixture_set or "") or None
        if fixture_set not in (None, *_EVAL_FIXTURE_SETS):
            raise ValueError(f"未知评测 fixture_set: {fixture_set}")
        permission_scope = (doc_row.get("permission_scope") or "general").strip() or "general"
        # §6 版本治理（2026-09-17 R4）：版本标识与生效窗口同样从 registry 行
        # 声明值读（入库脚本 register 时写入），随 chunk 落向量库供检索期
        # as_of/current/all_versions enforcement（backend/rag/versioning.py）。
        version_id = str(doc_row.get("version_id") or "")
        effective_from = str(doc_row.get("effective_from") or "")
        effective_to = str(doc_row.get("effective_to") or "")
        supersedes_version_id = str(doc_row.get("supersedes_version_id") or "")
        try:
            source_priority = int(doc_row.get("source_priority") or 0)
        except (TypeError, ValueError):
            source_priority = 0
        quality_status = str(doc_row.get("quality_status") or "unknown")

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
        _qc: dict = {}
        try:
            from backend.rag.preprocessing.pipeline import parse_and_chunk_full
            chunks, _qc = parse_and_chunk_full(file_path)
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
                _apply_fixture_metadata(ch.metadata, fixture_set)
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

        # ── ②.5 clean（C3 起不再二次清洗）──
        # pipeline.parse_and_chunk 已做节点级清洗（控制字符/全角半角/HTML/
        # PDF 页眉页脚/URL 邮箱等，DocumentCleaner 同源）。本段原有的
        # chunk 级二次清洗删除：
        #   1. 重复劳动——同一文本被 DocumentCleaner 处理两遍；
        #   2. 非幂等操作（URL/邮箱改写、中文标点统一）二次执行会再次
        #      变更文本，使 chunk 内容与解析产物漂移，影响 embedding 输入
        #      稳定性与章节映射；
        #   3. chunk_id 为内容派生，二次变更会让同内容重索引产出不同 id，
        #      破坏幂等重索引承诺。
        # span 保留（观测树连续性），标记 skip 供 Trace 详情页解释。
        clean_span = trace_collector.start_span(
            "index_clean",
            parent_id="index_upload",
            name=f"Clean {os.path.basename(file_path)}",
            type="clean",
            kind=SpanKind.INDEX_CLEAN.value,
            input={"doc_count": len(chunks)},
        )
        trace_collector.end_span(clean_span,
            metrics={"skipped": "cleaned_in_pipeline", "docs": len(chunks)},
        )

        # ── ④ dedup（SHA256 缓存检查）──
        dedup_span = trace_collector.start_span(
            "index_dedup",
            parent_id="index_upload",
            name=f"Check {os.path.basename(file_path)}",
            type="dedup",
            kind=SpanKind.INDEX_DEDUP.value,
            input={"file_hash": file_hash},
        )
        # ── ④.5 任务状态持久化（parsing 占位行）—— 必须在 dedup 检查之前 ──
        # 后台索引任务不持久化，进程重启即丢；启动时 sync() 依据该状态恢复中断
        # 文档。放在 dedup 之前的第二个原因：reindex_file 是强制重建入口，
        # 占位行把状态置为 parsing 后，下方 dedup 早退（要求 active）不再触发，
        # 否则"跳过重建 + 先写后删清理旧向量"叠加会凭空丢文档。
        # 已有行（重索引场景）只改状态、保留 chunk_ids 等历史元数据；
        # 成功后 register() 覆盖为 active。软失败不影响索引。
        try:
            self.registry.register_in_progress(
                file_path, doc_id=doc_id, file_hash=file_hash,
                kb_id=kb_id, department=department,
            )
        except Exception as e:
            logger.debug(f"[Indexer] parsing 占位行写入失败（不影响索引）: {e}")
        dup_check = self.registry.get_by_path(file_path)
        if dup_check and dup_check.get("file_hash") == file_hash and dup_check.get("status") == "active":
            trace_collector.end_span(dedup_span,
                metrics={"cached": True, "existing_doc_id": dup_check.get("doc_id", "")})
            logger.info(f"[Dedup] 文件未变更，跳过索引: {file_path}")
            # 返回契约：_index_file 包装器对返回值做 .get()，裸 return None 会 AttributeError
            return {
                "chunk_count": int(dup_check.get("chunk_count") or 0),
                "doc_db_id": dup_check.get("doc_db_id", ""),
                "file_hash": file_hash,
                "skipped": True,
            }
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
            total_before_filter = len(chunks)
            filtered_chunks = []
            filtered_details: list[dict] = []  # R-P1-3: 被过滤 chunk 明细（原因可追溯）
            for chunk in chunks:
                ok, reason = chunk_filter.should_keep(chunk.page_content, chunk.metadata)
                if ok:
                    if chunk.metadata.get("pii_masked"):
                        chunk.page_content = ChunkFilter.apply_pii_mask(chunk.page_content)
                    filtered_chunks.append(chunk)
                else:
                    filtered_details.append({
                        "chunk_index": chunk.metadata.get("chunk_index", len(filtered_chunks)),
                        "reason": reason,
                        "preview": chunk.page_content[:80],
                    })
                    logger.debug(f"[Filter] 拒绝 chunk: {reason} (doc={file_path})")
            filtered_count = len(filtered_details)
            if filtered_count > 0:
                reason_breakdown: dict[str, int] = {}
                for d in filtered_details:
                    reason_breakdown[d["reason"]] = reason_breakdown.get(d["reason"], 0) + 1
                logger.info(
                    f"[Filter] {file_path}: 过滤 {filtered_count}/{total_before_filter} "
                    f"个 chunk ({reason_breakdown})"
                )
            chunks = filtered_chunks

            trace_collector.end_span(chunk_span,
                metrics={"raw_chunks": total_before_filter,
                         "kept_chunks": len(filtered_chunks),
                         "filtered_out": filtered_count,
                         "chunk_size": chunk_size,
                         "chunk_overlap": chunk_overlap},
                output={"preview": [c.page_content[:100] for c in filtered_chunks[:3]],
                        "total": len(filtered_chunks),
                        "strategy": strategy_name,
                        "chunk_size": chunk_size,
                        "chunk_overlap": chunk_overlap,
                        # R-P1-3: 明细持久化进 trace（截断到 20 条防膨胀）
                        "filtered_details": filtered_details[:20]})
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
            "fixture_set": fixture_set or "",
            "department": department,  # 同上：用路径派生值，否则部门隔离失效
            "permission_scope": permission_scope,  # §4 权限范围：registry 行声明值
            # §6 版本治理：registry 行声明值（R4）
            "version_id": version_id,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "supersedes_version_id": supersedes_version_id,
            "source_priority": source_priority,
            "quality_status": quality_status,
            "doc_type": "general",
            "person_names": [],
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

        # 截断留痕：分块超上限被 _enrich 截断时，检索覆盖天然不完整，
        # 必须写进 registry quality_issues 可追溯，而不是只有日志
        chunks_truncated = any(ch.metadata.get("chunks_truncated") for ch in chunks)
        if chunks_truncated:
            _append_quality_issue(
                doc_meta, "chunks_truncated(超出单文档上限被截断,检索覆盖不完整)")

        # §5.1 质量记录：扫描件经 OCR 兜底识别 → 留痕（pipeline 已打标到 chunk
        # metadata）；OCR 文本质量天然低于文字层，门禁/审计按此字段甄别
        if any(ch.metadata.get("ocr_triggered") for ch in chunks):
            ocr_pages = max(
                (int(ch.metadata.get("ocr_pages") or 0) for ch in chunks),
                default=0,
            )
            _append_quality_issue(
                doc_meta, f"ocr_triggered(扫描件经OCR识别,{ocr_pages}页产出文本)")

        # R-P1-3: 过滤留痕汇总进 quality_issues（逐条明细在 index_chunk span
        # output.filtered_details，trace 详情页可查），保证误删可事后审计
        filter_summary = _filter_quality_summary(filtered_details)
        if filter_summary:
            _append_quality_issue(doc_meta, filter_summary)

        # ═══ §5.1–5.3 质量门禁（2026-09-17 R3）：每文档质量记录 + 类型化校验 ═══
        # 记录 JSON 落盘 data/quality_records/{kb}/{doc_id}.json；硬异常（0 叶子/
        # 0 字符等）抛错 → 文档 failed 不得伪装 active；软异常（截断/降级/大量
        # 过滤/值丢失）追加 quality_issues 留痕。门禁自身故障不阻断索引主流程。
        _raw_ast = _qc.get("raw_ast")
        if _raw_ast is not None:
            q_span = trace_collector.start_span(
                "index_quality",
                parent_id="index_upload",
                name="Quality record",
                type="audit",
                kind=SpanKind.INDEX_PARSE.value,
                input={"file_path": file_path, "doc_id": doc_id},
            )
            try:
                from backend.rag.preprocessing.quality_gate import (
                    anomaly_summary,
                    build_quality_record,
                    hard_anomalies,
                    persist_quality_record,
                    run_typed_validation,
                )
                _record = build_quality_record(
                    file_path=file_path, doc_id=doc_id, kb_id=kb_id,
                    raw_ast=_raw_ast, chunks=chunks,
                    file_size=file_size, file_hash=file_hash or "",
                    doc_type=_qc.get("doc_type", ""),
                    strategy_name=_qc.get("strategy_name", ""),
                    completeness=_qc.get("completeness"),
                    filtered_details=filtered_details,
                    truncated=chunks_truncated,
                    ocr_triggered=bool(getattr(_raw_ast, "ocr_triggered", False)),
                    ocr_pages=int(getattr(_raw_ast, "ocr_pages", 0) or 0),
                )
                _anomalies = run_typed_validation(_record, _raw_ast)
                _hard = hard_anomalies(_anomalies)
                if _hard:
                    # §5.3 硬异常：文档不得伪装 active
                    raise ValueError(
                        f"质量门禁硬异常: {_hard[0]['check']}({_hard[0]['detail']})"
                    )
                _soft = anomaly_summary(_anomalies)
                if _soft:
                    _append_quality_issue(doc_meta, _soft)
                _qpath = persist_quality_record(_record)
                doc_meta["quality_record_path"] = _qpath
                trace_collector.end_span(q_span,
                    metrics={"anomalies_total": len(_anomalies),
                             "anomalies_hard": len(_hard),
                             "anomalies_warn": len(_anomalies) - len(_hard),
                             "record_path": _qpath})
                logger.info(
                    f"[Indexer] 质量记录 {_qpath} "
                    f"(nodes={_record['parsing']['node_count']}, "
                    f"chunks={_record['chunking']['chunk_count']}, "
                    f"anomalies={len(_anomalies)})"
                )
            except ValueError:
                trace_collector.end_span(q_span, status="error",
                    metrics={"error": "quality_gate_hard_anomaly"})
                raise
            except Exception as e:
                trace_collector.end_span(q_span, status="error",
                    metrics={"error": str(e)[:200]})
                logger.warning(
                    f"[Indexer] 质量记录构建失败（不阻断索引）: "
                    f"{type(e).__name__}: {e}", exc_info=True,
                )

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
        fixture_set_val = doc_meta.get("fixture_set") or None
        domain_val = doc_meta.get("business_domain", "") or "general"

        # 模拟问题（从 doc_meta 拿；metadata 构建阶段已写入 questions_by_chunk）
        questions_by_chunk = doc_meta.get("questions_by_chunk", []) or []

        # ── 4.3c: 表格行描述（kv 数据行 → 一句话语义，供 embedding 前缀）──
        table_row_indexes = [
            i for i, ch in enumerate(chunks)
            if ch.metadata.get("chunk_type") == "table_row"
        ]
        if table_row_indexes:
            try:
                from backend.rag.preprocessing.table_describe import generate_table_descriptions
                # 同步直调：_index_file_inner 整体在线程池执行，无事件循环阻塞问题
                row_descs = generate_table_descriptions(
                    [chunks[i].page_content for i in table_row_indexes],
                    table_summary=doc_meta.get("summary", "") or "",
                )
                for i, desc in row_descs.items():
                    if 0 <= i < len(table_row_indexes):
                        chunks[table_row_indexes[i]].metadata["table_desc"] = desc
            except Exception as e:
                logger.warning(f"[TableDesc] 生成失败（无描述降级）: {e}")

        # ── 4.3a/4.3b: 实体与时间引用随 chunk 落库（数据可达性）──
        # 检索加分/时效降权策略待评测数据与业务规则输入后另做
        entities_json = ""
        if doc_meta.get("entities"):
            try:
                entities_json = json.dumps(doc_meta["entities"], ensure_ascii=False)[:500]
            except (TypeError, ValueError):
                entities_json = ""
        time_refs_val = doc_meta.get("time_refs") or []
        time_refs_str = ", ".join(str(t) for t in time_refs_val[:10]) if isinstance(time_refs_val, list) else str(time_refs_val)[:200]

        for i, ch in enumerate(chunks):
            ch.metadata["doc_type"] = doc_type_val
            ch.metadata["person_names"] = person_val
            ch.metadata["kb_id"] = kb_id_val
            _apply_fixture_metadata(ch.metadata, fixture_set_val)
            ch.metadata["business_domain"] = domain_val
            ch.metadata["department"] = department
            # §4 权限范围：随 chunk 进向量库/doc_db，检索侧按请求者持有权限
            # 裁决（backend/rag/permissions.py）；缺省 general 行为不变
            ch.metadata["permission_scope"] = permission_scope
            # §6 版本治理：随 chunk 进向量库/doc_db，检索侧按 as_of/current/
            # all_versions 要求裁决（backend/rag/versioning.py）；
            # version_id 空 = 非版本链文档（无时效约束，恒可见）
            ch.metadata["version_id"] = version_id
            ch.metadata["effective_from"] = effective_from
            ch.metadata["effective_to"] = effective_to
            ch.metadata["supersedes_version_id"] = supersedes_version_id
            ch.metadata["source_priority"] = source_priority
            ch.metadata["quality_status"] = quality_status
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

            # 4.1: 审核状态随 chunk 落库——near_dup 文档 = pending_review，
            # 供检索层 where 过滤（$ne）与前端文档列表展示；正常文档 active
            ch.metadata["review_status"] = (
                "pending_review" if doc_meta.get("near_dup_id") else "active"
            )
            # 4.3a: 结构化实体（检索加分策略待评测数据）
            if entities_json:
                ch.metadata["entities"] = entities_json
            # 4.3b: 时间引用（时效降权策略待业务规则输入）
            if time_refs_str:
                ch.metadata["time_refs"] = time_refs_str

            # 章节归属（C3 改为兜底：切分策略（含 Fixed/Recursive 的
            # section 感知合并）已注入的 section_title 权威优先；
            # find() 首次出现位置映射只在 chunk 无标题时使用——
            # 文本重复出现（模板化措辞）会错配章节）
            if section_positions and not (ch.metadata.get("section_title") or "").strip():
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
                 "fixture_set": fixture_set_val or "",
                 "department": department,
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
            # doc 级全文超长会被 embedding 模型截断/报错，且大文件内存峰值翻倍；
            # Stage1 doc 级检索只需头部语义即可定位文档，超长部分截断。
            # 截断打标进 metadata，doc 级检索对长文档天然残缺，需可观测。
            # 1.2 增强：summary/章节头部拼入 doc 级文本，LLM 元数据参与 doc 级向量
            doc_level_text = _build_doc_level_text(full_text, doc_meta)
            if len(full_text) > DOC_LEVEL_TEXT_MAX_CHARS:
                doc_db_meta["doc_level_truncated"] = "true"
                doc_db_meta["doc_level_full_chars"] = len(full_text)
            ids = self.doc_db.add_texts(texts=[doc_level_text], metadatas=[doc_db_meta]) if doc_level_text else []
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
            # person_names 存 list（规则/统一两路已改）；兼容历史逗号串
            "person_count": len(doc_meta.get("person_names") or []) if isinstance(doc_meta.get("person_names"), (list, tuple))
                            else (len(str(doc_meta.get("person_names")).split(",")) if doc_meta.get("person_names") else 0),
            "doc_db_id": doc_db_id,
        }
        if chunks_truncated:
            metrics["chunks_truncated"] = "true"
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
        # 预嵌入：除失败预检外，成功向量直接传给 vectordb.add_documents(embeddings=...)，
        # 避免 langchain 内部对同一批文本再次全量嵌入（原先向量被丢弃，成本翻倍）
        precomputed_vectors = self._embed_with_retry(
            chunks, embed_span, doc_summary=doc_meta.get("summary", "") or "")
        trace_collector.end_span(embed_span,
            metrics={"attempted": len(chunks),
                     "succeeded": len(precomputed_vectors),
                     "failed": len(chunks) - len(precomputed_vectors)})

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
                if len(precomputed_vectors) == len(chunks):
                    # 预嵌入全部成功 → 直接传入向量，跳过 add_documents 内部的二次嵌入
                    chunk_ids = self.vectordb.add_documents(
                        chunks, embeddings=precomputed_vectors) or []
                else:
                    # 预嵌入不完整（部分 chunk 嵌入失败）→ 回退由向量库统一嵌入，
                    # 保证不产生向量空洞；此时预嵌入仅充当失败预检
                    logger.warning(
                        f"[Embed] 预嵌入不完整 ({len(precomputed_vectors)}/{len(chunks)})，"
                        f"回退由向量库嵌入: {os.path.basename(file_path)}"
                    )
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
            # F4: 重索引时只精确清本次新写入，绝不按 doc_id 条件删（会连带旧版本）
            self._cleanup_partial_write(doc_id, file_path=file_path,
                                        reindex_ctx=reindex_ctx,
                                        new_doc_db_id=doc_db_id)
            raise

        # ── ⑤.5 BM25 同步（P0-1：上传/重索引后立即同步，避免"上传成功但 BM25 未更新"）──
        # 仅在显式传入 bm25_store 时执行（上传/重索引路径）；pipeline 启动 sync 不传（BM25 随后全量重建）。
        # 注意：chunk metadata 需含 doc_id/source_file 等字段（BM25 删除/去重依赖），_enrich 与上方注入已提供。
        if self.bm25_store is not None and chunks:
            try:
                self.bm25_store.replace_documents(
                    chunks, k=BM25_CANDIDATE_K, doc_id=doc_id, file_path=file_path,
                )
                logger.info(
                    f"[BM25] 文档已替换 {len(chunks)} chunks: {os.path.basename(file_path)}"
                )
            except Exception as e:
                logger.error(f"[BM25] 替换同步失败 (doc_id={doc_id}): {e}")

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
                    # §4 权限范围：沿用 registry 行声明值（doc_meta 已带），
                    # 否则最终 upsert 会把入库脚本预注册的受限标记冲回 general
                    "permission_scope": doc_meta.get("permission_scope", "general"),
                    # §6 版本治理（R4）：沿用 registry 行声明值，防止最终
                    # upsert 把入库脚本预注册的版本窗口冲掉
                    "version_id": doc_meta.get("version_id", ""),
                    "effective_from": doc_meta.get("effective_from") or None,
                    "effective_to": doc_meta.get("effective_to") or None,
                    "supersedes_version_id": doc_meta.get("supersedes_version_id", ""),
                    "source_priority": doc_meta.get("source_priority", 0),
                    "quality_status": doc_meta.get("quality_status", "unknown"),
                    "fixture_set": fixture_set_val or "",
                },
            )
        except Exception:
            # F4: registry 阶段失败同样精确清理——此向量/BM25 新数据已写入，
            # doc_id 条件删会把旧版本向量连带删掉
            self._cleanup_partial_write(doc_id, file_path=file_path,
                                        reindex_ctx=reindex_ctx,
                                        new_doc_db_id=doc_db_id)
            raise

        # P2-2:返回 dict 给 _index_file wrapper,消除 reindex_file 反查 registry 的需要
        return {
            "chunk_count": len(chunk_ids),
            "doc_db_id": doc_db_id,
            "file_hash": file_hash,
        }

    # ---- Stage 委托层（阶段3拆分：实现见 stages/embedding_stage.py）----

    @property
    def _embed_stage(self) -> EmbeddingStage:
        """embedding Stage（实例级缓存，跨文件复用 embed cache）。"""
        st = getattr(self, "_embed_stage_inst", None)
        if st is None:
            st = self._embed_stage_inst = EmbeddingStage(self.embedding)
        return st

    @staticmethod
    def _embed_text_for(chunk, doc_summary: str = "") -> str:
        return EmbeddingStage.text_for(chunk, doc_summary=doc_summary)

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

    async def _build_doc_metadata(self, full_text: str, base_meta: dict, parent_span_id: str = "", chunks_text: list[str] | None = None) -> dict:
        """构建文档级元数据 — 委托 MetadataStage（stages/metadata_stage.py）。

        返回 dict 契约与拆分前完全一致（下游 chunk 注入 / doc_db 落库 /
        registry.register 零感知）。
        """
        return await self._meta_stage.build(
            full_text, base_meta,
            parent_span_id=parent_span_id, chunks_text=chunks_text)

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
        if old_doc_id and self._should_use_version_snapshot(old_doc_type, file_path):
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
                # 快照失败 → 兜底删除旧数据，不影响新索引
                logger.warning(
                    f"[REINDEX] 版本快照失败，兜底删除旧数据: {e}"
                )
                self._remove_document(old_doc_id, file_path=file_path)
                self.registry.mark_deleted_by_doc_id(old_doc_id)
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
        index_result = self._index_file(
            file_path, file_hash=file_hash, reindex_ctx=reindex_ctx)
        # dedup 命中（内容未变，如占位行写入失败的极端场景）→ 没有新数据，
        # 清理旧向量会造成数据丢失，直接原样返回
        skipped = bool(index_result.get("skipped"))
        if not skipped:
            # 2.25 清理被取代的旧向量（精确按旧 ID，不碰同 doc_id 的新 chunk）
            self._cleanup_superseded(
                old_doc_id, old_chunk_ids, old_doc_db_id,
                new_doc_db_id=index_result.get("doc_db_id", ""),
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
        file_path: str = "",
    ):
        """重索引成功后清理被取代的旧向量（"先写后删"的删半边）。

        只按旧 ID 精确删除，绝不按 doc_id 条件删 —— 新旧 chunk 共享同一
        doc_id，条件删会把刚写入的新向量一起删掉。BM25 无需处理：
        replace_documents 在单次重建里已完成旧条目移除 + 新条目写入。
        """
        if not old_doc_id:
            return
        if old_chunk_ids:
            try:
                self.vectordb.delete(ids=old_chunk_ids)
                logger.info(
                    f"[REINDEX] 已清理旧 chunk 向量 {len(old_chunk_ids)} 条 "
                    f"(doc_id={old_doc_id})"
                )
            except Exception as e:
                logger.warning(
                    f"[REINDEX] 旧 chunk 向量清理失败（残留孤儿向量，"
                    f"doc_id={old_doc_id}）: {e}"
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
            from backend.rag.indexing.chunk_store import get_chunk_store
            get_chunk_store().delete_by_doc_id(doc_id)
        except Exception as e:
            logger.warning(f"删除 chunk_store 失败 (doc_id={doc_id}): {e}")
        if self.bm25_store is not None:
            try:
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
