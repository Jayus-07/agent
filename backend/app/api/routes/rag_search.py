"""RAG 搜索路由 — PR-2.x 从 rag.py 抽出。"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import StreamingResponse
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline, require_rag_ready, get_rag_status
from backend.app.api.routes._rag_shared import *

router = APIRouter()

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

@router.post("/ask", responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
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
