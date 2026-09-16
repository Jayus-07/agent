"""tasks/index_tasks.py — RAG 上传索引 Celery 任务（阶段4 队列化）。

职责划分（对齐 agent_tasks.py 模式）：
- Celery：调度（排队/重试/超时杀），不感知索引内部状态
- 状态权威：registry（文档状态）+ Redis 进度镜像（{REDIS_KEY_PREFIX}upload:{id}）
- 索引执行：复用 rag_upload._do_index_sync —— Worker 进程里
  _progress_queues 为空 → sync_emit 只写 Redis 镜像，天然跨进程安全

重试语义：
- 瞬态异常（LLM 超时/embedding/向量库抖动）→ autoretry（退避 + 抖动）
- ChunkingEmptyError（扫描件/结构损坏）是业务终态，重试无意义
  → dont_autoretry，直接终态收口（保留源文件供排查）
- 末次尝试失败才发终态 error 事件（重试期间只发 uploading 进度，
  避免 SSE 客户端过早收到失败终态）

SSE：Worker 与 API 分属不同进程，进度经 Redis 镜像由 API 侧
stream_upload_progress 的 Redis 轮询通道消费（事件格式与队列模式一致）。
"""
from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from backend.config.tasks import (
    CELERY_MAX_RETRIES,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
)
from backend.shared.logger import logger

#: 业务终态异常：重试无意义（扫描件/结构损坏文档，重跑结果相同）
_NO_RETRY_EXC = (KeyboardInterrupt, SystemExit)


def _redis_emit_fn(upload_id: str):
    """Worker 侧事件发射器：仅写 Redis 进度镜像（无进程内队列概念）。"""
    from backend.app.api.routes.rag_upload import _write_progress_redis

    def emit(stage: str, message: str = "", **extra) -> None:
        _write_progress_redis(upload_id, stage, message, **extra)

    return emit


def execute_index_task_impl(upload_id: str, filepath: str, filename: str,
                            kb_id: str = "policy_general",
                            department: str = "general", source: str = "",
                            batch_id: str | None = None,
                            upload_elapsed_ms: int | None = None,
                            was_overwrite: bool = False,
                            retries: int = 0) -> dict:
    """索引执行主体（Celery task 与 eager 测试共用的纯函数）。

    成功返回 {"status": terminal, ...}；失败上抛（由 Celery 判定
    autoretry 或终审 FAILED）。终态事件（done/duplicate/error）写 Redis
    镜像，供 SSE 轮询通道消费。
    """
    import time as _time

    from backend.app.api.routes.rag_upload import (
        _do_index_sync, _settle_index_result,
    )

    emit_fn = _redis_emit_fn(upload_id)
    t0 = _time.time()
    if retries:
        emit_fn("uploading", f"索引重试（第 {retries}/{CELERY_MAX_RETRIES} 次）...")

    try:
        # main_loop 传 None：Worker 进程无 asyncio 主循环，
        # _do_index_sync 的 sync_emit 在队列不存在时只写 Redis，不碰 loop
        result = _do_index_sync(upload_id, filepath, filename, None,
                                kb_id, department)
    except Exception as e:
        from backend.rag.indexing.indexer import ChunkingEmptyError
        no_retry = isinstance(e, ChunkingEmptyError) or retries >= CELERY_MAX_RETRIES
        if no_retry:
            # 终审失败：registry 标 failed / 清理源文件 / 终态 error 事件
            _settle_index_result(
                upload_id, filepath, filename, source, batch_id, kb_id,
                upload_elapsed_ms, was_overwrite, t0,
                result=None, emit_fn=emit_fn, exc=e)
        else:
            # 仍会重试：只发进度事件，不发终态（SSE 客户端继续等待）
            emit_fn("uploading",
                    f"索引异常，将自动重试（第 {retries + 1}/{CELERY_MAX_RETRIES} 次）: "
                    f"{type(e).__name__}: {e}"[:300])
        raise

    _settle_index_result(
        upload_id, filepath, filename, source, batch_id, kb_id,
        upload_elapsed_ms, was_overwrite, t0,
        result=result, emit_fn=emit_fn)
    terminal = (result or {}).get("terminal", "done")
    doc = (result or {}).get("doc") or {}
    return {"status": terminal,
            "trace_id": (result or {}).get("trace_id") or "",
            "doc_id": doc.get("doc_id", "") if isinstance(doc, dict) else ""}


def _register_task():
    """惰性注册：celery 未安装时允许模块被非 Worker 进程安全 import。"""
    from backend.rag.indexing.indexer import ChunkingEmptyError
    from backend.tasks.celery_app import celery_app

    @celery_app.task(
        bind=True,
        name="tasks.execute_index",
        acks_late=True,
        autoretry_for=(Exception,),
        retry_backoff=CELERY_RETRY_BACKOFF,
        retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
        retry_jitter=True,
        max_retries=CELERY_MAX_RETRIES,
        # 业务终态不重试：扫描件/结构损坏文档重跑结果相同
        dont_autoretry_for=_NO_RETRY_EXC + (ChunkingEmptyError,),
    )
    def execute_index_task(self, upload_id: str, filepath: str, filename: str,
                           kb_id: str = "policy_general",
                           department: str = "general", source: str = "",
                           batch_id: str | None = None,
                           upload_elapsed_ms: int | None = None,
                           was_overwrite: bool = False) -> dict:
        try:
            return execute_index_task_impl(
                upload_id, filepath, filename, kb_id=kb_id,
                department=department, source=source, batch_id=batch_id,
                upload_elapsed_ms=upload_elapsed_ms,
                was_overwrite=was_overwrite,
                retries=self.request.retries)
        except SoftTimeLimitExceeded:
            # 超时：按终态收口（registry failed / 终态 error 事件）
            from backend.app.api.routes.rag_upload import _settle_index_result
            exc = SoftTimeLimitExceeded(f"索引超时（soft time limit）")
            _settle_index_result(
                upload_id, filepath, filename, source, batch_id, kb_id,
                upload_elapsed_ms, was_overwrite, 0.0,
                result=None, emit_fn=_redis_emit_fn(upload_id), exc=exc)
            return {"status": "error", "reason": "timeout"}
        except Exception as e:
            retries_left = CELERY_MAX_RETRIES - self.request.retries
            logger.error("[IndexTask] %s failed (剩余重试 %d): %s",
                         upload_id, retries_left, e, exc_info=True)
            raise  # 终态事件已由 impl 在末次尝试时收口

    return execute_index_task


execute_index_task = _register_task()
