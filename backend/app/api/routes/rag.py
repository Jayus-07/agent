"""RAG 路由 — 知识库检索问答 + 文档管理"""
import asyncio
import os, uuid, json, time
from asyncio import Queue
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready, get_rag_status
from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.indexing.operation_log import DocumentOperationLogger
from backend.rag.progress_listener import ProgressListener
from backend.config import DOC_REGISTRY_PATH, DOC_OPERATION_LOG_PATH, CHROMA_PATH, EMBEDDING_MODEL_PATH
from backend.config.rag import METADATA_SCHEMA_FINGERPRINT
from backend.shared.logger import logger

# ── Upload 进度队列（按 upload_id 索引） ───────────────────────
# 内存 dict，30 分钟未活动自动清理
_progress_queues: dict[str, Queue] = {}

def _sse_encode(event: str, data: dict) -> str:
    """SSE 编码：data 中已含 stage 字段，前端用单一 onmessage 解析。

    故意省略 'event: <name>' 字段 — 让所有消息走 EventSource 默认 message event
    触发 onmessage listener，避免 addEventListener 注册时机 race condition。

    故意用 ensure_ascii=True — StreamingResponse 默认 Content-Type 没 charset，
    非 ASCII 字节可能被客户端按 latin-1 解码导致乱码。ensure_ascii=True 让 SSE 流
    纯 ASCII（中文 → \\uXXXX），客户端 JSON.parse 自动还原为 unicode 字符串。
    """
    return f"data: {json.dumps(data, ensure_ascii=True)}\n\n"

router = APIRouter(prefix="/rag", tags=["知识库"])

_registry: DocumentRegistry | None = None

def _get_registry() -> DocumentRegistry:
    global _registry
    if _registry is None:
        _registry = DocumentRegistry(DOC_REGISTRY_PATH)
    return _registry


_op_logger: DocumentOperationLogger | None = None


def _get_op_logger() -> DocumentOperationLogger:
    global _op_logger
    if _op_logger is None:
        _op_logger = DocumentOperationLogger(DOC_OPERATION_LOG_PATH)
    return _op_logger


def _extract_source(request: Request) -> str:
    """提取操作来源：IP | User-Agent（auth 接入后可加 user_id）。"""
    client_host = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    return f"{client_host} | {ua}"


def _safe_log_op(
    doc_id: str, doc_name: str, operation: str, source: str,
    trace_id: str | None = None, batch_id: str | None = None,
    result: str = "success", detail: dict | None = None,
    duration_ms: int = 0,
) -> None:
    """记录操作日志，失败不影响主流程（审计日志写挂不能阻断业务）。"""
    try:
        _get_op_logger().log(
            doc_id=doc_id, doc_name=doc_name, operation=operation, source=source,
            trace_id=trace_id, batch_id=batch_id, result=result, detail=detail,
            duration_ms=duration_ms,
        )
    except Exception as e:
        logger.warning(f"[RAG] 记录操作日志失败 ({operation}): {e}")


@router.get("/health")
async def rag_health():
    """RAG 管道就绪检查 — 前端上传前轮询此端点。"""
    return get_rag_status()

@router.get("/knowledge-bases")
async def list_knowledge_bases():
    """返回知识库列表（含文档计数）。"""
    from backend.config.knowledge_base import get_kb_list
    kbs = get_kb_list()
    try:
        reg = _get_registry()
        for kb in kbs:
            kb["doc_count"] = reg.count_by_kb_id(kb["id"])
    except Exception:
        logger.warning("统计知识库文档数失败，回退为 0", exc_info=True)
        for kb in kbs:
            kb["doc_count"] = 0
    return {"knowledge_bases": kbs}

