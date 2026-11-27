"""RAG Upload 核心索引调度、Celery分发与同步流水线引擎。"""
from __future__ import annotations

import asyncio
from asyncio import Queue
import json
import math
import os
import shutil
import sys
import time
import uuid

from fastapi import HTTPException

from backend.app.api.routes.rag_upload_subroutes.base import *
from backend.rag.authz import RagAuthorization, RagAuthorizationError
from backend.rag.indexing.publish import SupersededCandidate
from backend.rag.progress_listener import ProgressListener

def _mark_registry_failed(filepath: str) -> None:
    """只把本次索引写入的 parsing/indexing 占位行标为 failed。

    _index_file 开始时写入的 parsing 占位行若不收口，会在每次启动时被
    sync() 反复重试永久失败文档；候选发布失败、审核态与既有 active 行
    都不能被本次候选任务改写。
    """
    try:
        row = facade._get_registry().get_by_path(filepath)
        if row and row.get("status") in ("parsing", "indexing"):
            facade._get_registry().update_status(filepath, "failed")
    except Exception as exc:
        logger.debug(f"[RAG] registry 失败状态标记异常（不影响主流程）: {exc}")


_index_semaphore: asyncio.Semaphore | None = None


def _get_index_semaphore() -> asyncio.Semaphore:
    """索引并发信号量（懒创建）：模块导入时可能尚无事件循环，
    首次使用时创建，避免 loop 绑定兼容问题。"""
    mod = sys.modules.get("backend.app.api.routes.rag_upload")
    if mod is not None and getattr(mod, "_index_semaphore", None) is not None:
        return mod._index_semaphore
    global _index_semaphore
    if mod is not None and hasattr(mod, "_index_semaphore") and mod._index_semaphore is None:
        _index_semaphore = None
    if _index_semaphore is None:
        limit = getattr(facade, "_INDEX_CONCURRENCY_LIMIT", _INDEX_CONCURRENCY_LIMIT)
        _index_semaphore = asyncio.Semaphore(limit)
        if mod is not None:
            mod._index_semaphore = _index_semaphore
    return _index_semaphore


def _latest_upload_trace_id(upload_id: str, tenant_id: str) -> str:
    """失败收口时通过 Trace 标签回填本次上传 Trace，失败不影响收口。"""
    if not upload_id or not tenant_id:
        return ""
    try:
        from backend.observability.trace_store import get_trace_store

        lookup = getattr(get_trace_store(), "latest_trace_ids_by_upload", None)
        if not callable(lookup):
            return ""
        return str(lookup([upload_id], tenant_id).get(upload_id) or "")
    except Exception as exc:  # noqa: BLE001 — Trace 回填不可阻断失败收口
        logger.warning("[RAG] 上传失败 Trace 回填失败 upload_id=%s: %s", upload_id, exc)
        return ""


