"""RAG 文档管理路由 — PR-2.x 从 rag.py 抽出。"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import StreamingResponse
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready, get_rag_status
import os
import time

from backend.config import CHROMA_PATH, EMBEDDING_MODEL_PATH
from backend.config.rag import METADATA_SCHEMA_FINGERPRINT
from backend.rag.indexing.indexer import IncrementalIndexer
from backend.shared.logger import logger
# 显式导入替代 import *：_rag_shared 声明了 __all__，星号导入会静默丢掉
# os/time/logger 等名字，连 except 分支里的 logger 也变成 NameError（兜底失效直接 500）
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_op_logger,
    _get_registry,
    _safe_log_op,
)

router = APIRouter()


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

        # 批量查询每个文档的最新操作日志（委托给 DocumentOperationLogger）
        doc_ids = [d["doc_id"] for d in docs]
        last_ops, last_traces = _get_op_logger().get_last_ops_batch(doc_ids)

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


@router.get("/pending")
async def list_pending_docs(page: int = 1, page_size: int = 20):
    """列出待审核文档（status=pending_review，2026-08-11 P0 审核 Dashboard）。"""
    try:
        reg = _get_registry()
        result = reg.list_pending_review(page=page, page_size=page_size)
        # 格式化（与 list_documents 一致）
        for d in result["items"]:
            d["id"] = d.get("doc_id")
            d["name"] = d.get("file_name")
        return result
    except Exception as e:
        logger.error(f"[RAG] pending 列表失败: {e}")
        return {"items": [], "total": 0, "error": str(e)}


@router.post("/pending/{doc_id}/approve")
async def approve_pending_doc(doc_id: str, request: Request):
    """批准 pending 文档 → status='active'（2026-08-11）。"""
    source = _extract_source(request)
    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        if doc.get("status") != "pending_review":
            return {"ok": False, "error": f"文档状态为 {doc.get('status')}，不是 pending_review"}

        updated = reg.update_status_by_doc_id(doc_id, "active")
        if updated == 0:
            return {"ok": False, "error": "状态更新失败（可能并发）"}

        _safe_log_op(
            doc_id, doc.get("file_name", ""), "approve", source,
            trace_id=None, batch_id=None,
            result="success", duration_ms=0,
            detail={"from": "pending_review", "to": "active"},
        )

        # 触发 metadata_coverage 重算
        try:
            from backend.observability.metrics import update_metadata_coverage
            update_metadata_coverage()
        except Exception:
            pass

        return {"ok": True, "doc_id": doc_id, "new_status": "active"}
    except Exception as e:
        logger.error(f"[RAG] approve 失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/pending/{doc_id}/reject")
async def reject_pending_doc(doc_id: str, request: Request):
    """拒绝 pending 文档 → status='deleted'（2026-08-11）。"""
    source = _extract_source(request)
    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        if doc.get("status") != "pending_review":
            return {"ok": False, "error": f"文档状态为 {doc.get('status')}，不是 pending_review"}

        updated = reg.update_status_by_doc_id(doc_id, "deleted")
        if updated == 0:
            return {"ok": False, "error": "状态更新失败（可能并发）"}

        _safe_log_op(
            doc_id, doc.get("file_name", ""), "reject", source,
            trace_id=None, batch_id=None,
            result="success", duration_ms=0,
            detail={"from": "pending_review", "to": "deleted"},
        )

        return {"ok": True, "doc_id": doc_id, "new_status": "deleted"}
    except Exception as e:
        logger.error(f"[RAG] reject 失败: {e}")
        return {"ok": False, "error": str(e)}


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