@router.get("/stats")
async def get_stats():
    """知识库统计"""
    try:
        reg = _get_registry()
        docs = reg.list_active()
        total_chunks = sum(d.get("chunk_count", 0) for d in docs)
        return {
            "kb_count": len(set(d.get("kb_id", "default") for d in docs)),
            "doc_count": len(docs),
            "chunk_count": total_chunks,
            "embedding_model": os.path.basename(EMBEDDING_MODEL_PATH),
            "vector_db": "Chroma",
            "vector_db_path": CHROMA_PATH,
        }
    except Exception as e:
        logger.error(f"[RAG] stats 失败: {e}")
        return {"kb_count": 0, "doc_count": 0, "chunk_count": 0, "embedding_model": "", "vector_db": "Chroma", "error": str(e)}


@router.get("/documents")
async def list_documents(
    keyword: str = "",
    type: str = "",
    status: str = "",
    doc_type: str = "",
    kb_id: str = "",
    department: str = "",
    confidence_min: float = 0,
    llm_used: bool | None = None,
    quality_min: float = 0,
    sort_by: str = "updated_at",
    page: int = 1,
    page_size: int = 20,
):
    """文档列表 — 支持搜索、分页、元数据过滤"""
    try:
        reg = _get_registry()

        result = reg.search(
            keyword=keyword, type_filter=type, status_filter=status or "active",
            doc_type=doc_type, kb_id=kb_id, department=department,
            confidence_min=confidence_min,
            llm_used=llm_used, quality_min=quality_min, sort_by=sort_by,
            page=page, page_size=page_size,
        )
        docs = result["items"]
        total = result["total"]

        # 批量查询每个文档的最新操作日志
        doc_ids = [d["doc_id"] for d in docs]
        last_ops: dict[str, dict] = {}
        last_traces: dict[str, str] = {}
        if doc_ids:
            import sqlite3
            placeholders = ",".join(["?"] * len(doc_ids))
            op_conn = sqlite3.connect(DOC_OPERATION_LOG_PATH)
            op_conn.row_factory = sqlite3.Row
            rows = op_conn.execute(
                f"SELECT doc_id, operation, created_at, trace_id, result FROM doc_operation_log "
                f"WHERE id IN (SELECT MAX(id) FROM doc_operation_log WHERE doc_id IN ({placeholders}) GROUP BY doc_id)",
                doc_ids,
            ).fetchall()
            last_ops = {r["doc_id"]: dict(r) for r in rows}
            trace_rows = op_conn.execute(
                f"SELECT doc_id, trace_id FROM doc_operation_log "
                f"WHERE trace_id IS NOT NULL AND trace_id != '' AND doc_id IN ({placeholders}) "
                f"AND id IN (SELECT MAX(id) FROM doc_operation_log "
                f"WHERE trace_id IS NOT NULL AND trace_id != '' AND doc_id IN ({placeholders}) GROUP BY doc_id)",
                [*doc_ids, *doc_ids],
            ).fetchall()
            last_traces = {r["doc_id"]: r["trace_id"] for r in trace_rows}
            op_conn.close()

        embedding_model_name = os.path.basename(EMBEDDING_MODEL_PATH)

        def _format_doc(d: dict) -> dict:
            file_name = d.get("file_name", "")
            ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "unknown"
            return {
                "id": d["doc_id"],
                "name": file_name,
                "path": d.get("file_path", ""),
                "kb_id": d.get("kb_id", "default"),
                "type": ext,
                "size": d.get("file_size", 0),
                "chunk_count": d.get("chunk_count", 0),
                "chunks": d.get("chunk_count", 0),  # 向后兼容旧字段名
                "hash": d.get("file_hash", ""),
                "status": d.get("status", "active"),
                "embedding_model": embedding_model_name,
                "index_version": 1,
                "last_indexed": d.get("last_indexed"),
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at"),
                "parse_time_ms": None,   # 预留，后续接入
                "index_time_ms": None,   # 预留，后续接入
                "doc_type": d.get("doc_type", ""),
                "business_domain": d.get("business_domain", ""),
                "last_operation": last_ops.get(d["doc_id"], {}).get("operation", ""),
                "last_operation_at": last_ops.get(d["doc_id"], {}).get("created_at", ""),
                "last_trace_id": last_traces.get(d["doc_id"], ""),
                "last_operation_result": last_ops.get(d["doc_id"], {}).get("result", ""),
                "metadata_fingerprint": d.get("metadata_fingerprint", ""),
                "doc_version": d.get("doc_version", 1),
                "kb_id": d.get("kb_id", "policy_general"),
                "department": d.get("department", ""),
                "kb_version": d.get("kb_version", "v1"),
            }

        return {
            "documents": [_format_doc(d) for d in docs],
            "total": total,
            "page": result["page"],
            "page_size": result["page_size"],
            "current_fingerprint": METADATA_SCHEMA_FINGERPRINT,
        }
    except Exception as e:
        logger.error(f"[RAG] documents 失败: {e}")
        return {"documents": [], "total": 0, "error": str(e)}