def _settle_index_result(upload_id: str, filepath: str, filename: str, source: str,
                         batch_id: str | None, kb_id: str,
                         upload_elapsed_ms: int | None, was_overwrite: bool,
                         upload_t0: float, result: dict | None, emit_fn,
                         exc: BaseException | None = None,
                         staging_path: str = "", actor_id: str = "",
                         tenant_id: str = "default") -> dict | None:
    """索引终态收口 —— API 进程内与 Celery Worker 两条路径的统一出口（阶段4）。

    emit_fn: 同步事件发射器 (stage, message, **extra)。
      - API 进程内模式 = 进程内 queue.put_nowait + Redis 镜像
      - Celery Worker 模式 = 仅 Redis 镜像（跨进程，SSE 轮询消费）
    exc: 非 None 走失败分支（失败清理只删暂存文件，正式路径不动——
    B 阶段候选模型的旧版可读契约）。
    队列 None 哨兵不在本函数处理 —— API 模式调用方负责 _finalize_upload_queue；
    Worker 模式无队列概念（SSE 由 Redis 轮询，见 stream_upload_progress）。
    """
    from backend.rag.progress_listener import ProgressListener
    from backend.observability.metrics import (
        rag_index_duration_seconds,
        rag_upload_total,
    )

    def _record_terminal_metric(status: str, duration_ms: int) -> None:
        """指标旁路：监控失败不能改变索引终态。"""
        try:
            rag_upload_total.labels(status=status).inc()
            rag_index_duration_seconds.labels(status=status).observe(
                max(duration_ms, 0) / 1000.0
            )
        except Exception:  # noqa: BLE001 — 观测旁路不可阻断业务收口
            logger.debug("[RAG] 上传指标记录失败", exc_info=True)

    # ---- 失败终态 ----
    if exc is not None:
        logger.error(f"[RAG] 后台索引失败: {exc}", exc_info=exc)
        trace_id = facade._latest_upload_trace_id(upload_id, tenant_id)
        # _index_file 在普通模式下写入的 parsing 占位行必须收口为 failed；
        # 锁冲突/幂等冲突/已被新候选取代不属于本次解析失败，不得改写并发任务状态。
        if not isinstance(exc, (
            FileLockedByOtherError, UploadIdempotencyConflict,
            SupersededCandidate,
        )):
            facade._mark_registry_failed(filepath)
        # 运行记录终态（候选状态权威）。发布段失败（status=publishing，提交点
        # 已过）不标 failed——保持 publishing 等待重试续跑，不清理任何候选
        # 产物与暂存文件（续跑还要用）；仅候选期失败才走完整清理。
        run_status_now = ""
        try:
            from backend.rag.indexing.index_run_store_pg import (
                RunStateConflict, get_index_run_store,
            )
            _store = get_index_run_store()
            run_status_now = str(
                (_store.get_run(upload_id) or {}).get("status") or "")
            if run_status_now != "publishing":
                _store.mark_status(
                    upload_id, "failed", stage="settle", error=str(exc)[:500])
        except RunStateConflict:
            pass  # 已终态（幂等重放），保持一次写语义
        except Exception as run_err:
            logger.warning(f"[RAG] 运行记录 failed 标记失败: {run_err}")
        publishing_resume = run_status_now == "publishing"
        if not publishing_resume:
            # 候选期失败：暂存 + 候选产物整体清理（正式数据不在其中）
            if staging_path:
                facade._cleanup_failed_upload_sync(staging_path, was_overwrite=False)
            facade._cleanup_candidate_artifacts(upload_id, staging_path)
        # P1-4:ChunkingEmptyError 是业务失败(扫描件/结构损坏),保留源文件供排查;
        #      其它异常按孤儿文件处理逻辑清理
        from backend.rag.indexing.indexer import (
            ChunkingEmptyError,
            PrepaidContentIntegrityError,
        )
        from backend.shared.error_protocol import (
            ErrorCode,
            ProtocolError,
            error_envelope_from_exception,
        )
        duration_ms = int((time.time() - upload_t0) * 1000) + (upload_elapsed_ms or 0)
        _record_terminal_metric("failed", duration_ms)
        if isinstance(exc, ChunkingEmptyError):
            protocol_error = ProtocolError(
                ErrorCode.INVALID_PARAM,
                "文档内容无法解析，请检查文件后重试。",
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="chunking_empty",
                recoverable=True,
                error_protocol=protocol_error.envelope.to_dict(),
                trace_id=trace_id,
            )
            facade._safe_log_op("", filename, "upload", source, trace_id=trace_id or None, batch_id=batch_id,
                         user_id=str(actor_id or ""),
                         tenant_id=tenant_id,
                         detail={"error": str(exc)[:200], "error_type": "chunking_empty"})
        elif isinstance(exc, FileLockedByOtherError):
            # P0-2:锁冲突 = 同文件另一请求正在索引。源文件绝不能删 ——
            # 持锁方（Celery Worker 或另一本机任务）可能正在读它，
            # 删除会让对方索引中途断源。标 recoverable 让前端提示可重试。
            protocol_error = ProtocolError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "同文件正在被另一请求索引，请稍后重试。",
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="file_locked",
                recoverable=True,
                error_protocol=protocol_error.envelope.to_dict(),
                trace_id=trace_id,
            )
            facade._safe_log_op("", filename, "upload", source, trace_id=trace_id or None, batch_id=batch_id,
                         user_id=str(actor_id or ""),
                         tenant_id=tenant_id,
                         detail={"error": str(exc)[:200], "error_type": "file_locked"})
        elif isinstance(exc, UploadIdempotencyConflict):
            protocol_error = ProtocolError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                str(exc)[:300],
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="idempotency_conflict",
                recoverable=False,
                error_protocol=protocol_error.envelope.to_dict(),
                trace_id=trace_id,
            )
            facade._safe_log_op("", filename, "upload", source, trace_id=trace_id or None, batch_id=batch_id,
                         user_id=str(actor_id or ""),
                         tenant_id=tenant_id,
                         detail={"error": str(exc)[:200], "error_type": "idempotency_conflict"})
        elif isinstance(exc, SupersededCandidate):
            # 单发布者裁决：另一个（更新的）候选已发布，本次候选被取代。
            # 旧版本保持可读可检索——这是明确业务终态，不是系统故障。
            protocol_error = ProtocolError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                str(exc)[:300] or "该文档已被更新的版本抢先发布，本次上传被取代。",
                source="sse",
            )
            emit_fn(
                "error",
                protocol_error.envelope.message,
                error_type="superseded",
                recoverable=False,
                error_protocol=protocol_error.envelope.to_dict(),
                trace_id=trace_id,
            )
            facade._safe_log_op("", filename, "upload", source, trace_id=trace_id or None, batch_id=batch_id,
                         user_id=str(actor_id or ""),
                         tenant_id=tenant_id,
                         detail={"error": str(exc)[:200], "error_type": "superseded"})
        elif isinstance(exc, PrepaidContentIntegrityError):
            issue_codes = list(exc.issue_codes)
            if "coverage_gap" in issue_codes:
                message = "文档未入库：原文有内容没有进入最终分块，请核对解析结果和源文件。"
            else:
                message = "文档未通过入库完整性检查，尚未发布到知识库。请在入库失败页查看检查项和 Trace。"
            protocol_error = ProtocolError(
                ErrorCode.INVALID_PARAM,
                message,
                trace_id=trace_id,
                source="sse",
                details={"quality_issue_codes": issue_codes},
            )
            emit_fn(
                "error", message,
                error_type="quality_gate_rejected",
                recoverable=False,
                trace_id=trace_id,
                error_protocol=protocol_error.envelope.to_dict(),
            )
            facade._safe_log_op(
                "", filename, "upload", source,
                trace_id=trace_id or None, batch_id=batch_id,
                result="failed", user_id=str(actor_id or ""),
                tenant_id=tenant_id,
                detail={
                    "error": str(exc)[:200],
                    "error_type": "quality_gate_rejected",
                    "quality_issue_codes": issue_codes,
                    "upload_id": upload_id,
                },
            )
        else:
            envelope = error_envelope_from_exception(exc, source="sse")
            emit_fn("error", envelope.message,
                    error_protocol=envelope.to_dict(), trace_id=trace_id)
            facade._safe_log_op("", filename, "upload", source, trace_id=trace_id or None, batch_id=batch_id,
                         user_id=str(actor_id or ""),
                         tenant_id=tenant_id,
                         detail={"error": str(exc)[:200]})
        return None

    result = result or {}
    terminal = result.get("terminal", "done")
    # span_id → 前端 stage 键的统一映射（终态阶段耗时使用同一规则）
    _SPAN_STAGE_KEY = ProgressListener.SPAN_STAGE_KEY

    # ---- duplicate 终态 ----
    if terminal == "duplicate":
        duplicate_doc = result.get("doc") or {}
        stage_elapsed = result.get("stage_elapsed") or {}
        trace_id = str(result.get("trace_id") or "")
        # 用真实上传耗时覆盖（duplicate 跳过索引，后端算的 uploading 没意义）
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        total_ms = (upload_elapsed_ms or 0) + int((time.time() - upload_t0) * 1000)
        _record_terminal_metric("duplicate", total_ms)
        emit_fn("duplicate", "文件已存在，未重复索引",
                doc=facade.sanitize_doc_row(duplicate_doc), trace_id=trace_id,
                trace_association=result.get("trace_association", ""),
                stage_elapsed=stage_elapsed, total_ms=total_ms,
                processing_run_id=result.get("processing_run_id", ""),
                model_summary=result.get("model_summary", []))
        facade._safe_log_op(
            duplicate_doc.get("doc_id", ""), filename, "upload", source,
            trace_id=trace_id or None, batch_id=batch_id, result="duplicate",
            duration_ms=total_ms,
            detail={
                "duplicate": True,
                "chunk_count": duplicate_doc.get("chunk_count", 0),
                "processing_run_id": result.get("processing_run_id", ""),
                "trace_association": result.get("trace_association", ""),
            },
            user_id=str(actor_id or ""),
            tenant_id=tenant_id,
        )
        return

    # ---- 成功终态：按 path 直接拿刚索引的文档 ----
    new_doc = None
    try:
        reg = facade._get_registry()
        new_doc = reg.get_by_path(filepath)
        # 终态携带完整阶段耗时（来自后端 span duration_ms），覆盖前端累加
        raw_elapsed = result.get("stage_elapsed") or {}
        # span_id 转为前端 stage 键（如 index_chunk → chunking）
        stage_elapsed: dict[str, int] = {}
        for sid, ms in raw_elapsed.items():
            stage_key = _SPAN_STAGE_KEY.get(sid, sid)
            stage_elapsed[stage_key] = int(ms)
        # 优先用 sync_upload_impl 实测的上传耗时；缺失时回退到减法逻辑（向后兼容）
        index_elapsed_ms = int((time.time() - upload_t0) * 1000)
        total_ms = index_elapsed_ms + (upload_elapsed_ms or 0)
        _record_terminal_metric("success", total_ms)
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        elif "uploading" not in stage_elapsed:
            others = sum(v for k, v in stage_elapsed.items() if k != "uploading")
            stage_elapsed["uploading"] = max(total_ms - others, 0)
        # D-2 口径：形参 was_overwrite（os.replace 前一刻 isfile 判定，
        # 由调用方从 _do_index_sync 的 result 对齐）为唯一事实源；
        # result 内字段缺省时回退形参（两条路径都不断链）
        emit_fn("done", "索引完成", doc=facade.sanitize_doc_row(new_doc),
                trace_id=result.get("trace_id") or "",
                processing_run_id=result.get("processing_run_id") or "",
                model_summary=result.get("model_summary") or [],
                was_overwrite=bool(result.get("was_overwrite", was_overwrite)),
                stage_elapsed=stage_elapsed,
                total_ms=total_ms)
        # Phase 4: 文档变更后失效该 KB 的答案缓存（避免返回过时答案）
        try:
            from backend.rag.answer_cache import get_answer_cache
            get_answer_cache().invalidate_kb(kb_id)
        except Exception as cache_err:
            logger.debug(f"[RAG] 答案缓存失效失败（非致命）: {cache_err}")
    except Exception as e:
        emit_fn("done", "索引完成（文档信息获取失败）")
        logger.warning(f"[RAG] 获取入库文档信息失败: {e}")

    facade._safe_log_op(
        (new_doc or {}).get("doc_id", ""), filename, "upload", source,
        trace_id=result.get("trace_id") or None,
        batch_id=batch_id, result="success",
        duration_ms=int((time.time() - upload_t0) * 1000),
        detail={
            "chunk_count": result.get("chunk_count", 0),
            "file_hash": result.get("file_hash", ""),
            "duplicate": False,
            "processing_run_id": result.get("processing_run_id", ""),
            "doc_type": (new_doc or {}).get("doc_type", "general"),
            "llm_used": bool((new_doc or {}).get("llm_used", False)),
            "confidence": (new_doc or {}).get("confidence", 0),
        },
        user_id=str(actor_id or ""),
        tenant_id=tenant_id,
    )


