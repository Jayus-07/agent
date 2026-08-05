"""RAG 上传路由 — PR-2.x 从 rag.py 抽出。"""
import asyncio, os, uuid, time
from asyncio import Queue
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready
from backend.config.rag import RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE, RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.progress_listener import ProgressListener
# 显式导入替代 import *（详见 rag_documents.py 同处注释）
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_registry,
    _progress_queues,
    _safe_log_op,
    _sse_encode,
)
from backend.shared.logger import logger

router = APIRouter()


@router.post("/upload")
async def upload_document(request: Request, file: UploadFile = File(...),
                          kb_id: str = Form("policy_general"),
                          department: str = Form("general")):
    """P0-1 流式上传: 临时文件 + atomic rename + 双保险大小限制 + SSE 进度"""
    require_rag_ready()
    from backend.config.knowledge_base import validate_kb_dept
    if not validate_kb_dept(kb_id, department):
        return {"ok": False, "error": f"知识库 '{kb_id}' 不允许选择部门 '{department}'"}
    from backend.config.rag import (
        RAG_MAX_FILE_SIZE, RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE,
        RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
    )

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "md", "txt", "docx"):
        return {"ok": False, "error": f"ext not allowed: .{ext}"}

    # MIME type 二次校验，防止扩展名伪装（如 .md 文件实际是二进制）
    _ALLOWED_MIME = {
        "pdf":  {"application/pdf"},
        "md":   {"text/markdown", "text/plain", "application/octet-stream"},
        "txt":  {"text/plain", "application/octet-stream"},
        "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/octet-stream"},
    }
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type and content_type not in _ALLOWED_MIME.get(ext, set()):
        return {"ok": False, "error": f"MIME type not allowed for .{ext}: {content_type}"}

    max_size = RAG_MAX_FILE_SIZE * 1024 * 1024
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_size:
        return {"ok": False, "error": f"file too large (max {RAG_MAX_FILE_SIZE}MB)"}

    try:
        # sync_upload_impl 现在是 async def, 不需要 threadpool 包装
        # (file.read() 是 async, 必须在 async context 调, 同步 I/O 也用 sync open/write)
        result = await sync_upload_impl(
            file, request, max_size,
            RAG_TMP_DIR, RAG_UPLOAD_CHUNK_SIZE, RAG_UPLOAD_EMIT_BYTES, RAG_UPLOAD_EMIT_MS,
            kb_id=kb_id, department=department,
        )
    except Exception as e:
        return {"ok": False, "error": f"upload failed: {type(e).__name__}: {e}"}
    if not result.get("ok"):
        return result

    asyncio.create_task(_run_index_background(
        result["upload_id"], result["filepath"], result["filename"],
        result["source"], result["batch_id"], kb_id=kb_id, department=department,
        upload_elapsed_ms=result.get("upload_elapsed_ms"),
    ))
    return {"ok": True, "upload_id": result["upload_id"], "filename": result["filename"]}




