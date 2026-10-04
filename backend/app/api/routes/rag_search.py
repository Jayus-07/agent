"""RAG 搜索路由 — PR-2.x 从 rag.py 抽出。

2026-10-01 权限旁路收口：/search 不再直接调基础检索器（旧本地分支绕过
ChunkLevelRetriever 的 KB 白名单与 permission_scope 过滤，属于越权读），
统一改走 RAGPipeline.retrieve_documents —— 与 /ask 同一授权语义
（authz.retrieval_authorized_kbs keep-set + 文档级 permission 后过滤），
本地/remote 两种模式传同一组主体属性与角色。
"""
from fastapi import APIRouter, HTTPException, Request
from backend.app.api.schemas import RAGAskRequest, ErrorResponse
from backend.app.api.deps import get_rag_pipeline
from backend.app.api.identity import require_principal
from backend.rag.authz import RagAuthorization, RagAuthorizationError
import asyncio

from backend.shared.logger import logger
# 显式导入替代 import *（详见 rag_documents.py 同处注释）
from backend.app.api.routes._rag_shared import SearchRequest, _get_registry

router = APIRouter()

# /search 响应暴露的 metadata 白名单：file_path/permission_scope/person_names
# 等内部治理字段不对普通用户暴露（A 阶段验收项）
_SEARCH_META_KEYS = (
    "doc_id", "chunk_id", "vector_id", "kb_id", "department",
    "doc_type", "business_domain", "source_file", "chunk_index",
    "chunk_type", "section_title", "reporting_period",
)


@router.get("/health")
async def rag_health():
    """RAG 管道就绪检查 — 前端上传前轮询此端点。"""
    from backend.app.api.deps import get_rag_status
    return get_rag_status()

@router.get("/knowledge-bases")
async def list_knowledge_bases(request: Request):
    """知识库列表（含文档计数）— 按主体授权集合收窄（不返回不可见库）。"""
    principal = require_principal(request)
    try:
        authz = RagAuthorization.build(principal)
    except RagAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    from backend.config.knowledge_base import get_kb_list
    kbs = [kb for kb in get_kb_list() if authz.can_search_kb(kb["id"])]
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
async def search_knowledge(req: SearchRequest, request: Request):
    principal = require_principal(request)
    query = req.query
    """检索测试 — 授权检索链（不调 LLM），只返回主体可见文档"""
    if not query.strip():
        return {"query": query, "results": [], "error": "请输入检索词"}
    try:
        authz = RagAuthorization.build(principal)
    except RagAuthorizationError as e:
        # 授权服务故障 fail-closed：拒绝请求，绝不退化为无过滤检索
        logger.error(f"[RAG] /search 授权失败: {e}")
        raise HTTPException(status_code=403, detail="授权服务暂不可用，已拒绝检索")
    try:
        # 事件循环线程不得直接等 pipeline 初始化锁 —— 移到工作线程
        pipeline = await asyncio.to_thread(get_rag_pipeline)
        kb_id = getattr(req, "kb_id", "") or "default"
        if kb_id not in ("*", "default") and not authz.can_search_kb(kb_id):
            # 显式指定越权 KB：直接拒绝（避免「0 结果」掩盖配置错误）
            raise HTTPException(status_code=403, detail=f"没有知识库 '{kb_id}' 的访问权限")
        docs = await asyncio.to_thread(
            pipeline.retrieve_documents,
            query,
            kb_id,
            10,
            principal.subject_type,
            principal.department,
            principal.permissions,
            principal.roles,
        )
        results = []
        for i, doc in enumerate(docs):
            # 双保险后过滤：检索链内已过滤，此处按授权服务再裁一次
            meta = dict(doc.metadata or {})
            if not authz.can_read_row({
                "kb_id": meta.get("kb_id", ""),
                "permission_scope": meta.get("permission_scope", "general"),
            }):
                continue
            results.append({
                "index": i,
                "content": doc.page_content[:300],
                "score": meta.get("score"),
                "metadata": {k: meta[k] for k in _SEARCH_META_KEYS if k in meta},
            })
        try:
            stale = bool(getattr(pipeline, "is_index_stale", False))
        except Exception:  # noqa: BLE001 — 状态探测失败不阻塞检索结果
            stale = False
        return {"query": query, "results": results, "total": len(results),
                "index_status": "stale" if stale else "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RAG] 检索失败: {e}")
        return {"query": query, "results": [], "error": str(e)}


# ── 问答（已有）──

@router.post("/ask", responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
async def rag_ask(req: RAGAskRequest, request: Request):
    """知识库检索 + 大模型生成回答"""
    try:
        pipeline = await asyncio.to_thread(get_rag_pipeline)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    kb_id = req.kb_id or "default"
    principal = require_principal(request)
    try:
        authz = RagAuthorization.build(principal)
    except RagAuthorizationError as e:
        logger.error(f"[RAG] /ask 授权失败: {e}")
        raise HTTPException(status_code=403, detail="授权服务暂不可用，已拒绝检索")
    if kb_id not in ("*", "default") and not authz.can_search_kb(kb_id):
        raise HTTPException(status_code=403, detail=f"没有知识库 '{kb_id}' 的访问权限")
    # D1-6：sources 随返回值带回（请求级），不再读 RAGChain 单例属性——
    # 并发请求下单例属性互相覆盖，A 的响应可能带 B 的来源
    outcome = await asyncio.to_thread(
        pipeline.ask_result,
        req.question,
        req.session_id,
        kb_id=kb_id,
        subject_type=principal.subject_type,
        department=principal.department,
        permissions=principal.permissions,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        roles=principal.roles,
    )
    return {"answer": outcome.answer, "session_id": req.session_id,
            "sources": outcome.sources,
            # A5（2026-10-04）：answer_meta 随响应带回（拒答时含 answer_status
            # 稳定码），前端按枚举处理而非解析自然语言；加法字段向后兼容
            "answer_meta": outcome.answer_meta}