def _dispatch_index_to_celery(**kwargs) -> dict:
    """上传索引任务入队 Celery（rag_index 队列）。broker 不可达时抛异常。

    独立成函数便于测试注入：链路 e2e 测试用 autouse fixture 把它替换为
    抛 ConnectionError，即模拟 broker 不可达 → 覆盖进程内回退路径
    （celery 队列化主路径由 test_rag_upload_celery_mode.py 单独覆盖）。

    Phase1 Step8 试点：入队前创建统一 tasks 行（TaskState 接入，失败降级
    不阻断索引）。kwargs 中的 actor_id/tenant_id 仅用于任务归属，不进
    Celery 消息（索引任务签名不感知身份）。

    Phase2 Step3：queue 由 QueueRouter 按 workflow binding 决定（收口，
    不再直接读 CELERY_RAG_INDEX_QUEUE）。

    B 阶段：broker 投递前先落 rag_index_runs 运行记录（候选状态权威，
    记录 base_generation 发布基准与暂存文件位置）——崩溃恢复 sweeper 重投
    时才有续跑依据。
    """
    from backend.services import task_service
    from backend.tasks.index_task_runtime import create_index_task_record
    from backend.tasks.index_tasks import execute_index_task
    from backend.tasks.queue_router import log_route, resolve_for_workflow

    actor_id = str(kwargs.pop("actor_id", "") or "")
    tenant_id = str(kwargs.pop("tenant_id", "") or "")
    generation = str(kwargs.get("generation", "") or "")
    upload_id = str(kwargs.get("upload_id", "") or "")
    staging_path = str(kwargs.get("staging_path", "") or "")
    file_hash = str(kwargs.get("file_hash", "") or "")
    logical_path = str(kwargs.get("filepath", "") or "")
    index_run_store = None
    if generation:
        from backend.rag.indexing.index_run_store_pg import get_index_run_store
        existing_row = facade._get_registry().get_by_path(logical_path)
        index_run_store = get_index_run_store()
        index_run_store.create_run(
            upload_id=upload_id,
            generation=generation,
            file_path=logical_path,
            kb_id=str(kwargs.get("kb_id", "") or ""),
            department=str(kwargs.get("department", "") or ""),
            file_hash=file_hash,
            staging_path=staging_path,
            tenant_id=tenant_id,
            actor_id=actor_id,
            base_generation=str(
                (existing_row or {}).get("active_generation") or ""),
            # #61：认领时观测的生命周期状态，发布 CAS 据此判断排队期间
            # 是否被下线/送审（撤销决策不得被迟到发布覆写）。
            base_status=str((existing_row or {}).get("status") or ""),
        )

    db_task_id = create_index_task_record(
        kwargs.get("upload_id", ""), kwargs.get("filename", ""),
        kb_id=kwargs.get("kb_id", "") or "policy_general",
        tenant_id=tenant_id or "default",
        user_id=actor_id or "system",
        index_kwargs=dict(kwargs))
    if db_task_id:
        kwargs = {**kwargs, "db_task_id": db_task_id}

    route = resolve_for_workflow("rag_index")
    async_result = execute_index_task.apply_async(
        kwargs=kwargs, queue=route.physical_queue
    )
    celery_task_id = str(getattr(async_result, "id", "") or "")
    if index_run_store is not None and celery_task_id:
        try:
            recorded_run = index_run_store.set_celery_task_id(
                upload_id, celery_task_id)
            if recorded_run is None:
                logger.error(
                    "[RAG] 入队成功但索引运行记录不存在，upload_id=%s",
                    upload_id)
        except Exception:  # noqa: BLE001 — 入队成功后追踪写失败不得重投
            logger.warning(
                "[RAG] 入队成功但索引 Celery ID 回填失败，upload_id=%s",
                upload_id, exc_info=True)
    if db_task_id:
        task_service.mark_queued(db_task_id,
                                 celery_task_id,
                                 queue=route.physical_queue)
        log_route(route, dispatch_type="initial", task_id=db_task_id,
                  celery_task_name="tasks.execute_index")
    return {"queued": True, "celery_task_id": celery_task_id,
            "db_task_id": db_task_id,
            "upload_id": upload_id}


def _dispatch_index_with_idempotency(
    *,
    task_kwargs: dict,
    file_hash: str,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
) -> dict:
    """提交 RAG 索引任务；可信请求拒绝绕过幂等边界。

    幂等语义（B 阶段收口）：
      - 同键同内容重放 → 返回同一任务的派发结果（SSE 续看同一进度镜像）；
      - 同键不同内容 → UploadIdempotencyConflict（HTTP 层 409），
        已存在任务与正式版本均不受影响；
      - 同键同内容但在途 → 附着到在途任务（不重复派发）。
    幂等指纹只含逻辑身份（正式路径/文件名/内容哈希/KB/部门），不含
    暂存文件 nonce——同内容重试必然命中同一条目。
    """
    if not tenant_id or not actor_id:
        return facade._dispatch_index_to_celery(**task_kwargs)

    from backend.shared.idempotency import (
        IdempotencyConflict,
        run_idempotent_operation_for_identity,
    )

    payload = {
        "filepath": task_kwargs["filepath"],
        "filename": task_kwargs["filename"],
        "file_hash": file_hash,
        "kb_id": task_kwargs.get("kb_id", ""),
        "department": task_kwargs.get("department", ""),
    }
    upload_id = str(task_kwargs.get("upload_id", ""))

    def _on_conflict() -> dict:
        """同键冲突：分辨「同内容在途（附着）」与「异内容（409 拒绝）」。"""
        from backend.rag.indexing.index_run_store_pg import get_index_run_store
        run = get_index_run_store().get_run(upload_id)
        if (run and run.get("file_hash") == file_hash
                and run.get("kb_id") == task_kwargs.get("kb_id", "")
                and run.get("department") == task_kwargs.get("department", "")):
            return {"queued": False, "attached": True, "upload_id": upload_id,
                    "db_task_id": run.get("celery_task_id", "")}
        raise UploadIdempotencyConflict(
            "同一幂等键已绑定不同内容（或在途任务内容不一致），"
            "请更换 Idempotency-Key 或等待既有任务完成",
            upload_id=upload_id,
        )

    try:
        result = run_idempotent_operation_for_identity(
            "rag.index.submit",
            payload,
            lambda: facade._dispatch_index_to_celery(**task_kwargs),
            tenant_id=tenant_id,
            actor_id=actor_id,
            client_key=idempotency_key,
            # 缓存 Redis 停止时，上传仍通过 PG durable ledger 保证只提交
            # 一次；不允许退化为进程内幂等，主链只损失缓存能力。
            allow_postgres_fallback=True,
        )
    except ValueError as ve:
        # PG 结果仓储以裸 ValueError("IDEMPOTENCY_CONFLICT") 表达同键不同内容
        if "IDEMPOTENCY_CONFLICT" not in str(ve):
            raise
        result = _on_conflict()
    except IdempotencyConflict:
        result = _on_conflict()
    return facade._resolve_replayed_dispatch(result, task_kwargs, file_hash)