@router.get("/operations")
async def list_operations(
    page: int = 1,
    page_size: int = 20,
    operation: str = "",
    doc_id: str = "",
    batch_id: str = "",
):
    """文档操作审计日志 — 谁上传/重索引/删除了哪个文档，含 trace_id + batch_id 关联"""
    try:
        return _get_op_logger().list(
            page=page, page_size=page_size, operation=operation, doc_id=doc_id, batch_id=batch_id,
        )
    except Exception as e:
        logger.error(f"[RAG] operations 失败: {e}")
        return {"items": [], "total": 0, "error": str(e)}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    """文档详情 — 含 chunk 配置、embedding 模型等完整信息"""
    from backend.config import CHUNK_SIZE, CHUNK_OVERLAP

    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}

        file_name = doc.get("file_name", "")
        ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "unknown"
        embedding_model_name = os.path.basename(EMBEDDING_MODEL_PATH)

        return {
            "ok": True,
            "doc": {
                "id": doc["doc_id"],
                "name": file_name,
                "path": doc.get("file_path", ""),
                "kb_id": doc.get("kb_id", "default"),
                "type": ext,
                "size": doc.get("file_size", 0),
                "chunk_count": doc.get("chunk_count", 0),
                "chunks": doc.get("chunk_count", 0),
                "hash": doc.get("file_hash", ""),
                "status": doc.get("status", "active"),
                "embedding_model": embedding_model_name,
                "chunk_size": CHUNK_SIZE,
                "overlap": CHUNK_OVERLAP,
                "index_version": 1,
                "last_indexed": doc.get("last_indexed"),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
                "parse_time_ms": None,
                "index_time_ms": None,
            },
        }
    except Exception as e:
        logger.error(f"[RAG] 文档详情失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/documents/{doc_id}/reindex")
