"""RAG Upload 外部 HTTP API 路由端点集合。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from asyncio import Queue

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from backend.app.api.deps import (
    get_rag_pipeline,
    require_rag_editor,
    require_rag_ready,
    require_rag_user,
)
from backend.app.api.identity import (
    require_principal,
    resolve_identity,
    resolve_principal,
)
from backend.app.api.routes.rag_upload_subroutes.base import *
from backend.rag.authz import RagAuthorization
from backend.rag.preprocessing.parser import PARSABLE_EXTS

router = APIRouter(prefix="", tags=["RAG文档上传与索引"])

@router.post("/upload", dependencies=[Depends(require_rag_editor)])
async def upload_document(request: Request, file: UploadFile = File(...),
                          kb_id: str = Form(...),
                          department: str = Form(...),
                          ocr_page_budget: int | None = Form(None)):
    """P0-1 流式上传: 临时文件 + atomic rename + 双保险大小限制 + SSE 进度"""
    facade.require_rag_ready()
    # 归属裁决（2026-10-01 权限收口）：department 只认主体自身部门（admin 可
    # 代传任意合法部门），KB 必须 ∈ 主体可见集；请求体自报部门不再单独放行。
    principal = resolve_principal(request)
    try:
        authz = await asyncio.to_thread(facade.RagAuthorization.build, principal)
    except facade.RagAuthorizationError as e:
        logger.warning(f"[RAG] 上传授权失败: {e}")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"ok": False, "error": "授权服务暂不可用，已拒绝上传"})
    up_ok, up_reason = authz.can_upload_to(kb_id, department)
    if not up_ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"ok": False, "error": up_reason})
    identity = resolve_identity(request)
    from backend.config.rag import (
        RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE,
        RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
    )

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    facade._validate_ocr_page_budget(
        is_admin=authz.is_admin, ext=ext, budget=ocr_page_budget,
    )
    ok, err = facade._validate_mime(ext, file.content_type)
    if not ok:
        return {"ok": False, "error": err}

    # batch3 P3-2：X-Batch-Id 后端白名单校验——该头经 BFF 白名单透传后直接
    # 进 Celery 消息、doc_operation_log.batch_id 与 processing lineage，任意
    # 超长/特殊字符会被持久化。前端 randomId 产物（uuid / batch-<ts>-<rand>）
    # 均匹配此模式，合法链路不受影响；校验与 MIME 同口径（200 + ok:false）。
    batch_id_header = (request.headers.get("X-Batch-Id") or "").strip()
    if batch_id_header and not _BATCH_ID_PATTERN.fullmatch(batch_id_header):
        return {"ok": False, "error": "批次标识格式不正确"}

    try:
        # sync_upload_impl 现在是 async def, 不需要 threadpool 包装
        # (file.read() 是 async, 必须在 async context 调, 同步 I/O 也用 sync open/write)
        result = await facade.sync_upload_impl(
            file, request, RAG_MAX_FILE_SIZE * 1024 * 1024,
            RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE, RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
            kb_id=kb_id, department=department,
            tenant_id=identity.tenant_id,
            actor_id=identity.user_id,
            idempotency_key=(request.headers.get("Idempotency-Key") or "").strip(),
        )
    except Exception:
        # 2026-11-10 评审：异常串可能携带服务器绝对路径/连接串（OSError、
        # Redis 客户端错误），只进服务端日志，客户端返通用文案——对齐
        # acquire_index_lock 的脱敏口径与 error_protocol 的安全封套语义
        logger.exception("[RAG] 上传处理异常")
        return {"ok": False, "error": "上传处理失败，请稍后重试"}
    if not result.get("ok"):
        if result.get("status_code") == 422:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=422, content=result)
        return result

    # ── 可信请求在请求路径内派发（B 阶段）：幂等冲突/提交失败必须在 HTTP
    # 响应前暴露（409/错误响应），不能等 background task 静默吞掉。
    task_kwargs = {
        "upload_id": result["upload_id"], "filepath": result["filepath"],
        "staging_path": result.get("staging_path", ""),
        "generation": result.get("generation", ""),
        "file_hash": result.get("file_hash", ""),
        "filename": result["filename"], "kb_id": kb_id,
        "department": department, "source": result["source"],
        "batch_id": result["batch_id"],
        "upload_elapsed_ms": result.get("upload_elapsed_ms"),
        # Phase1 Step8：仅作 tasks 行归属（_dispatch_index_to_celery
        # 内 pop 掉，不进 Celery 消息）
        "actor_id": identity.user_id, "tenant_id": identity.tenant_id,
        "was_overwrite": result.get("was_overwrite", False),
    }
    if ocr_page_budget is not None:
        task_kwargs["ocr_page_budget"] = ocr_page_budget
    pre_dispatched = None
    if identity.tenant_id and identity.user_id:
        try:
            pre_dispatched = await asyncio.to_thread(
                facade._dispatch_index_with_idempotency,
                task_kwargs=task_kwargs,
                file_hash=result.get("file_hash", ""),
                tenant_id=identity.tenant_id,
                actor_id=identity.user_id,
                idempotency_key=result.get("idempotency_key", ""),
            )
        except UploadIdempotencyConflict as conflict:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=409,
                content={"ok": False, "conflict": True,
                         "error": str(conflict),
                         "upload_id": conflict.upload_id},
            )
        except Exception as dispatch_err:
            # 可信请求不能在幂等 Redis/PG 或任务提交失败时静默降级到
            # 进程内执行（会绕过「只执行一次」保证）——显式失败，
            # 暂存文件保留供重试（同键同内容重试会复用幂等语义）。
            # 异常串可能含 Redis/PG 连接串，全文只进日志。
            logger.error(f"[RAG] 索引任务提交失败: {dispatch_err}", exc_info=True)
            return {"ok": False, "error": "索引任务提交失败，请稍后重试"}

    # 附着语义：同内容更早的任务在途 → 客户端应订阅【那条】任务的 SSE
    attached_id = ""
    if isinstance(pre_dispatched, dict) and pre_dispatched.get("attached"):
        attached_id = str(pre_dispatched.get("upload_id") or "")
        if str(pre_dispatched.get("run_status")) == "published":
            # 附着到「已发布」任务：内容完全一致 = duplicate 语义。进度镜像
            # 可能已过期（TTL），这里重发一次终态，保证客户端 SSE 拿得到终态
            # （与 Worker 重复投递时 hash-dedup 发 duplicate 同一口径）。
            facade._write_progress_redis(
                attached_id, "duplicate", "文件已存在，未重复索引",
                owner=(identity.tenant_id, identity.user_id),
                doc=facade.sanitize_doc_row(facade._get_registry().get_by_path(
                    result["filepath"]) or {}))
            # 本进程重发终态 → 订阅必须走 Redis 轮询通道（本进程没有该任务
            # 的进程内队列）；打路由标记让 SSE 无缝切到 Redis 通道
            facade._celery_routed.add(attached_id)
            facade._progress_owners[attached_id] = (identity.tenant_id, identity.user_id)
            return {"ok": True, "duplicate_of": attached_id,
                    "upload_id": attached_id, "filename": result["filename"]}

    if not attached_id:
        task = asyncio.create_task(facade._run_index_background(
            result["upload_id"], result["filepath"], result["filename"],
            result["source"], result["batch_id"], kb_id=kb_id, department=department,
            upload_elapsed_ms=result.get("upload_elapsed_ms"),
            was_overwrite=result.get("was_overwrite", False),
            file_hash=result.get("file_hash", ""),
            staging_path=result.get("staging_path", ""),
            generation=result.get("generation", ""),
            tenant_id=identity.tenant_id,
            # actor 直取鉴权上下文（S4/S10）：sync_upload_impl 返回 dict
            # 不含 actor_id，取 result 会恒为空 → 审计 user_id 恒 anonymous
            actor_id=identity.user_id,
            idempotency_key=result.get("idempotency_key", ""),
            pre_dispatched=pre_dispatched,
            task_kwargs=None if pre_dispatched is not None else task_kwargs,
        ))
        # 持有引用：无引用的 fire-and-forget task 可能被 GC，异常也会被静默吞掉
        facade._background_index_tasks.add(task)
        task.add_done_callback(facade._background_index_tasks.discard)
    return {"ok": True, "duplicate_of": attached_id or None,
            "upload_id": attached_id or result["upload_id"],
            "filename": result["filename"]}


@router.get("/upload-failures", dependencies=[Depends(require_rag_editor)])
async def list_upload_failures(request: Request, limit: int = 50):
    """入库异常清单：异步重试队列和文档质量门禁失败分开统计。

    上传受理后索引在 worker 异步执行，失败发生在对话框关闭之后——此端点是
    管理端「入库失败」信号的唯一数据出口。异步队列沿用“失败且没有后续成功
    发布”的口径；另补上没有 rag_index_runs 对应行的质量门禁失败文档，避免
    文档页能看到失败、失败页却漏掉。两类分别计数，重试队列和历史质量失败不混为一类。
    """
    from backend.rag.indexing.index_run_store_pg import get_index_run_store
    from backend.app.api.identity import require_principal

    principal = require_principal(request)
    try:
        authz = RagAuthorization.build(principal)
    except RagAuthorizationError as exc:
        raise HTTPException(status_code=403, detail="授权服务暂不可用，已拒绝操作") from exc

    tenant_id = principal.tenant_id or ""
    department = None if authz.is_admin else (principal.department or "")
    kb_ids = None if authz.is_admin else sorted(
        kb_id
        for kb_id in authz.kb_scope
        if authz.can_upload_to(kb_id, department or "")[0]
    )
    store = get_index_run_store()
    queue_items = await asyncio.to_thread(
        store.list_pending_failures,
        limit=limit,
        tenant_id=tenant_id,
        kb_ids=kb_ids,
        department=department,
    )
    upload_ids = [
        str(item.get("upload_id") or "")
        for item in queue_items
        if item.get("upload_id")
    ]
    candidate_by_upload: dict[str, dict] = {}
    if upload_ids:
        try:
            from backend.rag.indexing.processing_lineage_pg import (
                get_processing_lineage_repository,
            )

            candidate_runs = await asyncio.to_thread(
                get_processing_lineage_repository().list_candidate_runs_by_upload_ids,
                upload_ids,
                tenant_id=tenant_id,
                kb_ids=sorted(authz.kb_scope),
            )
            candidate_by_upload = {
                str(candidate.get("task_id") or ""): candidate
                for candidate in candidate_runs
                if candidate.get("task_id") and authz.can_read_row(candidate)
            }
        except Exception as exc:  # noqa: BLE001 — 血缘查询失败不隐藏失败项
            logger.warning("[RAG] 上传失败列表运行身份查询失败: %s", exc)
    upload_ids = [
        str(item.get("upload_id") or "")
        for item in queue_items
        if item.get("upload_id")
        and not item.get("trace_id")
        and not candidate_by_upload.get(
            str(item.get("upload_id") or ""), {}
        ).get("trace_id")
    ]
    trace_by_upload: dict[str, str] = {}
    if upload_ids:
        try:
            from backend.observability.trace_store import get_trace_store

            trace_store = get_trace_store()
            lookup = getattr(trace_store, "latest_trace_ids_by_upload", None)
            if callable(lookup):
                trace_by_upload = await asyncio.to_thread(
                    lookup, upload_ids, tenant_id,
                )
        except Exception as exc:  # noqa: BLE001 — Trace 查询失败不隐藏失败项
            logger.warning("[RAG] 上传失败列表 Trace 查询失败: %s", exc)
    queue_total = await asyncio.to_thread(
        store.count_pending_failures,
        tenant_id=tenant_id,
        kb_ids=kb_ids,
        department=department,
    )
    registry = facade._get_registry()
    failed_rows = await asyncio.to_thread(registry.list_by_statuses, ["failed"])
    queue_doc_ids = {str(item.get("doc_id") or "") for item in queue_items}
    queue_paths = {
        os.path.normcase(str(item.get("file_path") or ""))
        for item in queue_items if item.get("file_path")
    }
    visible_quality_failures = []
    for row in failed_rows:
        if row.get("tenant_id") != tenant_id or row.get("quality_status") != "failed":
            continue
        can_manage, _ = authz.can_manage_row(row)
        if not can_manage:
            continue
        doc_id = str(row.get("doc_id") or "")
        file_path = str(row.get("file_path") or "")
        if (doc_id and doc_id in queue_doc_ids) or (
            file_path and os.path.normcase(file_path) in queue_paths
        ):
            continue
        visible_quality_failures.append(row)

    doc_ids = [str(row.get("doc_id") or "") for row in visible_quality_failures]
    trace_by_doc: dict[str, str] = {}
    if doc_ids:
        try:
            _last_ops, trace_by_doc = await asyncio.to_thread(
                facade._get_op_logger().get_last_ops_batch,
                doc_ids,
                tenant_id=tenant_id,
            )
        except Exception as exc:  # noqa: BLE001 — Trace 读取失败不隐藏失败文档
            logger.warning("[RAG] 质量失败文档 Trace 查询失败: %s", exc)

    registry_items = []
    for row in visible_quality_failures:
        trace_id = trace_by_doc.get(str(row.get("doc_id") or ""), "")
        registry_items.append({
            "upload_id": f"registry:{row.get('doc_id') or row.get('file_name')}",
            "record_kind": "quality_gate",
            "retryable": False,
            "file_name": row.get("file_name") or os.path.basename(file_path),
            "file_path": row.get("file_path", ""),
            "file_hash": row.get("file_hash") or "",
            "doc_id": row.get("doc_id", ""),
            "kb_id": row.get("kb_id", ""),
            "department": row.get("department", ""),
            "error": row.get("quality_issues") or "质量门禁失败",
            "created_at": row.get("updated_at") or row.get("last_indexed") or "",
            "trace_id": trace_id,
            "trace_status": "available" if trace_id else "missing_historical",
            "processing_run_id": row.get("last_processing_run_id", ""),
        })
    for item in queue_items:
        item.setdefault("record_kind", "async_queue")
        item.setdefault("retryable", True)
        candidate = candidate_by_upload.get(str(item.get("upload_id") or ""), {})
        if candidate:
            for target, source in (
                ("doc_id", "doc_id"),
                ("file_hash", "file_hash"),
                ("processing_run_id", "run_id"),
                ("operation", "operation"),
                ("kb_id", "kb_id"),
                ("department", "department"),
            ):
                if candidate.get(source):
                    item[target] = candidate[source]
            item["publication_status"] = "unpublished"
            item["document_registered"] = False
        item["trace_id"] = str(
            item.get("trace_id")
            or candidate.get("trace_id")
            or trace_by_upload.get(str(item.get("upload_id") or ""))
            or ""
        )
        item["trace_status"] = (
            "available" if item["trace_id"] else "missing_historical"
        )
    items = sorted(
        [*queue_items, *registry_items],
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )[:max(1, min(int(limit), 200))]
    quality_keys = [
        (str(item.get("kb_id") or ""), str(item.get("doc_id") or ""))
        for item in items
        if item.get("record_kind") == "quality_gate"
    ]
    if quality_keys:
        quality_details = await asyncio.to_thread(
            facade._load_quality_failure_details, quality_keys,
        )
        for item in items:
            if item.get("record_kind") == "quality_gate":
                item["failure_details"] = quality_details.get(
                    (
                        str(item.get("kb_id") or ""),
                        str(item.get("doc_id") or ""),
                    ),
                )
    quality_failure_total = len(visible_quality_failures)
    return {
        "ok": True,
        "total": queue_total + quality_failure_total,
        "items": items,
        "counts": {
            "async_queue": queue_total,
            "quality_gate": quality_failure_total,
        },
    }


@router.get("/upload/{upload_id}/stream")
async def stream_upload_progress(upload_id: str, request: Request):
    """SSE 订阅：实时推送上传 + 索引进度（仅上传者本人/admin 可订阅）。

    事件类型：
      stage  → {stage: uploading|parsing|chunking|embedding|writing|done|error, message}
      done   → 包含 doc 信息
      error  → 索引失败

    通道路由（上传级，按 _celery_routed 标记）：
      已入队 Celery → Redis 轮询通道（Worker 在另一进程，进程内队列没有它的事件）；
      未入队 → 进程内队列模式。队列模式消费循环每轮复查标记，
      入队成功后无缝切换 Redis 轮询（携带 last_sig 去重）。
      入队失败回退本机索引时标记不会出现，全程队列模式。
    """
    if not await asyncio.to_thread(facade._ensure_progress_owner, request, upload_id):
        # 统一「不存在或已过期」：不区分「无权」与「不存在」，不泄露存在性
        async def forbidden():
            yield facade._sse_encode("error", {"message": "上传任务不存在或已过期"})
        return StreamingResponse(forbidden(), media_type="text/event-stream")

    if upload_id in facade._celery_routed:
        return facade._redis_poll_stream_response(upload_id)

    queue = facade._progress_queues.get(upload_id)
    if queue is None:
        # 2026-11-10 评审：进程内索引场景 SSE 断连即 pop 队列（防泄漏的既定
        # 设计），重订阅此前直接报"不存在或已过期"——即使索引仍在进行
        # （emit_fn 对队列与 Redis 镜像双写，镜像仍在持续更新）。镜像存在
        # → 切 Redis 轮询通道续看（已终态则立即重放终态）；batch3 P2-2：
        # Redis 连接异常也切轮询通道（通道内自带 120s 异常宽限，恢复后
        # 无缝续看），只有键真不存在（missing）才走拒绝语义。
        mirror_status, _mirror_evt = await asyncio.to_thread(
            facade._read_progress_redis, upload_id)
        if mirror_status != "missing":
            return facade._redis_poll_stream_response(upload_id)

        async def not_found():
            yield facade._sse_encode("error", {"message": "上传任务不存在或已过期"})
        return StreamingResponse(not_found(), media_type="text/event-stream")

    async def event_stream():
        last_sig: str | None = None
        try:
            while True:
                # Celery 入队成功（后台任务与 SSE 并发竞速）→ 切 Redis 轮询
                if upload_id in facade._celery_routed:
                    async for chunk in facade._redis_poll_events(upload_id, last_sig=last_sig):
                        yield chunk
                    return
                try:
                    evt = await asyncio.wait_for(
                        queue.get(), timeout=facade.SSE_KEEPALIVE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    # P2 心跳:大文件索引期间可能数分钟无事件,
                    # 无心跳的 SSE 长连接容易被代理/网关断开
                    yield ": keepalive\n\n"
                    continue
                if evt is None:
                    break
                # 关键：保留 stage 字段在 data 中 — 前端 onmessage 解析 payload.stage
                # _sse_encode 的 event 参数（stage）不再用作 SSE event name（无 event: 字段）
                import json as _json
                last_sig = _json.dumps(evt, sort_keys=True, ensure_ascii=False, default=str)
                yield facade._sse_encode("message", evt)
        finally:
            # SSE 断开 → 清理队列（防内存泄漏）
            facade._progress_queues.pop(upload_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


for _fn in [
    upload_document,
    list_upload_failures,
    stream_upload_progress
]:
    facade.register(_fn.__name__, _fn)