def _resolve_replayed_dispatch(result: dict, task_kwargs: dict, file_hash: str) -> dict:
    """幂等重放守卫（联合验收实测缺口）：重放结果可能是「死任务」。

    场景：同指纹（同内容）重传 → 提交层 ledger 直接重放上次派发结果，
    但上次任务可能已终态失败（worker 重试耗尽/解析失败）。此时必须
    重新派发新一轮候选（create_run 重置状态 + 新 generation），否则
    SSE 永远等不到终态。分三种情形：
      - 重放任务的运行记录仍活着 → 原样返回（幂等语义）；
      - 重放任务已终态失败 → 重新派发（新任务）；
      - 重放结果属于【另一条】上传（同内容更早的请求）→ 附着到那条
        任务（调用方以返回的 upload_id 订阅 SSE）；它已失败则重新派发。
    """
    from backend.rag.indexing.index_run_store_pg import get_index_run_store

    result = dict(result or {})
    replayed_id = str(result.get("upload_id", ""))
    current_id = str(task_kwargs.get("upload_id", ""))
    if not replayed_id:
        return result  # 旧格式结果（无归属信息），保持旧行为
    run_store = get_index_run_store()
    run = run_store.get_run(replayed_id)
    if run is None:
        return result  # 运行记录缺失（历史数据）——保持旧行为
    status = str(run.get("status") or "")
    if status not in ("failed", "superseded"):
        if replayed_id != current_id:
            return {"queued": False, "attached": True,
                    "upload_id": replayed_id,
                    "run_status": status,
                    "db_task_id": result.get("db_task_id", "")}
        return result
    logger.warning(
        "[RAG] 幂等重放命中已失败任务(%s)，重新派发新一轮候选: %s",
        status, current_id or replayed_id)
    fresh = facade._dispatch_index_to_celery(**task_kwargs)
    return fresh


async def _run_index_background(upload_id: str, filepath: str, filename: str, source: str = "", batch_id: str | None = None, kb_id: str = "policy_general", department: str = "general", upload_elapsed_ms: int | None = None, was_overwrite: bool = False, file_hash: str = "", tenant_id: str = "", actor_id: str = "", idempotency_key: str = "", staging_path: str = "", generation: str = "", pre_dispatched: dict | None = None, task_kwargs: dict | None = None):
    """后台执行索引，向 queue 推送阶段事件；完成后记录操作日志。

    B 阶段：可信请求的 Celery 派发已在 HTTP 请求路径内完成（pre_dispatched
    非 None），本任务只负责「已入队」进度事件与 SSE 通道路由标记；不可信
    请求（task_kwargs 非 None）沿用派发+回退逻辑。

    was_overwrite: 本次上传是否将覆盖已有同名文件（仅响应语义；真正的
    覆盖发生在发布协议的 os.replace，失败时正式文件不被触碰）。
    """
    queue = facade._progress_queues.get(upload_id)
    if queue is None:
        await facade._cleanup_failed_upload(staging_path or filepath, was_overwrite=was_overwrite)
        return

    async def emit(stage: str, message: str = "", **extra):
        await queue.put({"stage": stage, "message": message, **extra})
        # Redis 写盘是同步网络 IO，放线程池执行，避免 Redis 慢时阻塞事件循环
        await asyncio.to_thread(facade._write_progress_redis, upload_id, stage, message,
                                owner=(tenant_id, actor_id), **extra)

    # 同步发射器：终态收口（_settle_index_result）与 Celery 分流共用。
    # queue.put_nowait 对无界 asyncio.Queue 与 await put 语义一致。
    def emit_fn(stage: str, message: str = "", **extra):
        if queue is not None:
            queue.put_nowait({"stage": stage, "message": message, **extra})
        facade._write_progress_redis(upload_id, stage, message,
                              owner=(tenant_id, actor_id), **extra)

    _upload_t0 = time.time()

    # ── 可信请求：派发已在请求路径完成 ──
    if pre_dispatched is not None:
        # 打标必须在发任何事件之前：SSE 队列模式每轮检查此标记，
        # 看到即切换 Redis 轮询通道消费 Worker 事件（跨进程队列收不到）
        facade._celery_routed.add(upload_id)
        await emit("uploading", f"文件 {filename} 已保存，索引任务已入队（Celery Worker 执行）")
        # 终态由 Worker 写 Redis 进度镜像；本进程队列不再有后续事件
        return

    # ── 不可信请求：派发 + broker 不可达回退本进程索引 ──
    if task_kwargs is None:
        # 旧式直调（无请求路径派发）：从函数参数装配完整任务字段
        task_kwargs = {
            "upload_id": upload_id, "filepath": filepath,
            "filename": filename, "kb_id": kb_id, "department": department,
            "source": source, "batch_id": batch_id,
            "upload_elapsed_ms": upload_elapsed_ms,
            "actor_id": actor_id, "tenant_id": tenant_id,
            "was_overwrite": was_overwrite,
        }
    task_kwargs.setdefault("staging_path", staging_path)
    task_kwargs.setdefault("generation", generation)
    task_kwargs.setdefault("file_hash", file_hash)
    try:
        facade._dispatch_index_with_idempotency(
            task_kwargs=task_kwargs,
            file_hash=file_hash,
            tenant_id=tenant_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )
        facade._celery_routed.add(upload_id)
        await emit("uploading", f"文件 {filename} 已保存，索引任务已入队（Celery Worker 执行）")
        return
    except Exception as enqueue_err:
        if tenant_id and actor_id:
            # 可信请求不能在幂等 Redis/PG 或任务提交失败时静默降级到
            # 进程内执行，否则会绕过“只执行一次”保证。
            facade._settle_index_result(
                upload_id, filepath, filename, source, batch_id, kb_id,
                upload_elapsed_ms, was_overwrite, _upload_t0,
                result=None, emit_fn=emit_fn, exc=enqueue_err,
                staging_path=staging_path, actor_id=actor_id,
                tenant_id=tenant_id)
            await facade._finalize_upload_queue(upload_id)
            return
        # broker 不可达：可用性优先，回退本进程索引（与 task_manager 503 语义对齐）
        logger.warning(f"[RAG] Celery 入队失败，回退进程内索引: {enqueue_err}")
        facade._celery_routed.discard(upload_id)
        await emit("uploading", "索引队列暂不可用，已切换为本机索引")

    result = None
    try:
        await emit("uploading", f"文件 {filename} 已保存，开始索引")

        # 同步索引（在线程池跑，不阻塞事件循环）；返回含 trace_id
        loop = asyncio.get_running_loop()
        # 仅限制 API 进程本地回退执行；Celery 正常路径的并发由
        # RAG_INDEX_WORKER_CONCURRENCY 控制。槽位满时提示排队。
        sem = facade._get_index_semaphore()
        if sem.locked():
            await emit("uploading",
                       f"索引队列繁忙（最大并发 {_INDEX_CONCURRENCY_LIMIT}），排队等待中...")
        async with sem:
            result = await loop.run_in_executor(
                None,
                lambda: facade._do_index_sync(
                    upload_id, filepath, filename, loop, kb_id, department,
                    batch_id=batch_id,
                    staging_path=staging_path, generation=generation,
                    file_hash=file_hash,
                    tenant_id=tenant_id,
                ),
            )
    except FileLockedByOtherError:
        # P0-2 双重投递兜底：Celery 任务实际已入队（broker 响应丢失被误判为
        # 入队失败）+ 本机回退同时执行，本机抢锁失败即此场景。
        # 索引由 Worker 负责 —— 这里绝不能走失败收口（旧实现会
        # _cleanup_failed_upload_sync 删掉 Worker 正在索引的源文件，导致
        # Worker 中途断源）。改为打标切换 SSE 到 Redis 轮询通道，消费
        # Worker 的终态事件；若 Worker 任务实际不存在（极罕见的跨上传
        # 锁冲突），SSE 空转后由轮询超时兜底报错，源文件保持原样。
        logger.warning(
            f"[RAG] {filename} 文件锁被占（Celery 任务大概率已在执行），本机回退退出")
        facade._celery_routed.add(upload_id)
        await emit("uploading", "索引任务已在队列中执行，等待其结果...")
        return
    except Exception as e:
        facade._settle_index_result(
            upload_id, filepath, filename, source, batch_id, kb_id,
            upload_elapsed_ms, was_overwrite, _upload_t0,
            result=None, emit_fn=emit_fn, exc=e,
            staging_path=staging_path, actor_id=actor_id,
            tenant_id=tenant_id)
        await facade._finalize_upload_queue(upload_id)
        return

    facade._settle_index_result(
        upload_id, filepath, filename, source, batch_id, kb_id,
        upload_elapsed_ms, was_overwrite, _upload_t0,
        result=result, emit_fn=emit_fn, actor_id=actor_id,
        tenant_id=tenant_id)
    await facade._finalize_upload_queue(upload_id)