async def sync_upload_impl(
    file, request, max_size, tmp_dir, chunk_size, emit_bytes, emit_ms,
    kb_id: str = "policy_general", department: str = "general",
) -> dict:
    """P0-1 流式上传 (async def, 直接在 upload_document 事件循环里跑).
    file.read() 是 async method, 必须 await. 写文件是 sync (open + write).
    """
    import time
    from backend.config.database import DOCS_DIRECTORY as _DOCS_DIRECTORY

    # _progress_queues / _extract_source 现为模块级显式导入，直接引用即可
    # （原先经 globals() 取值是为了绕开 import * 的名字丢失问题）

    safe_name = os.path.basename(file.filename or "")
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
        return {"ok": False, "error": "invalid filename"}
    # P1 fix: 用 realpath 解析 backend/data junction 符号链接
    # 避免同一物理文件被存为两条不同 file_path 记录（SQLite 主键冲突 → 重复 doc_id）
    abs_docs_dir = os.path.realpath(_DOCS_DIRECTORY)
    final_dir = os.path.abspath(os.path.join(abs_docs_dir, kb_id, department))
    final_path = os.path.normpath(os.path.join(final_dir, safe_name))
    # 末尾再 realpath 一次（防御 abspath 残留符号链接组件）
    final_path = os.path.realpath(final_path)
    try:
        if os.path.commonpath([abs_docs_dir, final_path]) != abs_docs_dir:
            return {"ok": False, "error": "invalid path"}
    except ValueError:
        return {"ok": False, "error": "invalid path"}

    ext = safe_name.rsplit(".", 1)[-1].lower()
    upload_id = uuid.uuid4().hex[:12]
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = f"{tmp_dir}/{upload_id}.{ext}"

    # 真实 HTTP 上传耗时（POST body 接收 + 写临时文件 + atomic rename）
    # 让前端"上传文件"阶段显示准确值，而非被减法逻辑吞掉为 0
    _upload_t0_sync = time.time()

    now = time.time()
    expired = [uid for uid, q in _progress_queues.items()
               if getattr(q, "_created_at", 0) < now - 1800]
    for uid in expired:
        _progress_queues.pop(uid, None)

    queue: Queue = Queue()
    queue._created_at = now
    _progress_queues[upload_id] = queue

    # 进度推送: 在 async context 直接 queue.put_nowait (因为是 asyncio.Queue, 跨 coroutine 同一 loop OK)
    def _safe_put(evt):
        queue.put_nowait(evt)

    try:
        total = 0
        last_emit_bytes = 0
        last_emit_time = time.time()
        cl_str = request.headers.get("content-length", "0")
        cl_int = int(cl_str) if cl_str.isdigit() else None

        with open(tmp_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    return {"ok": False, "error": f"file too large (max {max_size//1024//1024}MB, uploaded {total} bytes)"}
                f.write(chunk)

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

        os.makedirs(final_dir, exist_ok=True)
        os.replace(tmp_path, final_path)
        _safe_put({"stage": "uploading", "progress": 100, "bytes": total})

        return {
            "ok": True,
            "upload_id": upload_id,
            "filepath": final_path,
            "filename": safe_name,
            "size": total,
            "source": _extract_source(request),
            "batch_id": request.headers.get("X-Batch-Id") or None,
            "upload_elapsed_ms": int((time.time() - _upload_t0_sync) * 1000),
        }
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as cleanup_error:
                logger.warning(f"[RAG] 临时文件清理失败: {cleanup_error}")
        return {"ok": False, "error": f"upload failed: {type(e).__name__}: {e}"}


async def _cleanup_failed_upload(filepath: str) -> None:
    """索引失败后删除已落盘文件，避免孤儿文档被后续扫描重新索引。"""
    if not filepath:
        return
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
            logger.info(f"[RAG] 已清理索引失败文件: {filepath}")
    except OSError as exc:
        logger.warning(f"[RAG] 索引失败文件清理失败 {filepath}: {exc}")


async def _run_index_background(upload_id: str, filepath: str, filename: str, source: str = "", batch_id: str | None = None, kb_id: str = "policy_general", department: str = "general", upload_elapsed_ms: int | None = None):
    """后台执行索引，向 queue 推送阶段事件；完成后记录操作日志。

    upload_elapsed_ms: sync_upload_impl 实测的 HTTP 上传耗时（POST + 写文件 + atomic rename）。
    终态用此值填 stage_elapsed["uploading"]，避免被减法逻辑吞掉为 0。
    total_ms 改为 upload_elapsed_ms + 后台索引耗时（端到端总耗时）。
    """
    queue = _progress_queues.get(upload_id)
    if queue is None:
        await _cleanup_failed_upload(filepath)
        return

    async def emit(stage: str, message: str = "", **extra):
        await queue.put({"stage": stage, "message": message, **extra})

    _upload_t0 = time.time()
    result = None
    try:
        await emit("uploading", f"文件 {filename} 已保存，开始索引")

        # 同步索引（在线程池跑，不阻塞事件循环）；返回含 trace_id
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _do_index_sync, upload_id, filepath, filename, loop, kb_id, department)
    except Exception as e:
        logger.error(f"[RAG] 后台索引失败: {e}")
        await _cleanup_failed_upload(filepath)
        await emit("error", str(e))
        await queue.put(None)
        _safe_log_op("", filename, "upload", source, trace_id=None, batch_id=batch_id,
                     result="failed", duration_ms=int((time.time() - _upload_t0) * 1000) + (upload_elapsed_ms or 0),
                     detail={"error": str(e)[:200]})
        return

    terminal = (result or {}).get("terminal", "done")
    # span_id → 前端 stage 键的统一映射（终态阶段耗时使用同一规则）
    _SPAN_STAGE_KEY = ProgressListener.SPAN_STAGE_KEY
    if terminal == "duplicate":
        duplicate_doc = (result or {}).get("doc") or {}
        stage_elapsed = (result or {}).get("stage_elapsed") or {}
        # 用真实上传耗时覆盖（duplicate 跳过索引，后端算的 uploading 没意义）
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        total_ms = (upload_elapsed_ms or 0) + int((time.time() - _upload_t0) * 1000)
        await emit("duplicate", "文件已存在，未重复索引",
                   doc=duplicate_doc, trace_id="", stage_elapsed=stage_elapsed, total_ms=total_ms)
        await queue.put(None)
        _safe_log_op(
            duplicate_doc.get("doc_id", ""), filename, "upload", source,
            trace_id="", batch_id=batch_id, result="duplicate",
            duration_ms=total_ms,
            detail={"duplicate": True, "chunk_count": duplicate_doc.get("chunk_count", 0)},
        )
        return

    # 成功终态：按 path 直接拿刚索引的文档
    new_doc = None
    try:
        reg = _get_registry()
        new_doc = reg.get_by_path(filepath)
        # 终态携带完整阶段耗时（来自后端 span duration_ms），覆盖前端累加
        raw_elapsed = (result or {}).get("stage_elapsed") or {}
        # span_id 转为前端 stage 键（如 index_chunk → chunking）
        stage_elapsed: dict[str, int] = {}
        for sid, ms in raw_elapsed.items():
            stage_key = _SPAN_STAGE_KEY.get(sid, sid)
            stage_elapsed[stage_key] = int(ms)
        # 优先用 sync_upload_impl 实测的上传耗时；缺失时回退到减法逻辑（向后兼容）
        index_elapsed_ms = int((time.time() - _upload_t0) * 1000)
        total_ms = index_elapsed_ms + (upload_elapsed_ms or 0)
        if upload_elapsed_ms is not None:
            stage_elapsed["uploading"] = upload_elapsed_ms
        elif "uploading" not in stage_elapsed:
            others = sum(v for k, v in stage_elapsed.items() if k != "uploading")
            stage_elapsed["uploading"] = max(total_ms - others, 0)
        await emit("done", "索引完成", doc=new_doc,
                   trace_id=(result or {}).get("trace_id") or "",
                   stage_elapsed=stage_elapsed,
                   total_ms=total_ms)
    except Exception as e:
        await emit("done", "索引完成（文档信息获取失败）")
        logger.warning(f"[RAG] 获取入库文档信息失败: {e}")
    finally:
        await queue.put(None)

    _safe_log_op(
        (new_doc or {}).get("doc_id", ""), filename, "upload", source,
        trace_id=(result or {}).get("trace_id") or None,
        batch_id=batch_id, result="success",
        duration_ms=int((time.time() - _upload_t0) * 1000),
        detail={
            "chunk_count": (result or {}).get("chunk_count", 0),
            "file_hash": (result or {}).get("file_hash", ""),
            "duplicate": False,
            "doc_type": (new_doc or {}).get("doc_type", "general"),
            "llm_used": bool((new_doc or {}).get("llm_used", False)),
            "confidence": (new_doc or {}).get("confidence", 0),
        },
    )


