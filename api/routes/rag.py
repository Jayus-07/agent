"""RAG 路由 — 知识库检索问答"""
import asyncio
from fastapi import APIRouter, HTTPException
from api.schemas import RAGAskRequest, ErrorResponse
from api.deps import get_rag_pipeline

router = APIRouter(prefix="/rag", tags=["知识库"])


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