async def reindex_document(doc_id: str, request: Request, force: bool = False):
    """单文件重新索引 — 删除旧向量后重新加载/分块/Embedding/写入"""
    require_rag_ready()
    from backend.config import DOCS_DIRECTORY
    source = _extract_source(request)
    batch_id = request.headers.get("X-Batch-Id") or None
    doc_name = ""

    try:
        _t0 = time.time()
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        doc_name = doc.get("file_name", "")

        file_path = doc.get("file_path", "")
        if not file_path or not os.path.isfile(file_path):
            return {"ok": False, "error": f"文件不存在: {file_path}"}

        # 复用 pipeline 单例的 store/embedding（不再每次 new 加载模型；doc_db 路径与 upload 一致）
        pipeline = get_rag_pipeline()
        indexer = IncrementalIndexer(
            DOCS_DIRECTORY, pipeline.vectordb, pipeline.doc_db, pipeline.embedding, reg,
        )

        # 执行重索引
        result = indexer.reindex_file(file_path)
        elapsed_ms = int((time.time() - _t0) * 1000)

        # 获取更新后的文档信息（含 metadata 字段）
        updated_doc = reg.get_by_doc_id(doc_id) or {}
        _safe_log_op(doc_id, doc_name, "reindex", source,
                     trace_id=result.get("trace_id") or None, batch_id=batch_id,
                     result="success", duration_ms=elapsed_ms,
                     detail={"chunk_count": result.get("chunk_count", 0),
                             "file_hash": result.get("file_hash", ""),
                             "doc_type": updated_doc.get("doc_type", "general"),
                             "llm_used": bool(updated_doc.get("llm_used", False)),
                             "confidence": updated_doc.get("confidence", 0)})

        return {"ok": True, "doc_id": doc_id, "chunk_count": result.get("chunk_count", 0), "hash": result.get("file_hash", ""), "doc": updated_doc}
    except Exception as e:
        logger.error(f"[RAG] reindex 失败: {e}")
        _safe_log_op(doc_id, doc_name, "reindex", source, trace_id=None, batch_id=batch_id,
                     result="failed", duration_ms=int((time.time() - _t0) * 1000) if '_t0' in dir() else 0,
                     detail={"error": str(e)[:200]})
        return {"ok": False, "error": str(e)}


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
    _g = globals()
    _progress_queues = _g["_progress_queues"]
    _extract_source = _g["_extract_source"]

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


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    """删除文档 — 软删 registry + 清理两处向量 + 删原文件（防 sync 复活）"""
    source = _extract_source(request)
    batch_id = request.headers.get("X-Batch-Id") or None
    doc_name = ""
    _delete_t0 = time.time()
    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        doc_name = doc.get("file_name", "")
        file_path = doc.get("file_path", "")

        # ① 软删 registry — 按 doc_id 删所有行（修复绝对/相对路径重复行漏删）
        deleted_rows = reg.mark_deleted_by_doc_id(doc_id)
        logger.info(f"[RAG] 软删 doc_id={doc_id}: {deleted_rows} 行")
        warnings: list[str] = []
        if deleted_rows == 0:
            warnings.append("registry 中无活跃记录（可能已被删除）")

        # ② 清理两处向量
        pipeline = get_rag_pipeline()
        try:
            pipeline.vectordb.delete(where={"doc_id": doc_id})
        except Exception as e:
            msg = f"向量库(chunks)清理失败: {e}"
            logger.warning(f"[RAG] {msg}")
            warnings.append(msg)
        try:
            pipeline.doc_db.delete(where={"doc_id": doc_id})
        except Exception as e:
            msg = f"向量库(doc)清理失败: {e}"
            logger.warning(f"[RAG] {msg}")
            warnings.append(msg)

        # ③ 清理 chunk_store
        try:
            from backend.rag.indexing.chunk_store import get_chunk_store
            get_chunk_store().delete_by_doc_id(doc_id)
        except Exception as e:
            msg = f"chunk_store 清理失败: {e}"
            logger.warning(f"[RAG] {msg}")
            warnings.append(msg)

        # ④ 删原文件
        if file_path:
            try:
                os.remove(file_path)
            except FileNotFoundError:
                pass  # 文件已不在，正常
            except OSError as e:
                msg = f"原文件删除失败: {e}"
                logger.warning(f"[RAG] {msg}")
                warnings.append(msg)

        logger.info(f"[RAG] 已删除文档: {doc_id}" + (f"（{len(warnings)} 个警告）" if warnings else ""))
        _safe_log_op(doc_id, doc_name, "delete", source, trace_id=None, batch_id=batch_id,
                     result="success", duration_ms=int((time.time() - _delete_t0) * 1000),
                     detail={"file_path": file_path, "deleted_rows": deleted_rows, "warnings": warnings or None})
        return {"ok": True, "doc_id": doc_id, "warnings": warnings or None}
    except Exception as e:
        logger.error(f"[RAG] 删除文档失败: {e}")
        _safe_log_op(doc_id, doc_name, "delete", source, trace_id=None, batch_id=batch_id,
                     result="failed", duration_ms=int((time.time() - _delete_t0) * 1000),
                     detail={"error": str(e)[:200]})
        return {"ok": False, "error": str(e)}


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str):
    """获取文档的 Chunk 列表（含内容和 metadata）"""
    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"doc_id": doc_id, "chunks": [], "error": "文档不存在"}
        chunk_ids_str = doc.get("chunk_ids", "[]")
        import json as _json
        chunk_ids = _json.loads(chunk_ids_str) if isinstance(chunk_ids_str, str) else chunk_ids_str

        # 从 ChromaDB 查询 chunk 实际内容（复用 pipeline store，不再 new embeddings）
        chunks = []
        try:
            store = get_rag_pipeline().vectordb
            # 用公开 API get(where=...) 按 doc_id 获取所有 chunks
            results = store.get(where={"doc_id": doc_id})
            if results and results.get("ids"):
                for i, cid in enumerate(results["ids"]):
                    content = (results.get("documents") or [""] * len(results["ids"]))[i]
                    meta = (results.get("metadatas") or [{}] * len(results["ids"]))[i]
                    chunks.append({
                        "id": cid,
                        "content": content or "",
                        "metadata": meta or {},
                        "token_count": len((content or "").encode()),
                    })
        except Exception as e:
            logger.warning(f"[RAG] Chunk 查询失败: {e}")
            chunks = [{"id": cid, "content": "", "metadata": {}, "token_count": 0} for cid in chunk_ids]

        return {"doc_id": doc_id, "chunks": chunks, "total": len(chunks)}
    except Exception as e:
        return {"doc_id": doc_id, "chunks": [], "total": 0, "error": str(e)}


