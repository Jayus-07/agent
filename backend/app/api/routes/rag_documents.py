"""RAG 文档管理路由 — PR-2.x 从 rag.py 抽出。"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import StreamingResponse, FileResponse, Response
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import (
    get_rag_pipeline,
    require_rag_ready,
    get_rag_status,
    require_rag_user,
    require_rag_editor,
    require_admin_user,
)
import asyncio
import os
import time

from backend.config import EMBEDDING_MODEL, EMBEDDING_MODEL_PATH, ENV_MODE


def _embedding_model_name() -> str:
    """实际生效的 embedding 模型名。

    云端模式返回 API 模型名（如 text-embedding-v3），本地模式返回本地模型
    路径的 basename。原实现固定取 EMBEDDING_MODEL_PATH 的 basename，
    云端模式下文档列表/统计永远错误显示本地模型名（bge-small-zh-v1.5）。
    """
    # 专项模型配置来自数据库；优先读取运行时绑定，不能让旧 env 默认值
    # 覆盖管理端刚保存的 Embedding 模型名。
    try:
        from backend.infra.llm.specialized import resolve_binding

        binding = resolve_binding("embedding")
        if binding is not None:
            return binding.model_name
    except Exception as exc:
        logger.debug("[RAG] 读取专项 Embedding 模型失败，使用兼容回退: %s", exc)
    if ENV_MODE == "cloud":
        return EMBEDDING_MODEL
    return os.path.basename(EMBEDDING_MODEL_PATH)
from backend.config.rag import METADATA_SCHEMA_FINGERPRINT
from backend.rag.indexing.processing_lineage_pg import (
    get_processing_lineage_repository,
)
from backend.shared.logger import logger
# 显式导入替代 import *：_rag_shared 声明了 __all__，星号导入会静默丢掉
# os/time/logger 等名字，连 except 分支里的 logger 也变成 NameError（兜底失效直接 500）
from backend.app.api.routes._rag_shared import (
    _extract_source,
    _get_op_logger,
    _get_registry,
    _safe_log_op,
    sanitize_doc_row,
)

router = APIRouter(dependencies=[Depends(require_rag_user)])


def _require_authz(request: Request):
    """路由级授权入口（2026-10-01 权限收口）：Principal → RagAuthorization。

    授权服务异常 fail-closed（403），绝不退化为无过滤查询。
    """
    from backend.app.api.identity import require_principal
    from backend.rag.authz import RagAuthorization, RagAuthorizationError

    principal = require_principal(request)
    try:
        return RagAuthorization.build(principal)
    except RagAuthorizationError as e:
        logger.error(f"[RAG] 授权失败: {e}")
        raise HTTPException(status_code=403, detail="授权服务暂不可用，已拒绝操作")


def _invisible_doc() -> Response:
    """无权文档的统一不可见响应（与「不存在」同形，不泄露存在性）。"""
    return Response(
        content='{"ok":false,"error":"文档不存在"}',
        status_code=404,
        media_type="application/json",
    )


def _deny_manage(reason: str):
    """管理动作越权的显式拒绝（写操作必须让调用方知道被拒）。"""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"ok": False, "error": reason})


@router.get("/stats")
async def get_stats(request: Request):
    """知识库统计 — 只统计当前主体可见范围（授权下推，不泄露全局量）。"""
    try:
        authz = _require_authz(request)
        reg = _get_registry()
        docs = [d for d in reg.list_active() if authz.can_read_row(d)]
        total_chunks = sum(d.get("chunk_count", 0) for d in docs)
        return {
            "kb_count": len(set(d.get("kb_id", "default") for d in docs)),
            "doc_count": len(docs),
            "chunk_count": total_chunks,
            "embedding_model": _embedding_model_name(),
            # 向量库唯一实现 = pgvector（agent_memory.rag_vectors 表）；
            # Chroma 与 VECTOR_BACKEND 开关已于 2026-09-17 删除。
            "vector_db": "pgvector",
            "vector_db_path": "pgvector:rag_vectors",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RAG] stats 失败: {e}")
        return {"kb_count": 0, "doc_count": 0, "chunk_count": 0, "embedding_model": "", "vector_db": "pgvector", "error": str(e)}


@router.get("/documents")
async def list_documents(
    request: Request,
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
    """文档列表 — 支持搜索、分页、元数据过滤；行集与 total 同受授权范围约束"""
    try:
        authz = _require_authz(request)
        reg = _get_registry()

        # 授权范围下推（2026-10-01）：显式 kb/department 参数是查询条件，
        # 不是授权依据——授权只来自主体属性，二者求交后进 SQL。
        if kb_id and not authz.can_search_kb(kb_id):
            return {"documents": [], "total": 0, "page": page,
                    "page_size": page_size, "current_fingerprint": METADATA_SCHEMA_FINGERPRINT}
        kb_scope = sorted(authz.kb_scope)
        # 显式 department 参数对非 admin 只能是本部门（查询条件可收窄）
        if department and not authz.is_admin and department != authz.principal.department:
            return {"documents": [], "total": 0, "page": page,
                    "page_size": page_size, "current_fingerprint": METADATA_SCHEMA_FINGERPRINT}
        visible_scopes = authz.sql_visible_scopes(reg.distinct_permission_scopes())

        result = reg.search(
            keyword=keyword, type_filter=type, status_filter=status or "active",
            doc_type=doc_type, kb_id=kb_id, department=department,
            confidence_min=confidence_min,
            llm_used=llm_used, quality_min=quality_min, sort_by=sort_by,
            page=page, page_size=page_size,
            kb_scope=kb_scope, visible_scopes=visible_scopes,
        )
        docs = result["items"]
        total = result["total"]

        # 批量查询每个文档的最新操作日志（委托给 DocumentOperationLogger）
        doc_ids = [d["doc_id"] for d in docs]
        last_ops, last_traces = _get_op_logger().get_last_ops_batch(doc_ids)

        embedding_model_name = _embedding_model_name()

        def _format_doc(d: dict) -> dict:
            file_name = d.get("file_name", "")
            ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "unknown"
            actual_embedding_model = d.get("embedding_model") or embedding_model_name
            return {
                "id": d["doc_id"],
                "name": file_name,
                # file_path 是服务器内部路径，不出 API 边界（2026-10-01 权限收口）
                "kb_id": d.get("kb_id", "policy_general"),
                "type": ext,
                "size": d.get("file_size", 0),
                "chunk_count": d.get("chunk_count", 0),
                "chunks": d.get("chunk_count", 0),  # 向后兼容旧字段名
                "hash": d.get("file_hash", ""),
                "status": d.get("status", "active"),
                "embedding_model": actual_embedding_model,
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
                "last_processing_run_id": d.get("last_processing_run_id", ""),
                "pipeline_version": d.get("pipeline_version", ""),
                "metadata_route": d.get("metadata_route", ""),
                "ocr_used": bool(d.get("ocr_used", False)),
                "ocr_model": d.get("ocr_model", ""),
                "metadata_model": d.get("metadata_model", ""),
                "model_count": int(d.get("model_count") or 0),
                "processing_status": d.get("processing_status", ""),
                "processing_finished_at": d.get("processing_finished_at"),
            }

        return {
            "documents": [_format_doc(d) for d in docs],
            "total": total,
            "page": result["page"],
            "page_size": result["page_size"],
            "current_fingerprint": METADATA_SCHEMA_FINGERPRINT,
        }
    except HTTPException:
        raise
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
async def list_pending_docs(request: Request, page: int = 1, page_size: int = 20):
    """列出待审核文档（status=pending_review）— 按「可管理范围」过滤：
    非 admin 编辑只看到本部门、可见库的待审文档（total 同口径）。"""
    try:
        authz = _require_authz(request)
        reg = _get_registry()
        dept_filter = "" if authz.is_admin else (authz.principal.department or "")
        result = reg.list_pending_review(
            page=page, page_size=page_size,
            kb_scope=sorted(authz.kb_scope), department=dept_filter,
        )
        # 格式化（与 list_documents 一致）+ 行级归属校验双保险
        items = []
        for d in result["items"]:
            ok, _ = authz.can_manage_row(d)
            if not ok:
                continue
            d["id"] = d.get("doc_id")
            d["name"] = d.get("file_name")
            items.append(sanitize_doc_row(d))
        result["items"] = items
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RAG] pending 列表失败: {e}")
        return {"items": [], "total": 0, "error": str(e)}


@router.post("/pending/{doc_id}/approve", dependencies=[Depends(require_rag_editor)])
async def approve_pending_doc(doc_id: str, request: Request):
    """批准 pending 文档 → status='active'（2026-08-11）。"""
    source = _extract_source(request)
    authz = _require_authz(request)
    try:
        # 归属校验先于任何模式分支：registry 是共享 PG，app 侧可直接裁决
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        ok, reason = authz.can_manage_row(doc)
        if not ok:
            return _deny_manage(reason)

        from backend.config.rag import RAG_MODE
        if RAG_MODE == "remote":
            # remote 模式下 app 不持有向量库，审核动作必须由 rag-service 执行。
            pipeline = await asyncio.to_thread(get_rag_pipeline)
            result = await asyncio.to_thread(
                pipeline.review_pending_doc, doc_id, "approve",
            )
            if result.get("ok"):
                _safe_log_op(
                    doc_id, "", "approve", source,
                    trace_id=None, batch_id=None,
                    result="success", duration_ms=0,
                    detail={"to": result.get("new_status")},
                    user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""),
                )
            return result

        if doc.get("status") != "pending_review":
            return {"ok": False, "error": f"文档状态为 {doc.get('status')}，不是 pending_review"}

        from backend.rag.indexing.review_service import review_pending_doc
        result = review_pending_doc(
            doc_id, "approve", registry=reg, pipeline=None,
        )
        if not result.get("ok"):
            return result

        _safe_log_op(
            doc_id, doc.get("file_name", ""), "approve", source,
            trace_id=None, batch_id=None,
            result="success", duration_ms=0,
            detail={"from": "pending_review", "to": result.get("new_status")},
            user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""),
        )

        # 触发 metadata_coverage 重算
        try:
            from backend.observability.metrics import update_metadata_coverage
            update_metadata_coverage()
        except Exception:
            logger.debug("[P1-10] metadata_coverage 重算失败", exc_info=True)

        return result
    except Exception as e:
        logger.error(f"[RAG] approve 失败: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/pending/{doc_id}/reject", dependencies=[Depends(require_rag_editor)])
async def reject_pending_doc(doc_id: str, request: Request):
    """拒绝 pending 文档 → status='deleted'（2026-08-11）。"""
    source = _extract_source(request)
    authz = _require_authz(request)
    try:
        # 归属校验先于任何模式分支
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        ok, reason = authz.can_manage_row(doc)
        if not ok:
            return _deny_manage(reason)

        from backend.config.rag import RAG_MODE
        if RAG_MODE == "remote":
            # remote 模式下 app 不得直接触碰 proxy.vectordb/doc_db/BM25。
            pipeline = await asyncio.to_thread(get_rag_pipeline)
            result = await asyncio.to_thread(
                pipeline.review_pending_doc, doc_id, "reject",
            )
            if result.get("ok"):
                _safe_log_op(
                    doc_id, "", "reject", source,
                    trace_id=None, batch_id=None,
                    result="success", duration_ms=0,
                    detail={"to": result.get("new_status")},
                    user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""),
                )
            return result

        if doc.get("status") != "pending_review":
            return {"ok": False, "error": f"文档状态为 {doc.get('status')}，不是 pending_review"}

        pipeline = await asyncio.to_thread(get_rag_pipeline)
        from backend.rag.indexing.review_service import review_pending_doc
        result = await asyncio.to_thread(
            review_pending_doc,
            doc_id, "reject", registry=reg, pipeline=pipeline,
        )

        _safe_log_op(
            doc_id, doc.get("file_name", ""), "reject", source,
            trace_id=None, batch_id=None,
            result="success" if result.get("ok") else "failed", duration_ms=0,
            detail={
                "from": "pending_review",
                "to": result.get("new_status"),
                "warnings": result.get("warnings"),
            },
            user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""),
        )

        return result
    except Exception as e:
        logger.error(f"[RAG] reject 失败: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request):
    """文档详情 — 含 chunk 配置、embedding 模型等完整信息（先授权后取数）"""
    from backend.config import CHUNK_SIZE, CHUNK_OVERLAP

    try:
        authz = _require_authz(request)
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc or not authz.can_read_row(doc):
            return _invisible_doc()

        file_name = doc.get("file_name", "")
        ext = file_name.rsplit(".", 1)[-1] if "." in file_name else "unknown"
        embedding_model_name = doc.get("embedding_model") or _embedding_model_name()

        return {
            "ok": True,
            "doc": {
                "id": doc["doc_id"],
                "name": file_name,
                # file_path 不出 API 边界（2026-10-01 权限收口）
                "kb_id": doc.get("kb_id", "default"),
                "type": ext,
                "size": doc.get("file_size", 0),
                "chunk_count": doc.get("chunk_count", 0),
                "chunks": doc.get("chunk_count", 0),
                "hash": doc.get("file_hash", ""),
                "status": doc.get("status", "active"),
                # 版本链字段必须与列表/检索溯源口径一致，避免详情页丢失
                # 「回答实际使用哪一版」所需的身份信息。
                "version_id": doc.get("version_id", ""),
                "doc_version": doc.get("doc_version", 1),
                "kb_version": doc.get("kb_version", ""),
                "effective_from": doc.get("effective_from", ""),
                "effective_to": doc.get("effective_to", ""),
                "supersedes_version_id": doc.get("supersedes_version_id", ""),
                # K5（2026-10-04）：上传链路已生成 LLM 摘要，详情补暴露供前端直读
                "summary": doc.get("summary", ""),
                # 归属与抽取元数据（2026-10-01 补齐：库内有值但详情映射漏了）
                "department": doc.get("department", ""),
                "doc_type": doc.get("doc_type", ""),
                "confidence": doc.get("confidence", 0),
                "business_domain": doc.get("business_domain", ""),
                "embedding_model": embedding_model_name,
                "chunk_size": CHUNK_SIZE,
                "overlap": CHUNK_OVERLAP,
                "index_version": 1,
                "last_indexed": doc.get("last_indexed"),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
                "parse_time_ms": None,
                "index_time_ms": None,
                "last_processing_run_id": doc.get("last_processing_run_id", ""),
                "pipeline_version": doc.get("pipeline_version", ""),
                "metadata_route": doc.get("metadata_route", ""),
                "ocr_used": bool(doc.get("ocr_used", False)),
                "ocr_model": doc.get("ocr_model", ""),
                "metadata_model": doc.get("metadata_model", ""),
                "model_count": int(doc.get("model_count") or 0),
                "processing_status": doc.get("processing_status", ""),
                "processing_finished_at": doc.get("processing_finished_at"),
            },
        }
    except Exception as e:
        logger.error(f"[RAG] 文档详情失败: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/documents/{doc_id}/file")
async def preview_document_file(doc_id: str, request: Request):
    """原文快照预览（原文定位 P1）：双重鉴权后返回平台存储的源文件。

    - 用户级裁决先于任何模式分支（对齐 review/delete 军规）：KB ABAC +
      文档 permission_scope（can_read_row 合并裁决），无权与不存在同形
      404（不泄露存在性）。⚠️ remote 分支此前只校验内部令牌直接转发——
      rag-service 侧无用户上下文，跳过本裁决 = 任何登录用户可读任意
      permission_scope 受限文档（2026-10-03 实机验收抓出并修复）；
    - 文件在谁家谁出货：local 模式直接读 DOCS_DIRECTORY；remote 模式经
      内部令牌转发 rag-service（文件在服务侧主机，app 不可直读）；
    - file_path 仅服务器侧使用，不回显、不出 API 边界。
    """
    authz = _require_authz(request)
    try:
        reg = _get_registry()
        doc = await asyncio.to_thread(reg.get_by_doc_id, doc_id)
        if (
            not doc
            or str(doc.get("status") or "") != "active"
            or not authz.can_read_row(doc)
        ):
            raise HTTPException(status_code=404, detail="文档不存在")

        from backend.config.rag import RAG_MODE

        if RAG_MODE == "remote":
            pipeline = await asyncio.to_thread(get_rag_pipeline)
            status, content, media = await asyncio.to_thread(
                pipeline.fetch_document_file, doc_id)
            if status != 200:
                # 用户可见性已由上方裁决；此处非 200 = 快照缺失/服务异常
                raise HTTPException(status_code=404, detail="文档不存在")
            return Response(content=content, media_type=media)

        import mimetypes
        import os

        from backend.config.database import DOCS_DIRECTORY

        file_path = os.path.realpath(str(doc.get("file_path") or ""))
        root = os.path.realpath(DOCS_DIRECTORY)
        if not file_path.startswith(root + os.sep) or not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="文档不存在")
        media = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        # 不传 filename：缺省 disposition 为 attachment，预览需要 inline 语义
        return FileResponse(file_path, media_type=media)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RAG] 原文快照失败 doc_id={doc_id}: {e}")
        raise HTTPException(status_code=503, detail="原文服务暂不可用") from e


@router.get("/documents/{doc_id}/processing-runs")
async def list_processing_runs(doc_id: str, request: Request, page: int = 1, page_size: int = 20):
    """返回文档历次入库运行及其模型血缘摘要（先授权后取数）。"""
    try:
        authz = _require_authz(request)
        doc = _get_registry().get_by_doc_id(doc_id)
        if not doc or not authz.can_read_row(doc):
            return {"doc_id": doc_id, "items": [], "total": 0, "error": "文档不存在"}
        result = await asyncio.to_thread(
            get_processing_lineage_repository().list_runs,
            doc_id,
            page=page,
            page_size=page_size,
        )
        return {"doc_id": doc_id, **result}
    except Exception as e:
        logger.error(f"[RAG] 处理运行列表查询失败: {e}")
        return {"doc_id": doc_id, "items": [], "total": 0, "error": str(e)}


@router.get("/documents/{doc_id}/processing-runs/{run_id}")
async def get_processing_run_detail(doc_id: str, run_id: str, request: Request):
    """返回一次入库运行的阶段、模型、版本和 token 明细（先授权后取数）。"""
    try:
        authz = _require_authz(request)
        doc = _get_registry().get_by_doc_id(doc_id)
        if not doc or not authz.can_read_row(doc):
            return {"doc_id": doc_id, "run_id": run_id, "error": "文档不存在"}
        detail = await asyncio.to_thread(
            get_processing_lineage_repository().get_run_detail,
            doc_id,
            run_id,
        )
        if detail is None:
            return {"doc_id": doc_id, "run_id": run_id, "error": "处理运行不存在"}
        try:
            from backend.observability.llm_usage_store import get_llm_usage_store
            detail["usage"] = await asyncio.to_thread(
                get_llm_usage_store().by_processing_run,
                run_id,
            )
        except Exception as usage_error:
            logger.warning(f"[RAG] 处理运行用量查询失败: {usage_error}")
            detail["usage"] = []
        return detail
    except Exception as e:
        logger.error(f"[RAG] 处理运行详情查询失败: {e}")
        return {"doc_id": doc_id, "run_id": run_id, "steps": [], "error": str(e)}


def _submit_reindex_task(doc_id: str, doc_name: str, *, batch_id: str | None,
                         source: str, user_id: str, tenant_id: str) -> dict | None:
    """把重索引提交进 rag_index 队列（rag-index-worker 执行，remote 断层收口）。

    返回提交/join 响应 dict；broker 不可达返回 None（调用方按模式降级或
    诚实报错）。活动任务 join：同文档已有 PENDING/RUNNING 的重索引任务
    → 返回既有 task_id，后来者搭车看进度（并发第一道闸；worker 侧
    advisory lock 是第二道，覆盖提交窗口竞态）。
    """
    from backend.models.task import TaskStatus
    from backend.services import task_service
    from backend.tasks.index_task_runtime import create_reindex_task_record
    from backend.tasks.queue_router import log_route, resolve_for_workflow

    latest = task_service.get_latest_biz_task("rag_reindex", doc_id)
    if latest and latest.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        return {"ok": True, "doc_id": doc_id, "async": True, "joined": True,
                "task_id": latest.id, "status": latest.status.value}

    # PAUSED/WAITING_USER 的历史行不算活动（允许重新提交；旧行保持暂停）
    task_id = create_reindex_task_record(
        doc_id, doc_name, tenant_id=tenant_id or "default",
        user_id=user_id or "system",
        reindex_kwargs={"doc_id": doc_id, "batch_id": batch_id,
                        "source": source})
    route = resolve_for_workflow("rag_index")
    log_route(route, dispatch_type="initial", task_id=task_id or "",
              celery_task_name="tasks.reindex_document")
    try:
        from backend.tasks.index_tasks import reindex_document_task

        async_result = reindex_document_task.apply_async(
            kwargs=dict(doc_id=doc_id, batch_id=batch_id, source=source,
                        db_task_id=task_id),
            queue=route.physical_queue)
    except Exception as e:
        logger.error(f"[RAG] reindex 入队失败（broker 不可达？）: {e}")
        if task_id:
            try:
                task_service.update_status(task_id, TaskStatus.FAILED,
                                           progress="入队失败（任务队列不可达）")
            except Exception:  # noqa: BLE001 — 状态补写失败不影响错误返回
                logger.warning("[RAG] reindex 任务行终态补写失败", exc_info=True)
        return None
    if task_id:
        task_service.mark_queued(task_id, getattr(async_result, "id", ""),
                                 queue=route.physical_queue)
    return {"ok": True, "doc_id": doc_id, "async": True, "joined": False,
            "task_id": task_id or "", "status": "PENDING"}


@router.post("/documents/{doc_id}/reindex", dependencies=[Depends(require_rag_editor)])
async def reindex_document(doc_id: str, request: Request, force: bool = False):
    """单文件重新索引 — 幂等提交（rag_index 队列）；降级时本机同步执行。

    2026-10-02 remote 断层收口：remote 模式下 app 持有的是代理 pipeline，
    直接构造 IncrementalIndexer 触碰 proxy.vectordb 必失败。现默认提交进
    rag_index 队列由 rag-index-worker（RAG_MODE=local）执行，执行体
    reindex_service.run_reindex 与本地同步路径共用；进度经 Redis 镜像供
    GET /documents/{doc_id}/reindex/status 轮询。broker 不可达时 local
    模式降级本机同步，remote 模式明确报错（不假装提交成功）。
    """
    require_rag_ready()
    from backend.config.rag import RAG_MODE, RAG_REINDEX_ASYNC_ENABLED
    source = _extract_source(request)
    batch_id = request.headers.get("X-Batch-Id") or None
    doc_name = ""
    authz = _require_authz(request)
    reg = _get_registry()

    try:
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        doc_name = doc.get("file_name", "")
        # 归属校验（2026-10-01）：任何 editor 只能重索引自己部门的文档
        ok, reason = authz.can_manage_row(doc)
        if not ok:
            return _deny_manage(reason)

        # F2 加固：doc_id 存在多条 active 行 = 历史重索引 bug 留下的重复数据
        # （同 doc_id 双路径、归属不一）。此时无法判断哪行是正确归属，直接拒绝，
        # 避免重索引作用在错误文件上并继续扩散损坏。
        if reg.count_active_by_doc_id(doc_id) > 1:
            return {"ok": False,
                    "error": "数据异常：该 doc_id 存在多条活跃记录（历史重索引损坏），请先清理重复数据后再重索引"}

        file_path = doc.get("file_path", "")
        if not file_path or not os.path.isfile(file_path):
            return {"ok": False, "error": f"文件不存在: {file_path}"}

        if RAG_REINDEX_ASYNC_ENABLED:
            from backend.app.api.identity import resolve_identity

            identity = resolve_identity(request)
            submitted = _submit_reindex_task(
                doc_id, doc_name, batch_id=batch_id, source=source,
                user_id=identity.user_id, tenant_id=identity.tenant_id)
            if submitted is not None:
                return submitted
            # broker 不可达：local 降级本机同步，remote 诚实报错
            if RAG_MODE != "local":
                return {"ok": False,
                        "error": "任务队列暂不可用，请稍后重试（需检查 rag-index-worker/broker）"}

        # 同步执行（开关关闭的历史路径 / local 降级），执行体与 worker 共用
        from backend.app.api.identity import resolve_identity
        from backend.rag.indexing import reindex_service
        sync_actor = resolve_identity(request)
        result = await asyncio.to_thread(
            reindex_service.run_reindex, doc_id,
            registry=reg, batch_id=batch_id, source=source,
            executor="local_sync", user_id=sync_actor.user_id)
        updated_doc = result.get("doc") or {}
        return {"ok": True, "doc_id": doc_id,
                "chunk_count": result.get("chunk_count", 0),
                "hash": result.get("hash", ""), "doc": updated_doc}
    except Exception as e:
        logger.error(f"[RAG] reindex 失败: {e}")
        from backend.rag.indexing import reindex_service
        reindex_service.log_reindex_failed(doc_id, source, batch_id=batch_id,
                                           error=str(e), duration_ms=0)
        return {"ok": False, "error": str(e)}


@router.get("/documents/{doc_id}/reindex/status")
async def reindex_status(doc_id: str, request: Request):
    """重索引任务状态 + 进度（提交接口的配对轮询通道）。

    task 为空 = 从未提交；status PENDING/RUNNING = 进行中；
    SUCCESS/FAILED/CANCELLED = 终态（前端停止轮询）。
    progress 为 Redis 镜像的最新阶段事件（stage=done/error/duplicate 即终态）。
    """
    authz = _require_authz(request)
    doc = _get_registry().get_by_doc_id(doc_id)
    if not doc or not authz.can_read_row(doc):
        return {"ok": False, "error": "文档不存在"}

    from backend.services import task_service

    task_view = None
    try:
        task = task_service.get_latest_biz_task("rag_reindex", doc_id)
        if task:
            task_view = {"task_id": task.id,
                         "status": task.status.value,
                         "progress": task.progress or "",
                         "error": task.error_message or "",
                         "retry_count": task.retry_count}
    except Exception as e:
        logger.warning(f"[RAG] reindex 任务状态查询失败: {e}")

    progress = None
    try:
        import json as _json

        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis
        from backend.rag.indexing.reindex_service import progress_key

        r = get_redis()
        if r is not None:
            raw = r.hgetall(f"{REDIS_KEY_PREFIX}upload:{progress_key(doc_id)}")
            if raw:
                _dec = lambda v: v.decode("utf-8", "replace") if isinstance(v, bytes) else v
                data = {_dec(k): _dec(v) for k, v in raw.items()}
                progress = {"stage": data.get("stage", ""),
                            "message": data.get("message", ""),
                            "updated_at": data.get("updated_at", "")}
                try:
                    progress["detail"] = _json.loads(data.get("detail") or "{}")
                except ValueError:
                    progress["detail"] = {}
    except Exception as e:
        logger.debug(f"[RAG] reindex 进度镜像读取失败: {e}")

    return {"ok": True, "doc_id": doc_id, "task": task_view, "progress": progress}


def _purge_doc_vectors(doc_id: str, file_path: str, pipeline, warnings: list[str]) -> None:
    """级联删除一个 Doc 的全部向量/索引/文件（pgvector 两 collection + chunk_store + BM25 + 原文件）。

    不负责 registry 状态变更——由调用方决定 mark_deleted 还是 update_status。
    """
    def _delete_store(store) -> None:
        # PGVector 的普通 delete 只删当前 collection；候选发布协议还会
        # 留下 <collection>::cand:<generation>，删除必须跨代次清理。
        supports_all_collections = any(
            "delete_all_collections_by_doc_id" in cls.__dict__
            for cls in type(store).__mro__
        )
        if supports_all_collections:
            store.delete_all_collections_by_doc_id(doc_id)
        else:
            store.delete(where={"doc_id": doc_id})

    try:
        _delete_store(pipeline.vectordb)
    except Exception as e:
        warnings.append(f"向量库(chunks)清理失败: {e}")
    try:
        _delete_store(pipeline.doc_db)
    except Exception as e:
        warnings.append(f"向量库(doc)清理失败: {e}")
    try:
        from backend.rag.indexing.chunk_store import get_chunk_store
        get_chunk_store().delete_by_doc_id(doc_id)
    except Exception as e:
        warnings.append(f"chunk_store 清理失败: {e}")
    try:
        # P0-2: 传入 file_path 作为 BM25 删除的第二过滤键（doc_id 协议分裂时按文件名兜底命中）
        pipeline.remove_documents_from_bm25([doc_id], file_paths=[file_path])
    except Exception as e:
        warnings.append(f"BM25 清理失败: {e}")
    if file_path:
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass  # 文件已不在，正常
        except OSError as e:
            warnings.append(f"原文件删除失败: {e}")


def _verify_doc_purged(doc_id: str, file_path: str, pipeline) -> list[str]:
    """回读各存储，返回残留描述列表（空 = 清理干净）。"""
    residue: list[str] = []
    try:
        result = pipeline.vectordb.get(where={"doc_id": doc_id})
        if result and result.get("ids"):
            residue.append(f"pgvector chunk 残留 {len(result['ids'])} 条")
    except Exception as e:
        residue.append(f"pgvector chunk 验证失败: {e}")
    try:
        result = pipeline.doc_db.get(where={"doc_id": doc_id})
        if result and result.get("ids"):
            residue.append(f"pgvector doc 残留 {len(result['ids'])} 条")
    except Exception as e:
        residue.append(f"pgvector doc 验证失败: {e}")
    try:
        from backend.rag.indexing.chunk_store import get_chunk_store
        cnt = get_chunk_store().count_by_doc_id(doc_id)
        if cnt > 0:
            residue.append(f"chunk_store 残留 {cnt} 条")
    except Exception as e:
        residue.append(f"chunk_store 验证失败: {e}")
    if pipeline.bm25_store is not None:
        try:
            bm25_docs = pipeline.bm25_store.load_docs()
            hits = [d for d in bm25_docs if (d.metadata or {}).get("doc_id") == doc_id]
            if hits:
                residue.append(f"BM25 残留 {len(hits)} chunks")
        except Exception as e:
            residue.append(f"BM25 验证失败: {e}")
    return residue


@router.delete("/documents/{doc_id}", dependencies=[Depends(require_rag_editor)])
async def delete_document(doc_id: str, request: Request):
    """删除文档 — 软删 registry + 清理两处向量 + 删原文件（防 sync 复活）"""
    source = _extract_source(request)
    batch_id = request.headers.get("X-Batch-Id") or None
    doc_name = ""
    _delete_t0 = time.time()
    authz = _require_authz(request)
    try:
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc:
            return {"ok": False, "error": "文档不存在"}
        doc_name = doc.get("file_name", "")
        # 归属校验（2026-10-01）：删除含 os.remove 副作用，必须先过归属裁决
        ok, reason = authz.can_manage_row(doc)
        if not ok:
            return _deny_manage(reason)
        file_path = doc.get("file_path", "")

        # remote 模式：app 不持有向量库，级联清理（向量/chunk_store/BM25/源文件）
        # 必须由持有本地索引的 rag-service 执行（2026-10-01 删除收口）
        from backend.config.rag import RAG_MODE
        if RAG_MODE == "remote":
            pipeline = await asyncio.to_thread(get_rag_pipeline)
            result = await asyncio.to_thread(pipeline.delete_document_cascade, doc_id)
            _safe_log_op(doc_id, doc_name, "delete", source, trace_id=None, batch_id=batch_id,
                         result="success" if result.get("ok") else "failed",
                         duration_ms=int((time.time() - _delete_t0) * 1000),
                         detail={"mode": "remote", "deleted_rows": result.get("deleted_rows"),
                                 "degraded": result.get("degraded"),
                                 "warnings": result.get("warnings")},
                         user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""))
            return result

        # ① 软删 registry — 按 doc_id 删所有行（修复绝对/相对路径重复行漏删）
        deleted_rows = reg.mark_deleted_by_doc_id(doc_id)
        logger.info(f"[RAG] 软删 doc_id={doc_id}: {deleted_rows} 行")
        warnings: list[str] = []
        if deleted_rows == 0:
            warnings.append("registry 中无活跃记录（可能已被删除）")

        # ②③④⑤ 级联清理向量/索引/文件（含 BM25）
        pipeline = await asyncio.to_thread(get_rag_pipeline)
        _purge_doc_vectors(doc_id, file_path, pipeline, warnings)

        # ⑥ 回读验证：确认各存储已清理干净
        residue = _verify_doc_purged(doc_id, file_path, pipeline)
        degraded = bool(residue)
        if degraded:
            warnings.extend(residue)
            logger.warning(f"[RAG] 删除后残留: doc_id={doc_id}, {residue}")

        logger.info(f"[RAG] 已删除文档: {doc_id}" + (f"（{len(warnings)} 个警告）" if warnings else ""))
        _safe_log_op(doc_id, doc_name, "delete", source, trace_id=None, batch_id=batch_id,
                     result="partial" if degraded else "success",
                     duration_ms=int((time.time() - _delete_t0) * 1000),
                     detail={"file_path": file_path, "deleted_rows": deleted_rows, "warnings": warnings or None},
                     user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""))
        return {"ok": True, "doc_id": doc_id, "degraded": degraded, "warnings": warnings or None}
    except Exception as e:
        logger.error(f"[RAG] 删除文档失败: {e}")
        _safe_log_op(doc_id, doc_name, "delete", source, trace_id=None, batch_id=batch_id,
                     result="failed", duration_ms=int((time.time() - _delete_t0) * 1000),
                     detail={"error": str(e)[:200]},
                     user_id=str(getattr(getattr(authz, "principal", None), "user_id", "") or ""))
        return {"ok": False, "error": str(e)}


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, request: Request):
    """获取文档的 Chunk 列表（含内容和 metadata）— 先授权，且仅对
    active（已发布）文档暴露正文；候选/失败文档不可见。数据源 =
    chunk_store（agent_memory.chunk_store，与 rag-service 同库）。"""
    try:
        authz = _require_authz(request)
        reg = _get_registry()
        doc = reg.get_by_doc_id(doc_id)
        if not doc or not authz.can_read_row(doc):
            return {"doc_id": doc_id, "chunks": [], "total": 0, "error": "文档不存在"}
        chunk_ids_str = doc.get("chunk_ids", "[]")
        import json as _json
        chunk_ids = _json.loads(chunk_ids_str) if isinstance(chunk_ids_str, str) else chunk_ids_str

        # 2026-10-04 修复：remote 模式下 app 本地 pipeline 无向量库，原路径
        # 恒走异常回退返回空壳（trace 页 chunk 视图为空的根因）。chunk 正文
        # 权威在 chunk_store（agent_memory.chunk_store，indexer 写入、app 与
        # rag-service 同库可读），直接按 doc_id 读取；registry 只做授权与
        # 发布状态门（active 才暴露正文，候选/失败不可见）。
        chunks = []
        try:
            from backend.rag.indexing.chunk_store import get_chunk_store
            status = str(doc.get("status") or "")
            if status == "active":
                rows = await asyncio.to_thread(
                    get_chunk_store().get_by_doc_id, doc_id)
                for r in rows:
                    content = r.get("content") or ""
                    chunks.append({
                        "id": f"{doc_id}:{r.get('chunk_index')}",
                        "content": content,
                        "metadata": {
                            k: r[k] for k in (
                                "doc_id", "chunk_index", "section_title",
                                "doc_type", "kb_id", "department", "char_count",
                            ) if k in r
                        },
                        "token_count": len(content.encode()),
                    })
            else:
                logger.info(f"[RAG] Chunk 查询跳过非 active 文档: status={status}")
        except Exception as e:
            logger.warning(f"[RAG] Chunk 查询失败: {e}")
            chunks = [{"id": cid, "content": "", "metadata": {}, "token_count": 0} for cid in chunk_ids]

        return {"doc_id": doc_id, "chunks": chunks, "total": len(chunks)}
    except HTTPException:
        raise
    except Exception as e:
        return {"doc_id": doc_id, "chunks": [], "total": 0, "error": str(e)}


@router.get("/chunks/{doc_id}/detail")
async def get_chunk_detail(doc_id: str, request: Request):
    """获取文档的完整 Chunk 文本（从 chunk_store 取，非向量库）。
    供 Trace 详情页查看每条 chunk 的完整内容、token 数、关键词。"""
    try:
        authz = _require_authz(request)
        doc = _get_registry().get_by_doc_id(doc_id)
        if not doc or not authz.can_read_row(doc):
            return {"doc_id": doc_id, "chunks": [], "total": 0, "error": "文档不存在"}
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
    except HTTPException:
        raise
    except Exception as e:
        return {"doc_id": doc_id, "chunks": [], "total": 0, "error": str(e)}

# ── S2 知识库授权盘点（2026-10-05，管理端「知识库授权」面板数据源）──
@router.get("/kb-authority", dependencies=[Depends(require_admin_user)])
async def kb_authority_overview():
    """全量 KB 注册口径盘点（admin）：owner_depts/audience/文档计数/状态。

    注册表以 backend/config/knowledge_base.py 为单一事实源（G2，禁止手抄
    第二份）；此端点只读派生，不做授权变更——可见性变更走代码评审（注册表
    变更随仓库发布），避免运行时改口径造成审计断链。
    """
    from backend.config.knowledge_base import KNOWLEDGE_BASES
    items = []
    counts: dict = {}
    try:
        reg = _get_registry()
        for kid in KNOWLEDGE_BASES:
            try:
                counts[kid] = reg.count_by_kb_id(kid)
            except Exception:
                counts[kid] = 0
    except Exception:
        logger.warning("KB 授权盘点：registry 计数失败，回退 0", exc_info=True)
    for kid, meta in sorted(KNOWLEDGE_BASES.items()):
        items.append({
            "kb_id": kid,
            "name": meta.get("name", kid),
            "domain": meta.get("domain", ""),
            "owner_depts": meta.get("owner_depts", []),
            "audience": meta.get("audience", "internal"),
            "deprecated": bool(meta.get("deprecated", False)),
            "read_only": bool(meta.get("read_only", False)),
            "alias_for": meta.get("alias_for"),
            "doc_count": counts.get(kid, 0),
        })
    return {"knowledge_bases": items, "total": len(items)}
