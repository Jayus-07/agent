"""RAG 路由 — PR-2.x 拆分为 3 个子模块。

端点分布:
  - rag_search.py:    GET /health, /knowledge-bases, POST /search
  - rag_documents.py: GET /documents, /stats, /operations, /{id}, /chunks, DELETE, POST reindex
  - rag_upload.py:    POST /upload, GET /upload/{id}/stream
  - rag_lifecycle.py: POST /knowledge/{id}/lifecycle, /expire-at, GET events（C1/C2 生命周期）
  - 根 POST /rag:     直接用 RAG pipeline 问答
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import asyncio

from backend.app.api.routes.rag_search import router as search_router
from backend.app.api.routes.rag_documents import router as documents_router
from backend.app.api.routes.rag_upload import router as upload_router
from backend.app.api.routes.rag_lifecycle import router as lifecycle_router
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready
from backend.app.api.identity import require_principal

router = APIRouter(prefix="/rag", tags=["RAG"])

# 搜索 + 知识库
router.include_router(search_router)

# 文档 CRUD
router.include_router(documents_router)

# 上传 + 索引
router.include_router(upload_router)

# 知识生命周期（deprecate/restore/回审 + expire_at + 流转审计）
router.include_router(lifecycle_router)


# 根 POST — RAG 问答（需保持旧 API 兼容）
@router.post("", responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
async def rag_ask(req: RAGAskRequest, request: Request):
    require_rag_ready()
    # 主体统一走 Principal（授权收口）：subject_type/department 不再内联推导
    principal = require_principal(request)
    # 初始化锁等待与 LLM 问答都是长耗时同步操作，全部移入工作线程
    pipeline = await asyncio.to_thread(get_rag_pipeline)
    answer = await asyncio.to_thread(
        pipeline.ask,
        req.query,
        session_id=req.session_id or "rag-api",
        subject_type=principal.subject_type,
        department=principal.department,
        permissions=principal.permissions,
    )
    return {"query": req.query, "answer": answer, "session_id": req.session_id}
