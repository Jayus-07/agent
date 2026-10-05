"""rag/indexing/reindex_service.py — 文档重索引执行体（2026-10-02 remote 断层收口）。

从 rag_documents.reindex_document 路由抽出的共享执行逻辑，两条路径共用：
- app 本地同步路径（RAG_REINDEX_ASYNC_ENABLED 关 / RAG_MODE=local 降级）
- rag-index-worker 的 Celery 任务 tasks.reindex_document（remote 任务化主路径）

执行语义（沿用 indexer.reindex_file 既有安全网，不在本层重造）：
- 先写后删：新索引成功后才清理被取代的旧向量，失败旧版本原样在服；
- BM25 代次发布：indexer 发布新代次快照，其他进程（rag-service）按
  published_generation 每请求检查自动热刷新（与上传同机制）。

并发互斥：doc_id 粒度 PG advisory xact lock（try 语义）——两个管理员同时
对同一文档提交重索引时，后到者快速失败而非排队悬挂。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from backend.shared.logger import logger

# 进度镜像 key 前缀（复用 upload: 走 _write_progress_redis 同一通道，终态
# 词表 done/error 也在 _SSE_TERMINAL_STAGES 保护范围内）
REINDEX_PROGRESS_PREFIX = "reindex:"


class ReindexInProgressError(RuntimeError):
    """同文档已有重索引在执行（advisory lock 争用，非可重试业务错误）。"""


def progress_key(doc_id: str) -> str:
    """重索引进度镜像的 upload 通道 key。"""
    return f"{REINDEX_PROGRESS_PREFIX}{doc_id}"


def _default_emit() -> Callable[..., None]:
    def _noop(stage: str, message: str = "", **extra) -> None:
        return None
    return _noop


def _lock_connection():
    """advisory lock 用的 memory 库连接（与 task_service 同源同 DSN 口径）。"""
    import psycopg

    from backend.config.database import DB_CONNECT_TIMEOUT, MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    dsn = (f"postgresql://{c['user']}:{c['password']}"
           f"@{c['host']}:{c['port']}/{c['dbname']}")
    return psycopg.connect(dsn, autocommit=False,
                           connect_timeout=DB_CONNECT_TIMEOUT)


def load_reindex_target(registry, doc_id: str) -> dict:
    """执行前重验：文档仍存在且源文件可读（提交与执行之间可能被删除）。

    返回 registry 行；不满足时抛 ValueError（任务层按 validation_error
    分类为不可重试终态）。
    """
    doc = registry.get_by_doc_id(doc_id)
    if not doc:
        raise ValueError(f"文档不存在: {doc_id}")
    file_path = doc.get("file_path", "")
    import os

    from backend.config.rag import RAG_UPLOAD_PATH_GUARD
    if RAG_UPLOAD_PATH_GUARD:
        from backend.rag.indexing.upload_path_guard import test_artifact_path_reason
        from backend.config.database import DOCS_DIRECTORY

        # 测试夹具可以位于仓库外的临时目录；只有平台文档根目录内的
        # 路径才套用测试产物拒绝规则，避免把合法的外部存储误判为测试文件。
        file_real = os.path.realpath(str(file_path or ""))
        docs_root = os.path.realpath(str(DOCS_DIRECTORY))
        try:
            under_docs_root = os.path.commonpath([file_real, docs_root]) == docs_root
        except ValueError:
            under_docs_root = False
        if under_docs_root and test_artifact_path_reason(file_path):
            raise ValueError("测试临时路径文件被拒绝")

    if not file_path or not os.path.isfile(file_path):
        raise ValueError(f"文件不存在: {file_path}")
    return doc


def build_indexer(registry, doc: dict, pipeline, batch_id: str | None):
    """按 registry 回读归属构造 IncrementalIndexer（与原路由逻辑逐字对齐）。

    kb_id 传 "default" 才能触发 indexer._derive_kb_id() 的路径反推兜底；
    department 缺失时退 "general"——重索引不得把归属覆盖成默认值。
    """
    from backend.config import DOCS_DIRECTORY
    from backend.rag.indexing.indexer import IncrementalIndexer
    from backend.rag.indexing.processing_lineage_pg import (
        get_processing_lineage_repository,
    )

    reg_kb = doc.get("kb_id") or ""
    reg_dept = doc.get("department") or ""
    return IncrementalIndexer(
        DOCS_DIRECTORY, pipeline.vectordb, pipeline.doc_db, pipeline.embedding,
        registry,
        kb_id=reg_kb or "default",
        department=reg_dept or "general",
        # 生产 BM25Store 会从向量集合原子重建，并排除旧 chunk。
        bm25_store=pipeline.bm25_store,
        processing_lineage_repository=get_processing_lineage_repository(),
        processing_task_id=f"reindex:{doc.get('doc_id', '')}",
        processing_batch_id=batch_id,
    )


def log_reindex_success(doc_id: str, doc_name: str, source: str, *,
                        trace_id: str | None, batch_id: str | None,
                        result: dict, duration_ms: int,
                        executor: str = "rag_index_worker",
                        user_id: str = "") -> None:
    """终态成功操作日志（审计口径与原路由 _safe_log_op 对齐）。"""
    from backend.config import DOC_OPERATION_LOG_PATH
    from backend.rag.indexing.operation_log_pg import (
        PostgresDocumentOperationLogger,
    )

    doc = result.get("doc") or {}
    try:
        PostgresDocumentOperationLogger(DOC_OPERATION_LOG_PATH).log(
            doc_id=doc_id, doc_name=doc_name, operation="reindex", source=source,
            user_id=user_id or "anonymous",
            trace_id=trace_id or None, batch_id=batch_id, result="success",
            detail={"chunk_count": result.get("chunk_count", 0),
                    "file_hash": result.get("file_hash", ""),
                    "doc_type": (doc.get("doc_type", "general")
                                 if isinstance(doc, dict) else "general"),
                    "skipped": bool(result.get("skipped")),
                    "executor": executor},
            duration_ms=duration_ms)
    except Exception:  # noqa: BLE001 — 审计写失败不阻断业务
        logger.warning("[ReindexService] %s 成功操作日志写入失败", doc_id,
                       exc_info=True)


def log_reindex_failed(doc_id: str, source: str, *, batch_id: str | None,
                       error: str, duration_ms: int, user_id: str = "") -> None:
    """终态失败操作日志（由调用方在终态出口调用；重试中间态不写）。"""
    from backend.config import DOC_OPERATION_LOG_PATH
    from backend.rag.indexing.operation_log_pg import (
        PostgresDocumentOperationLogger,
    )

    try:
        from backend.config import DOC_REGISTRY_PATH
        from backend.rag.indexing.doc_registry import DocumentRegistry

        doc_name = ""
        try:
            row = DocumentRegistry(DOC_REGISTRY_PATH).get_by_doc_id(doc_id)
            doc_name = (row or {}).get("file_name", "")
        except Exception:  # noqa: BLE001 — 名字补查失败不阻断日志
            pass
        PostgresDocumentOperationLogger(DOC_OPERATION_LOG_PATH).log(
            doc_id=doc_id, doc_name=doc_name, operation="reindex", source=source,
            user_id=user_id or "anonymous",
            trace_id=None, batch_id=batch_id, result="failed",
            detail={"error": str(error)[:200], "executor": "rag_index_worker"},
            duration_ms=duration_ms)
    except Exception:  # noqa: BLE001 — 审计写失败不阻断业务
        logger.warning("[ReindexService] %s 失败操作日志写入失败", doc_id,
                       exc_info=True)


def run_reindex(doc_id: str, *, registry=None, pipeline=None,
                batch_id: str | None = None, source: str = "worker",
                emit: Callable[..., None] | None = None,
                executor: str = "rag_index_worker",
                user_id: str = "") -> dict:
    """单文档重索引完整执行（本地同步路径与 Celery 任务共用）。

    成功返回 {"ok": True, "doc_id", "chunk_count", "hash", "doc", "skipped"}
    并写成功操作日志；失败原样上抛（失败操作日志由调用方终态出口写，
    重试中间态不写——分类/重试决策归任务层）。
    emit(stage, message, **extra)：进度发射器，缺省 no-op。
    """
    from backend.config import DOC_REGISTRY_PATH
    from backend.rag.indexing.doc_registry import DocumentRegistry

    _emit = emit or _default_emit()
    t0 = time.monotonic()

    if registry is None:
        registry = DocumentRegistry(DOC_REGISTRY_PATH)
    doc = load_reindex_target(registry, doc_id)
    doc_name = doc.get("file_name", "")

    # doc_id 粒度互斥：try 语义，争用即快速失败（不排队悬挂 worker 槽位）。
    # xact 锁随连接生命周期释放（with 退出 commit/rollback 均解锁）。
    with _lock_connection() as lock_conn, lock_conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    (f"rag_reindex:{doc_id}",))
        if not (cur.fetchone() or [False])[0]:
            raise ReindexInProgressError(
                f"文档 {doc_id} 已有重索引在执行，请勿重复提交")

        if pipeline is None:
            from backend.rag.pipeline import get_rag_pipeline
            pipeline = get_rag_pipeline()

        indexer = build_indexer(registry, doc, pipeline, batch_id)
        result = indexer.reindex_file(doc["file_path"])

    skipped = bool(result.get("skipped"))
    if not skipped:
        # 发布方本进程立即换用新代（其余进程按代次检查自动跟进）
        try:
            pipeline.refresh_bm25_from_store()
        except Exception as e:  # noqa: BLE001 — 不影响索引结果
            logger.warning("[ReindexService] BM25 刷新失败（不影响索引结果）: %s", e)

    duration_ms = int((time.monotonic() - t0) * 1000)
    updated_doc = registry.get_by_doc_id(doc_id) or {}
    payload: dict[str, Any] = {
        "ok": True, "doc_id": doc_id,
        "chunk_count": result.get("chunk_count", 0),
        "hash": result.get("file_hash", ""),
        "doc": updated_doc, "skipped": skipped,
        "trace_id": result.get("trace_id", ""),
    }
    log_reindex_success(doc_id, doc_name, source, trace_id=result.get("trace_id"),
                        batch_id=batch_id, result=payload, duration_ms=duration_ms,
                        executor=executor, user_id=user_id)
    _emit("done" if not skipped else "duplicate",
          "内容未变化，已跳过重索引" if skipped else "重索引完成",
          doc_id=doc_id, chunk_count=payload["chunk_count"],
          file_hash=payload["hash"], skipped=skipped,
          duration_ms=duration_ms)
    return payload