def _do_index_sync(upload_id: str, filepath: str, filename: str, main_loop: asyncio.AbstractEventLoop, kb_id: str = "policy_general", department: str = "general"):
    """同步执行索引，通过 _progress_queues[upload_id] 推送阶段（从线程内调用）。

    P1 改造：
      - 不再调 indexer.sync()（全盘扫描，会把已软删但原文件还在的文档判为 ADDED 重新索引→删除复活）。
        改用 reindex_file() 单文件索引。duplicate 检测保留（reindex_file 不做 hash 比对）。
      - 复用 pipeline 已加载的 embedding/vectordb/doc_db，避免每次上传重新加载 bge 模型。
      - doc_db 用 pipeline.doc_db（DOC_DB_PATH），修复原误用同一个 store 导致 doc 全文写进 chunk 库。
    """
    from backend.config import DOCS_DIRECTORY  # noqa: F401
    import hashlib

    queue = _progress_queues.get(upload_id)
    if queue is None:
        return

    # 同步索引开始时刻：用于 SSE uploading 阶段真实耗时；duplicate 也带上
    _upload_t0_sync = time.time()

    def sync_emit(stage: str, message: str = "", **extra):
        """从同步线程调用：run_coroutine_threadsafe 把事件投到主 async loop 的队列"""
        evt = {"stage": stage, "message": message, **extra}
        # 主 loop 在另一个线程，必须用 run_coroutine_threadsafe（不能 asyncio.get_event_loop()）
        asyncio.run_coroutine_threadsafe(queue.put(evt), main_loop)

    try:
        reg = _get_registry()

        # duplicate 检测：文件已索引且 SHA256 未变 → 跳过索引，emit duplicate stage
        # （reindex_file 不做 hash 比对，会直接重灌，所以这里必须先拦）
        existing = reg.get_by_path(filepath)
        # 文件大小用于 uploading 阶段的真实耗时（duplicate 也附带便于前端展示）
        try:
            _file_size = os.path.getsize(filepath)
        except OSError:
            _file_size = 0
        if existing and existing.get("status") == "active":
            with open(filepath, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            if existing.get("file_hash") == file_hash:
                logger.info(f"[RAG] 文件未变化，跳过索引: {filename}")
                return {
                    "trace_id": "",
                    "duplicate": True,
                    "terminal": "duplicate",
                    "doc": {**existing, "duplicate": True},
                    "stage_elapsed": {"uploading": int((time.time() - _upload_t0_sync) * 1000)},
                }
            else:
                logger.info(f"[RAG] 文件已变化，重新索引: {filename} (old={existing.get('file_hash','')[:12]} new={file_hash[:12]})")
        elif existing:
            logger.info(f"[RAG] 文件状态非 active ({existing.get('status')})，重新索引: {filename}")
        else:
            logger.info(f"[RAG] 新文件，首次索引: {filename} (path={filepath})")

        # 复用 pipeline 单例（启动时预热，此处通常毫秒级返回；若预热未完成会阻塞等待）
        sync_emit("uploading", "正在初始化索引管道（首次 ~15s）...")
        _pipe_t0 = time.time()
        pipeline = get_rag_pipeline()
        _pipe_elapsed = int((time.time() - _pipe_t0) * 1000)
        if _pipe_elapsed > 3000:
            logger.info(f"[RAG] 管道初始化耗时 {_pipe_elapsed}ms（可能预热未完成）")
        listener = ProgressListener(sync_emit)
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=pipeline.vectordb,
            doc_db=pipeline.doc_db,
            embedding=pipeline.embedding,
            registry=reg,
            kb_id=kb_id,
            department=department,
        )
        try:
            # 单文件索引（不再 sync 全盘扫描 → 不会复活已删文档 + 上传变快）
            result = indexer.reindex_file(filepath)
        finally:
            listener.unsub()
        logger.info(f"[RAG] 上传索引完成: {filename} → {result}")
        return result
    except Exception as e:
        raise




@router.get("/upload/{upload_id}/stream")
async def stream_upload_progress(upload_id: str):
    """SSE 订阅：实时推送上传 + 索引进度。

    事件类型：
      stage  → {stage: uploading|parsing|chunking|embedding|writing|done|error, message}
      done   → 包含 doc 信息
      error  → 索引失败
    """
    queue = _progress_queues.get(upload_id)
    if queue is None:
        async def not_found():
            yield _sse_encode("error", {"message": f"upload_id {upload_id} 不存在或已过期"})
        return StreamingResponse(not_found(), media_type="text/event-stream")

    async def event_stream():
        try:
            while True:
                evt = await queue.get()
                if evt is None:
                    break
                # 关键：保留 stage 字段在 data 中 — 前端 onmessage 解析 payload.stage
                # _sse_encode 的 event 参数（stage）不再用作 SSE event name（无 event: 字段）
                yield _sse_encode("message", evt)
        finally:
            # SSE 断开 → 清理队列（防内存泄漏）
            _progress_queues.pop(upload_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