@router.get("/chunks/{doc_id}/detail")
async def get_chunk_detail(doc_id: str):
    """获取文档的完整 Chunk 文本（从 SQLite chunk_store，非 ChromaDB）。
    供 Trace 详情页查看每条 chunk 的完整内容、token 数、关键词。"""
    try:
        from backend.rag.indexing.chunk_store import get_chunk_store
        cs = get_chunk_store()
        rows = cs.get_by_doc_id(doc_id)
        return {
            "doc_id": doc_id,
            "chunks": [
                {
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "char_count": r["char_count"],
                    "keywords": r["keywords"],
                    "llm_keywords": r.get("llm_keywords", ""),
                    "llm_model": r.get("llm_model", ""),
                    "section_title": r.get("section_title", ""),
                    "doc_type": r.get("doc_type", ""),
                    "kb_id": r.get("kb_id", ""),
                    "department": r.get("department", ""),
                    "simulated_questions": r.get("simulated_questions", []),
                }
                for r in rows
            ],
            "total": len(rows),
        }
    except Exception as e:
        return {"doc_id": doc_id, "chunks": [], "total": 0, "error": str(e)}


from pydantic import BaseModel
class SearchRequest(BaseModel):
    query: str = ""

@router.post("/search")
async def search_knowledge(req: SearchRequest):
    query = req.query
    """检索测试 — 直接调 RAG Pipeline 检索链（不调 LLM）"""
    if not query.strip():
        return {"query": query, "results": [], "error": "请输入检索词"}
    try:
        pipeline = get_rag_pipeline()
        # 使用 chunk_retriever（CustomRetriever → ChromaDB 语义检索）
        retriever = getattr(pipeline, 'chunk_retriever', None)
        if not retriever:
            return {"query": query, "results": [], "error": "检索器未初始化"}
        import asyncio as _asyncio
        docs = await _asyncio.to_thread(retriever.retrieve, query)
        results = []
        for i, doc in enumerate(docs[:10]):
            results.append({
                "index": i,
                "content": doc.page_content[:300],
                "score": getattr(doc, 'score', None) if hasattr(doc, 'score') else doc.metadata.get('score'),
                "metadata": doc.metadata,
            })
        return {"query": query, "results": results, "total": len(results)}
    except Exception as e:
        logger.error(f"[RAG] 检索失败: {e}")
        return {"query": query, "results": [], "error": str(e)}


# ── 问答（已有）──

@router.post("", responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
async def rag_ask(req: RAGAskRequest):
    """知识库检索 + 大模型生成回答"""
    try:
        pipeline = get_rag_pipeline()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    kb_id = req.kb_id or "default"
    answer = await asyncio.to_thread(pipeline.ask, req.question, req.session_id, kb_id=kb_id)
    sources = getattr(pipeline.lc_chain, '_last_sources', [])
    return {"answer": answer, "session_id": req.session_id, "sources": sources}
