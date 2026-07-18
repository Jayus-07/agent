"""RAG 路由 — 知识库检索问答 + 文档管理"""
import asyncio
import os, uuid, json
from asyncio import Queue
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline
from backend.rag.indexing.doc_registry import DocumentRegistry
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.config import DOC_REGISTRY_PATH, CHROMA_PATH, EMBEDDING_MODEL_PATH
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
    page: int = 1,
    page_size: int = 20,
):
    """文档列表 — 支持搜索、分页、类型/状态过滤"""
    try:
        reg = _get_registry()

        # 统一走 search() 保证分页一致（无过滤条件时等效于全量分页）
        result = reg.search(
            keyword=keyword, type_filter=type, status_filter=status or "active",
            page=page, page_size=page_size,
        )
        docs = result["items"]
        total = result["total"]

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
            }

        return {
            "documents": [_format_doc(d) for d in docs],
            "total": total,
            "page": result["page"],
            "page_size": result["page_size"],
        }
    except Exception as e:
        logger.error(f"[RAG] documents 失败: {e}")
        return {"documents": [], "total": 0, "error": str(e)}


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
async def reindex_document(doc_id: str, force: bool = False):
    """单文件重新索引 — 删除旧向量后重新加载/分块/Embedding/写入"""
    from backend.config import DOCS_DIRECTORY
    from backend.rag.indexing.indexer import IncrementalIndexer
    from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
    from langchain_huggingface import HuggingFaceEmbeddings

    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}

        file_path = doc.get("file_path", "")
        if not file_path or not os.path.isfile(file_path):
            return {"ok": False, "error": f"文件不存在: {file_path}"}

        # 初始化向量库和索引器
        embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)
        vectordb = ChromaKnowledgeStore(persist_directory=CHROMA_PATH, embedding_function=embeddings)
        doc_db = ChromaKnowledgeStore(persist_directory=os.path.join(os.path.dirname(CHROMA_PATH), "doc_db"), embedding_function=embeddings)
        indexer = IncrementalIndexer(DOCS_DIRECTORY, vectordb, doc_db, embeddings, reg)

        # 执行重索引
        result = indexer.reindex_file(file_path)

        # 获取更新后的文档信息
        updated_doc = reg.get_by_doc_id(doc_id)
        return {"ok": True, "doc_id": doc_id, "chunk_count": result.get("chunk_count", 0), "hash": result.get("file_hash", ""), "doc": updated_doc}
    except Exception as e:
        logger.error(f"[RAG] reindex 失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """上传文档 → 保存到 data/docs/ → 触发后台增量索引 → 返回 upload_id 用于 SSE 订阅

    两阶段流程：
      1. POST 立即保存文件 + 返回 upload_id（不等索引）
      2. 前端 GET /upload/{upload_id}/stream 订阅 SSE，接收真实索引进度
    """
    from backend.config import DOCS_DIRECTORY

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "md", "txt", "docx"):
        return {"ok": False, "error": f"不支持的文件格式: .{ext}"}

    # 保存文件
    docs_dir = DOCS_DIRECTORY
    os.makedirs(docs_dir, exist_ok=True)
    # P1.5+ 修复：normalize path，避免 os.path.join 混合分隔符导致 registry 查询不匹配
    # db 里存的 path 是纯 '\\'，query 必须用同样的格式
    filepath = os.path.normpath(os.path.join(docs_dir, file.filename))
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # 创建 upload_id + 进度队列
    upload_id = uuid.uuid4().hex[:12]
    queue: Queue = Queue()
    _progress_queues[upload_id] = queue

    # 后台跑索引（不阻塞 HTTP 响应）
    asyncio.create_task(_run_index_background(upload_id, filepath, file.filename))

    return {"ok": True, "upload_id": upload_id, "filename": file.filename}


async def _run_index_background(upload_id: str, filepath: str, filename: str):
    """后台执行索引，向 queue 推送阶段事件。"""
    from backend.config import DOCS_DIRECTORY
    from backend.rag.indexing.indexer import IncrementalIndexer
    from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
    from langchain_huggingface import HuggingFaceEmbeddings

    queue = _progress_queues.get(upload_id)
    if queue is None:
        return

    async def emit(stage: str, message: str = "", **extra):
        await queue.put({"stage": stage, "message": message, **extra})

    try:
        await emit("uploading", f"文件 {filename} 已保存，开始索引")

        # 同步索引（在线程池跑，不阻塞事件循环）
        loop = asyncio.get_running_loop()
        # 把 loop 引用传给同步函数，让 run_coroutine_threadsafe 能把事件投回主 loop
        await loop.run_in_executor(None, _do_index_sync, upload_id, filepath, filename, loop)
    except Exception as e:
        logger.error(f"[RAG] 后台索引失败: {e}")
        await emit("error", str(e))
        await queue.put(None)  # sentinel
        return

    # 发送 done 事件（含新文档信息）
    try:
        reg = _get_registry()
        docs = reg.list_active()
        new_doc = next((d for d in docs if d["file_name"] == filename), None)
        await emit("done", "索引完成", doc=new_doc)
    except Exception as e:
        await emit("done", "索引完成（文档信息获取失败）")
    finally:
        await queue.put(None)  # sentinel → SSE 关闭


def _do_index_sync(upload_id: str, filepath: str, filename: str, main_loop: asyncio.AbstractEventLoop):
    """同步执行索引，通过 _progress_queues[upload_id] 推送阶段（从线程内调用）。"""
    from backend.config import DOCS_DIRECTORY  # noqa: F401  （用于 _ProgressIndexingWrapper）
    from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
    from langchain_huggingface import HuggingFaceEmbeddings
    import hashlib

    queue = _progress_queues.get(upload_id)
    if queue is None:
        return

    def sync_emit(stage: str, message: str = "", **extra):
        """从同步线程调用：run_coroutine_threadsafe 把事件投到主 async loop 的队列"""
        evt = {"stage": stage, "message": message, **extra}
        # 主 loop 在另一个线程，必须用 run_coroutine_threadsafe（不能 asyncio.get_event_loop()）
        asyncio.run_coroutine_threadsafe(queue.put(evt), main_loop)

    try:
        # P1.5+ 优化：检测文件是否已索引（SHA256 一致）→ 跳过 emit 完整 4 个 stage
        # 避免用户上传重复文档时进度条"一闪而过"（之前只看到 uploading + done）
        reg = _get_registry()
        existing = reg.get_by_path(filepath)
        if existing and existing.get("status") == "active":
            with open(filepath, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            if existing.get("file_hash") == file_hash:
                logger.info(f"[RAG] 文件未变化，跳过索引: {filename}")
                # emit 特殊 stage 'duplicate'，前端会显示"已存在"提示
                sync_emit("duplicate", "文件已存在，未重复索引",
                          doc={**existing, "duplicate": True})
                return

        embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)
        store = ChromaKnowledgeStore(persist_directory=CHROMA_PATH, embedding_function=embeddings)

        # Phase 1.5: 用 _ProgressListener 订阅 TraceCollector 的 span end 事件
        # 把 indexer 的 6 个标准 span 自动映射到前端 SSE 阶段
        listener = _ProgressListener(sync_emit)
        indexer = IncrementalIndexer(
            docs_dir=DOCS_DIRECTORY,
            vectordb=store,
            doc_db=store,
            embedding=embeddings,
            registry=reg,
        )
        try:
            result = indexer.sync()
        finally:
            listener.unsub()
        logger.info(f"[RAG] 上传索引完成: {filename} → {result}")
    except Exception as e:
        sync_emit("error", f"索引失败: {e}")
        raise


class _ProgressListener:
    """订阅 TraceCollector 的 span end 事件，把 indexer 的 6 个标准 span
    映射到前端 SSE 阶段 (parsing / chunking / embedding / writing)。

    Phase 1.5: 替代原 _ProgressIndexingWrapper 的 monkey-patch，直接订阅
    TraceCollector 事件，避免与 indexer 内部逻辑耦合。
    """

    # span_id → SSE stage 映射（None 表示不单独 emit，由 done 事件统一）
    SPAN_STAGE_MAP = {
        "index_parse":     "parsing",
        "index_chunk":     "chunking",
        "index_embed":     "embedding",
        "index_vector_db": "writing",
        # index_upload → uploading（已在 _run_index_background emit）
        # index_metadata → 不单独 emit，由 done 事件携带最终结果
    }

    def __init__(self, emit_fn):
        from backend.rag.tracer import trace_collector
        self._emit = emit_fn
        self._unsub = trace_collector.subscribe(self._on_span_end)

    def _on_span_end(self, trace, span):
        stage = self.SPAN_STAGE_MAP.get(span.span_id)
        if not stage:
            return
        # 从 span.metrics 提取进度文案
        msg = self._format_message(span)
        try:
            self._emit(stage, msg)
        except Exception:
            pass  # listener emit 失败不影响 indexer

    @staticmethod
    def _format_message(span) -> str:
        """根据 span_id + metrics 构造前端可读进度文案。"""
        m = span.metrics or {}
        if span.span_id == "index_parse":
            return f"已解析 {m.get('doc_count', 0)} 页"
        if span.span_id == "index_chunk":
            kept = m.get("kept_chunks", 0)
            filtered = m.get("filtered_out", 0)
            return f"切分 {kept} chunks" + (f"（过滤 {filtered}）" if filtered else "")
        if span.span_id == "index_embed":
            succ = m.get("succeeded", 0)
            attempted = m.get("attempted", 0)
            return f"Embedding {succ}/{attempted}" + ("（部分失败）" if succ < attempted else "")
        if span.span_id == "index_vector_db":
            return f"写入 {m.get('written', 0)} 向量"
        return ""

    def unsub(self):
        self._unsub()


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
async def delete_document(doc_id: str):
    """删除文档（软删除 + 清理向量）"""
    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        reg.mark_deleted(doc["file_path"])
        # 清理 Chroma 中的向量
        try:
            from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
            from langchain_huggingface import HuggingFaceEmbeddings
            embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)
            store = ChromaKnowledgeStore(persist_directory=CHROMA_PATH, embedding_function=embeddings)
            store.delete(where={"doc_id": doc_id})
        except Exception:
            pass
        logger.info(f"[RAG] 已删除文档: {doc_id}")
        return {"ok": True, "doc_id": doc_id}
    except Exception as e:
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

        # 从 ChromaDB 查询 chunk 实际内容（使用公开 API）
        chunks = []
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
            from backend.rag.vectorstore.knowledge_store import ChromaKnowledgeStore
            embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_PATH)
            store = ChromaKnowledgeStore(persist_directory=CHROMA_PATH, embedding_function=embeddings)
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


# ── 问答（已有） ──

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