def _reverify_publish_authorization(
    *, actor_id: str, tenant_id: str, kb_id: str, department: str,
) -> None:
    """发布前按运行记录 actor/tenant 复验当前授权（#65，fail-closed）。

    入队时的授权只在 HTTP 时点有效；任务可能因排队/重试/崩溃重投在数十分钟
    后才执行。此处按 actor 当前权威行重建授权（与图/Tool 通道同一套
    resolve_task_authorization → RagAuthorization），失权即拒绝发布，由调用方
    按 superseded 语义清理候选。

    部门口径：HTTP 通道的 principal.department 来自请求头（网关注入），与
    auth.users.dept 不保证一致；发布目标部门在入队时已裁决并固化进运行记录，
    因此复验时用记录部门重建授权面（当前权威行仍提供状态/租户/角色），部门
    是否仍启用由 validate_kb_dept 的 active_departments 兜底。

    跳过条件：运行记录没有可复验的用户身份（空/占位 actor，或未声明租户）——
    这些不是已认证 HTTP 上传，保持既有行为。
    """
    actor = str(actor_id or "").strip()
    tid = str(tenant_id or "").strip()
    if not actor or not tid or actor in {"system", "default", "anonymous"}:
        return
    from backend.rag.authz import RagAuthorization, RagAuthorizationError
    from backend.rag.indexing.publish import PublishAuthorizationRevoked
    from backend.security.task_authorization import (
        TaskAuthorizationDenied,
        resolve_task_authorization,
    )
    from dataclasses import replace

    try:
        ctx = resolve_task_authorization(actor, tid)
        authz = RagAuthorization.build(
            replace(ctx.principal, department=str(department or "")))
    except (TaskAuthorizationDenied, RagAuthorizationError) as e:
        raise PublishAuthorizationRevoked(
            f"索引发布前授权复验失败（{actor}@{tid}）: {e}") from e
    ok, reason = authz.can_upload_to(kb_id, department)
    if not ok:
        raise PublishAuthorizationRevoked(
            f"索引发布前授权已失效（{actor}@{tid} → {kb_id}/{department}）: {reason}")


def _latest_processing_trace_for_doc(doc_id: str) -> tuple[str, str]:
    """取文档最近一次成功处理的 Trace 和运行号，查询失败时留空。"""
    if not doc_id:
        return "", ""
    try:
        from backend.rag.indexing.processing_lineage_pg import (
            get_processing_lineage_repository,
        )

        latest = get_processing_lineage_repository().get_latest_for_doc(doc_id)
    except Exception as exc:  # noqa: BLE001 — Trace 查询失败不阻断重复上传收尾
        logger.warning(f"[RAG] 获取文档历史 Trace 失败 ({doc_id}): {exc}")
        return "", ""
    if not latest:
        return "", ""
    return str(latest.get("trace_id") or ""), str(latest.get("run_id") or "")


def _active_document_artifacts_complete(
    doc: dict, filepath: str, expected_hash: str,
) -> bool:
    """只有原件、chunk_store 和登记向量齐全，才允许把同哈希上传当作重复件。"""
    doc_id = str(doc.get("doc_id") or "")
    try:
        expected_count = int(doc.get("chunk_count") or 0)
    except (TypeError, ValueError):
        return False
    if not doc_id or expected_count <= 0 or not os.path.isfile(filepath):
        return False
    try:
        if facade.sha256_of_file(filepath) != expected_hash:
            return False
        from backend.rag.indexing.chunk_store import get_chunk_store

        chunks = get_chunk_store().get_by_doc_id(doc_id)
        raw_vector_ids = doc.get("chunk_ids") or []
        vector_ids = (
            json.loads(raw_vector_ids)
            if isinstance(raw_vector_ids, str) else raw_vector_ids
        )
        expected_vector_ids = {
            str(value) for value in vector_ids or [] if str(value or "").strip()
        }
        if not expected_vector_ids:
            return False
        active_vector_ids = facade._active_vector_ids_for_doc(
            doc_id, sorted(expected_vector_ids),
        )
    except Exception as exc:  # noqa: BLE001 — 无法核对时不误判为完整
        raise RuntimeError(f"核对已入库文档完整性失败 ({doc_id})") from exc
    return (
        len(chunks) == expected_count
        and expected_vector_ids.issubset(active_vector_ids)
    )


def _active_vector_ids_for_doc(doc_id: str,
                               expected_ids: list[str]) -> set[str]:
    """只读核对主 pgvector collection 中登记的 chunk ID。"""
    if not doc_id or not expected_ids:
        return set()
    import psycopg2
    from psycopg2 import sql

    from backend.config.database import (
        CHROMA_PATH,
        VECTOR_PG_CONFIG,
        VECTOR_PG_TABLE_PREFIX,
    )

    table = sql.Identifier(f"{VECTOR_PG_TABLE_PREFIX}rag_vectors")
    collection = os.path.basename(os.path.normpath(str(CHROMA_PATH))) or "default"
    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT id FROM {} WHERE collection = %s AND doc_id = %s "
                    "AND id = ANY(%s)"
                ).format(table),
                (collection, doc_id, expected_ids),
            )
            return {str(row[0]) for row in cursor.fetchall()}
    finally:
        conn.rollback()
        conn.close()


def _do_index_sync(upload_id: str, filepath: str, filename: str,
                   main_loop: asyncio.AbstractEventLoop | None,
                   kb_id: str = "policy_general",
                   department: str = "general",
                   batch_id: str | None = None,
                   staging_path: str = "",
                   generation: str = "",
                   file_hash: str = "",
                   tenant_id: str = "",
                   ocr_page_budget: int | None = None):
    """候选模式索引（B 阶段）：解析→向量→BM25 全部落在 generation 隔离层，
    发布协议统一提交；任何阶段失败旧版保持可读可检索。

    关键语义：
      - Worker 只读不可变暂存文件（staging_path），正式路径在发布前不被触碰；
      - registry 在候选期只读（不再有 parsing 占位原地降级）；
      - 发布拿索引锁串行化——锁只保护发布段，两个并发候选可并行解析/向量，
        由发布 CAS 裁决唯一发布者，输者明确 superseded；
      - 终态幂等：published 运行重入直接返回 done（不重复发布）。

    main_loop 传 None：Worker 进程无 asyncio 主循环，sync_emit 只写 Redis。
    """
    from backend.config import DOCS_DIRECTORY

    queue = facade._progress_queues.get(upload_id)

    _upload_t0_sync = time.time()

    def sync_emit(stage: str, message: str = "", **extra):
        """从同步线程调用：进程内队列事件投主 loop（仅 API 回退路径有）"""
        facade._write_progress_redis(upload_id, stage, message, **extra)
        if queue is None or main_loop is None:
            return  # Worker / 无订阅者 → 只写 Redis 镜像
        evt = {"stage": stage, "message": message, **extra}
        asyncio.run_coroutine_threadsafe(queue.put(evt), main_loop)

    reg = facade._get_registry()
    from backend.rag.indexing.index_run_store_pg import (
        RunStateConflict,
        get_index_run_store,
    )
    run_store = get_index_run_store()
    run = run_store.get_run(upload_id)
    tenant_id = str(tenant_id or (run or {}).get("tenant_id") or "default")
    if run is None:
        # 旧消息/回退路径现场登记（base 取当前 registry 指针）
        generation = generation or uuid.uuid4().hex
        existing0 = reg.get_by_path(filepath)
        run = run_store.create_run(
            upload_id=upload_id, generation=generation, file_path=filepath,
            kb_id=kb_id, department=department,
            file_hash=file_hash, staging_path=staging_path,
            base_generation=str((existing0 or {}).get("active_generation") or ""),
            base_status=str((existing0 or {}).get("status") or ""),
        )
    generation = run["generation"]
    staging = run.get("staging_path") or staging_path or filepath
    source_hash = file_hash or run.get("file_hash") or ""

    # ── 终态幂等短路（重试不得重复发布）──
    if run["status"] == "published":
        doc = reg.get_by_path(filepath) or {}
        trace_id, processing_run_id = facade._latest_processing_trace_for_doc(
            str(doc.get("doc_id") or ""))
        logger.info(f"[RAG] 运行已发布，幂等返回 done: {upload_id}")
        return {
            "trace_id": trace_id, "processing_run_id": processing_run_id,
            "terminal": "done",
            "doc": {**doc, "duplicate": False},
            "chunk_count": int(doc.get("chunk_count") or 0),
            "file_hash": doc.get("file_hash", source_hash),
            "stage_elapsed": {},
        }
    if run["status"] in ("failed", "superseded"):
        # 失败终态被重投（sweeper 唤醒）：不复活失败任务；正在的新一轮
        # 上传会经 create_run 重置状态走正常流程
        raise RuntimeError(
            f"索引运行已终态({run['status']})，拒绝重跑: {run.get('error', '')[:200]}")

    if not os.path.isfile(staging):
        raise RuntimeError(f"暂存源文件缺失（可能已被清理）: {os.path.basename(staging)}")

    # 哈希校验：上传源哈希必须等于 Worker 处理的哈希（B 阶段验收项）
    actual_hash = facade.sha256_of_file(staging)
    if source_hash and actual_hash != source_hash:
        raise RuntimeError(
            "暂存文件哈希与任务声明不一致（源文件被篡改或写坏），拒绝索引")

    sync_emit("uploading", "正在校验文档版本...", file_hash=actual_hash[:12])

    # ── duplicate 检测（与 active 版本同内容 → 无需发布）──
    existing = reg.get_by_path(filepath)
    try:
        _raw_existing_ids = (existing or {}).get("chunk_ids") or "[]"
        _parsed_existing_ids = (
            json.loads(_raw_existing_ids)
            if isinstance(_raw_existing_ids, str) else _raw_existing_ids
        )
        old_chunk_ids_for_bm25 = [
            str(item) for item in (_parsed_existing_ids or [])
        ]
    except (ValueError, TypeError):
        old_chunk_ids_for_bm25 = []
    same_active_version = (
        existing
        and existing.get("status") == "active"
        and existing.get("file_hash") == actual_hash
    )
    artifacts_complete = (
        facade._active_document_artifacts_complete(existing, filepath, actual_hash)
        if same_active_version else False
    )
    if (same_active_version and artifacts_complete
            and run.get("status") != "publishing"):
        logger.info(f"[RAG] 文件未变化，跳过索引: {filename}")
        run_store.mark_status(upload_id, "published", stage="duplicate")
        trace_id, processing_run_id = facade._latest_processing_trace_for_doc(
            str(existing.get("doc_id") or ""))
        return {
            "trace_id": trace_id,
            "processing_run_id": processing_run_id,
            "trace_association": "previous_document_processing",
            "terminal": "duplicate",
            "doc": {**existing, "duplicate": True},
            "file_hash": actual_hash,
            "stage_elapsed": {"uploading": int((time.time() - _upload_t0_sync) * 1000)},
        }

    # ── 同逻辑文档互斥：已有其他运行在途 → 明确拒绝（排队语义交上层重试）──
    others = [r for r in run_store.list_non_terminal_by_file(filepath)
              if r.get("upload_id") != upload_id]
    if others:
        raise FileLockedByOtherError(
            f"文档 {filename} 已有另一个上传任务在处理（{others[0].get('upload_id')}），请稍后重试")

    sync_emit("uploading", "正在初始化索引管道（首次 ~15s）...")
    _pipe_t0 = time.time()
    pipeline = facade.get_rag_pipeline()
    _pipe_elapsed = int((time.time() - _pipe_t0) * 1000)
    if _pipe_elapsed > 3000:
        logger.info(f"[RAG] 管道初始化耗时 {_pipe_elapsed}ms（可能预热未完成）")

    # ── 候选期（无锁）：解析/向量/BM25 全在 generation 隔离层 ──
    from backend.rag.indexing.publish import (
        CandidatePublishError,
        SupersededCandidate,
        cleanup_candidate,
        make_candidate_stores,
    )
    stores = make_candidate_stores(pipeline, generation)
    base_generation = str(run.get("base_generation") or "")
    run_store.mark_status(upload_id, "indexing", stage="index")
    listener = ProgressListener(sync_emit)
    from backend.rag.indexing.indexer import IncrementalIndexer
    from backend.rag.indexing.processing_lineage_pg import (
        get_processing_lineage_repository,
    )
    indexer = IncrementalIndexer(
        docs_dir=DOCS_DIRECTORY,
        vectordb=stores.vectordb,
        doc_db=stores.doc_db,
        embedding=pipeline.embedding,
        registry=reg,
        kb_id=kb_id,
        department=department,
        bm25_store=stores.bm25_store,
        chunk_store=stores.chunk_store,
        candidate_mode=True,
        bm25_source_vectordb=stores.bm25_source,
        processing_lineage_repository=get_processing_lineage_repository(),
        processing_task_id=upload_id,
        processing_batch_id=batch_id,
        tenant_id=tenant_id,
        **({"ocr_page_budget": ocr_page_budget}
           if ocr_page_budget is not None else {}),
    )
    try:
        index_result = indexer.index_candidate(
            staging, filepath, file_hash=actual_hash,
            old_chunk_ids=old_chunk_ids_for_bm25)
    except SupersededCandidate:
        raise
    except Exception:
        # 候选期失败：候选层整体清理后原样上抛（终态收口统一发 error、
        # 标 failed、删暂存；正式数据未被触碰）
        cleanup_candidate(stores)
        raise
    finally:
        listener.unsub()
    index_result["staging_path"] = staging

    # ── 发布段（索引锁串行化；锁只保护发布段，候选期不占锁）──
    sync_emit("embedding", "候选索引完成，正在发布...")
    lock_fd = facade.acquire_index_lock(filepath)
    try:
        # 心跳启动夹在锁获取与 try 之间曾留下窗口：启动失败则 fd 不释放、
        # .lock 文件残留至 stale TTL，期间该文件所有发布被拒
        _heartbeat_stop = threading.Event()
        facade._start_lock_heartbeat(filepath + ".lock", _heartbeat_stop)
        old_row = reg.get_by_path(filepath)
        try:
            _raw_old = (old_row or {}).get("chunk_ids") or "[]"
            _parsed_old = json.loads(_raw_old) if isinstance(_raw_old, str) else _raw_old
            old_chunk_ids = [str(x) for x in (_parsed_old or [])]
        except (ValueError, TypeError):
            old_chunk_ids = []
        from backend.rag.indexing.publish import publish_candidate

        # #65：发布前复验 actor 当前授权（排队/重试期间被撤销即拒绝发布）
        facade._reverify_publish_authorization(
            actor_id=str(run.get("actor_id") or ""),
            tenant_id=str(run.get("tenant_id") or ""),
            kb_id=kb_id, department=department,
        )
        publish_candidate(
            run_store=run_store,
            upload_id=upload_id,
            registry=reg,
            stores=stores,
            final_path=filepath,
            doc_id=index_result["doc_id"],
            kb_id=kb_id,
            file_hash=actual_hash,
            base_generation=base_generation,
            base_status=str(run.get("base_status") or ""),
            index_result=index_result,
            old_row=old_row,
            old_chunk_ids=old_chunk_ids,
            old_doc_db_id=str((old_row or {}).get("doc_db_id") or ""),
            main_bm25_store=pipeline.bm25_store,
        )
    except SupersededCandidate as s:
        # 单发布者裁决：更新的候选已发布。清理本次候选 + 暂存，明确报
        # superseded（旧版本保持可读可检索）
        cleanup_candidate(stores)
        try:
            run_store.mark_status(upload_id, "superseded", error=str(s)[:500])
        except RunStateConflict:
            pass
        if staging != filepath and os.path.isfile(staging):
            try:
                os.unlink(staging)
            except OSError:
                pass
        logger.warning(f"[RAG] 候选被取代: {filename}: {s}")
        raise
    except CandidatePublishError as e:
        # 提交点之后的失败：发布已提交（registry 已指向本代次），候选已晋级。
        # 保持 publishing 状态等待重试续跑（publish_candidate 幂等），不清理
        # 任何产物、不删暂存（续跑还要用它），不标记 failed。
        logger.error(f"[RAG] 发布段失败（保持 publishing 待续跑）: {e}")
        try:
            run_store.mark_status(
                upload_id, "publishing", stage="publish", error=str(e)[:500],
            )
        except Exception as status_error:  # noqa: BLE001 — 原发布错误优先
            logger.warning(
                f"[RAG] 发布失败原因写入运行记录失败 ({upload_id}): "
                f"{status_error}")
        raise
    finally:
        _heartbeat_stop.set()
        facade.release_index_lock(lock_fd, filepath)

    # 上传后刷新本进程内存检索对象（跨进程刷新由 C 阶段热刷新承担）
    try:
        pipeline.refresh_bm25_from_store()
    except Exception as e:
        logger.warning(f"[RAG] BM25 刷新失败（不影响索引结果）: {e}")
    # 发布通知（C 阶段）：pub/sub 推给在线 API/rag-service 立即刷新；
    # epoch 键留给错过消息的进程轮询兜底。通知失败不影响发布结果。
    try:
        from backend.infra.redis.client import get_redis
        from backend.config.redis import REDIS_KEY_PREFIX
        r = get_redis()
        if r is not None:
            r.publish(f"{REDIS_KEY_PREFIX}rag:index:published", generation)
            r.set(f"{REDIS_KEY_PREFIX}rag:index:generation", generation, ex=86400)
    except Exception as notify_err:  # noqa: BLE001 — 通知是加速项不是正确性依赖
        logger.debug(f"[RAG] 索引发布通知失败（版本检查兜底）: {notify_err}")

    logger.info(f"[RAG] 上传索引完成: {filename} → {index_result.get('doc_id')}")
    return {
        "trace_id": index_result.get("trace_id", ""),
        "chunk_count": index_result.get("chunk_count", 0),
        "file_hash": actual_hash,
        "doc_id": index_result.get("doc_id", ""),
        "processing_run_id": index_result.get("processing_run_id", ""),
        "stage_elapsed": index_result.get("stage_elapsed", {}),
    }


async def sync_upload_impl(
    file, request, max_size, tmp_dir, chunk_size, emit_bytes, emit_ms,
    kb_id: str = "policy_general", department: str = "general",
    tenant_id: str = "", actor_id: str = "", idempotency_key: str = "",
) -> dict:
    """P0-1 流式上传 (async def, 直接在 upload_document 事件循环里跑).
    file.read() 是 async method, 必须 await. 写文件是 sync (open + write).
    """
    doc_dir_override = getattr(facade, "_DOCS_DIRECTORY", None)
    if doc_dir_override:
        _DOCS_DIRECTORY = doc_dir_override
    else:
        from backend.config.database import DOCS_DIRECTORY as _DOCS_DIRECTORY

    # facade._progress_queues / _extract_source 现为模块级显式导入，直接引用即可
    # （原先经 globals() 取值是为了绕开 import * 的名字丢失问题）

    raw_filename = str(file.filename or "")
    if RAG_UPLOAD_PATH_GUARD:
        from backend.rag.indexing.upload_path_guard import test_artifact_path_reason

        reason = test_artifact_path_reason(raw_filename)
        if reason:
            from backend.security.events import record_security_event

            record_security_event(
                "INPUT_GUARD_BLOCK", category="TEST_ARTIFACT_REJECTED",
                detail={"path_reason": reason},
            )
            return {"ok": False, "status_code": 422,
                    "error": "测试临时路径文件被拒绝"}
    safe_name = os.path.basename(raw_filename)
    # 修复中文文件名乱码：尝试多种编码回编解码
    if safe_name:
        for enc in ('latin-1', 'cp1252', 'iso-8859-1'):
            try:
                raw = safe_name.encode(enc)
                candidate = raw.decode('utf-8')
                # 成功解码且包含中文字符 → 采纳
                if any('一' <= c <= '鿿' for c in candidate):
                    safe_name = candidate
                    break
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
    if not safe_name or safe_name.startswith("."):
        return {"ok": False, "error": "文件名无效"}
    # P1 fix: 用 realpath 解析 backend/data junction 符号链接
    # 避免同一物理文件被存为两条不同 file_path 记录（SQLite 主键冲突 → 重复 doc_id）
    abs_docs_dir = os.path.realpath(_DOCS_DIRECTORY)
    tenant_component = ""
    if tenant_id and tenant_id != "default":
        import hashlib
        tenant_component = "tenant_" + hashlib.sha256(
            tenant_id.encode("utf-8")
        ).hexdigest()[:24]
    final_dir = os.path.abspath(os.path.join(
        abs_docs_dir, kb_id, department, tenant_component,
    ))
    final_path = os.path.normpath(os.path.join(final_dir, safe_name))
    # 末尾再 realpath 一次（防御 abspath 残留符号链接组件）
    final_path = os.path.realpath(final_path)
    try:
        if os.path.commonpath([abs_docs_dir, final_path]) != abs_docs_dir:
            return {"ok": False, "error": "文件保存路径无效"}
    except ValueError:
        return {"ok": False, "error": "文件保存路径无效"}

    # F9: was_overwrite 检测移到 os.replace 前一刻（原在函数入口，与 replace
    # 隔了整段上传+校验流程，TOCTOU 窗口内并发同名上传可基于陈旧状态决策
    # cleanup 策略）。贴近 replace 后窗口缩到备份+replace 两条语句。

    ext = safe_name.rsplit(".", 1)[-1].lower()
    # upload_id 语义（B 阶段收口）：任务标识，不再是可复用的写入路径。
    #   - 显式幂等键 → 确定性 id（同键同内容重试 = 同一任务与进度）；
    #   - 无键请求一律随机新任务（同名不同内容互不混写）。
    if tenant_id and actor_id and idempotency_key:
        import hashlib
        upload_id = hashlib.sha256(
            f"{tenant_id}:{actor_id}:{idempotency_key}".encode("utf-8")
        ).hexdigest()
    else:
        upload_id = uuid.uuid4().hex
    # generation：本次候选代次（向量候选 collection / BM25 staging 目录共用）
    generation = uuid.uuid4().hex
    # 暂存文件：独立随机命名、不可变——Worker 只读它，索引成功才由发布
    # 协议 os.replace 到正式路径（旧实现在上传 API 进程、拿锁之前就覆盖
    # 正式文件，是并发覆盖竞态的根源，已废除）。
    staging_dir = os.path.join(tmp_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)
    staging_path = os.path.abspath(os.path.join(staging_dir, f"{upload_id}.{generation}.{ext}"))

    # 真实 HTTP 上传耗时（POST body 接收 + 写暂存文件）
    _upload_t0_sync = time.time()

    # F11: 过期队列清理抽为独立函数（定时 GC 复用同一逻辑）
    facade.cleanup_expired_progress_queues()

    queue: Queue = Queue()
    queue._created_at = time.time()
    facade._progress_queues[upload_id] = queue
    # SSE 订阅归属绑定（2026-10-01 权限收口）：只有上传者本人/admin 可订阅
    facade._progress_owners[upload_id] = (tenant_id, actor_id)

    # 进度推送: 在 async context 直接 queue.put_nowait (因为是 asyncio.Queue, 跨 coroutine 同一 loop OK)
    def _safe_put(evt):
        queue.put_nowait(evt)

    try:
        import hashlib as _hashlib
        total = 0
        last_emit_bytes = 0
        last_emit_time = time.time()
        cl_str = request.headers.get("content-length", "0")
        cl_int = int(cl_str) if cl_str.isdigit() else None
        hasher = _hashlib.sha256()
        magic_prefix = bytearray()
        contains_nul = False

        with open(staging_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    # F1 修复:超限拒绝必须清理临时文件。
                    f.close()
                    try:
                        os.unlink(staging_path)
                    except OSError as cleanup_error:
                        logger.warning(f"[RAG] 超限上传暂存文件清理失败: {cleanup_error}")
                    return {"ok": False, "error": f"文件超过 {max_size // 1024 // 1024} MB 上限"}
                f.write(chunk)
                if len(magic_prefix) < _MAGIC_PREFIX_BYTES:
                    magic_prefix.extend(
                        chunk[:_MAGIC_PREFIX_BYTES - len(magic_prefix)]
                    )
                contains_nul = contains_nul or b"\x00" in chunk
                # 流式计算 SHA256（B 阶段）：哈希与字节同源，Worker 校验
                # 「上传源哈希 = 处理哈希」不再依赖二次读盘
                hasher.update(chunk)

                now_emit = time.time()
                if total - last_emit_bytes >= emit_bytes or (now_emit - last_emit_time) * 1000 >= emit_ms:
                    progress = int(total * 100 / max(cl_int or total, 1))
                    _safe_put({
                        "stage": "uploading",
                        "progress": min(progress, 99),
                        "bytes": total,
                    })
                    last_emit_bytes = total
                    last_emit_time = now_emit

        file_hash = hasher.hexdigest()
        # P1-8: 入口校验 — 空文件与损坏文件（魔数）直接拒绝，避免后台索引阶段才失败
        if total == 0:
            try:
                os.unlink(staging_path)
            except OSError:
                pass
            return {"ok": False, "error": "文件内容为空"}
        _magic_ok = facade._magic_matches(
            ext, bytes(magic_prefix), contains_nul=contains_nul,
        )
        if not _magic_ok:
            try:
                os.unlink(staging_path)
            except OSError:
                pass
            return {"ok": False, "error": f"文件内容与 .{ext} 格式不符或已损坏"}
        # 扫描件预检：PDF 无文本层时，OCR 兜底已启用（RAG_OCR_PROVIDER != off）
        # 则放行交给索引链路走 OCR；OCR 关闭时索引必然产出 0 chunk，
        # 入口直接明确拒绝，不让用户等后台索引几十秒才报错（预检失败不误杀）
        if ext == "pdf":
            from backend.config.rag import RAG_OCR_PROVIDER
            from backend.rag.preprocessing.parser.pdf_parser import check_pdf_budget
            # fitz 逐页计数/文本提取是秒级 CPU+IO 块，必须挪出事件循环
            # （2026-11-10 评审：同步执行期间所有并发请求被卡住）
            budget_error = await asyncio.to_thread(check_pdf_budget, staging_path)
            if budget_error:
                try:
                    os.unlink(staging_path)
                except OSError:
                    pass
                return {"ok": False, "error": budget_error}
            _precheck_no_text = None
            if RAG_OCR_PROVIDER == "off":
                # OCR 开启时无需在上传阶段额外扫文本层；关闭 OCR 时检查整个
                # 已通过资源预算的 PDF，避免文本层只在采样窗口之外而被误拒。
                _precheck_no_text = await asyncio.to_thread(
                    facade._pdf_has_text_layer, staging_path, RAG_PDF_MAX_PAGES,
                )
            if _precheck_no_text is False:
                try:
                    os.unlink(staging_path)
                except OSError:
                    pass
                return {"ok": False, "error": (
                    "PDF 无文本层（可能为扫描件/纯图片），且 OCR 兜底未开启，无法解析；"
                    "请使用可复制文字的 PDF，或配置 RAG_OCR_PROVIDER 开启 OCR 支持"
                )}
        # B 阶段：源文件不再在此覆盖正式路径。was_overwrite 仅作响应语义
        # （提示这是一次覆盖上传），真正覆盖发生在发布协议的 os.replace。
        was_overwrite = os.path.isfile(final_path)
        _safe_put({"stage": "uploading", "progress": 100, "bytes": total})

        return {
            "ok": True,
            "upload_id": upload_id,
            "filepath": final_path,          # 逻辑正式路径（registry/幂等键）
            "staging_path": staging_path,    # 不可变暂存文件（Worker 只读它）
            "generation": generation,
            "filename": safe_name,
            "size": total,
            "source": facade._extract_source(request),
            "batch_id": request.headers.get("X-Batch-Id") or None,
            "upload_elapsed_ms": int((time.time() - _upload_t0_sync) * 1000),
            "was_overwrite": was_overwrite,
            "file_hash": file_hash,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
        }
    except Exception:
        # 异常串可能携带服务器绝对路径（OSError 的 str 含完整路径），
        # 全文只进日志，客户端返通用文案（对齐 acquire_index_lock 脱敏口径）
        logger.exception(f"[RAG] 上传流式处理异常 (upload={upload_id})")
        if os.path.exists(staging_path):
            try:
                os.unlink(staging_path)
            except OSError as cleanup_error:
                logger.warning(f"[RAG] 暂存文件清理失败: {cleanup_error}")
        return {"ok": False, "error": "上传处理失败，请稍后重试"}


for _fn in [
    _mark_registry_failed,
    _get_index_semaphore,
    _latest_upload_trace_id,
    _settle_index_result,
    _dispatch_index_to_celery,
    _dispatch_index_with_idempotency,
    _resolve_replayed_dispatch,
    _run_index_background,
    _reverify_publish_authorization,
    _latest_processing_trace_for_doc,
    _active_document_artifacts_complete,
    _active_vector_ids_for_doc,
    _do_index_sync,
    sync_upload_impl
]:
    facade.register(_fn.__name__, _fn)
