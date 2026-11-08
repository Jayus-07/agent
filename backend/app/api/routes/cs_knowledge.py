"""cs_knowledge.py — 知识助手（cs-workstation-v2 P7）

  - GET  /cs/knowledge/unresolved        未解决问题聚合（7 日窗口，按归一化哈希聚类）
  - POST /cs/knowledge/faq-draft         由未解决问题生成 FAQ 草稿（status=draft，
                                          幂等 faq_key；进既有 cs_faq 草稿→发布机制）
  - GET  /cs/knowledge/search?q=         知识检索（FAQ 只读命中 + 置信分，零台账）
  - 话术管理复用 P4 /cs/canned-responses CRUD（个人/团队两级，零新增）

数据面：
  未解决聚合 = ai.unanswered_questions（拒答/空结果旁路台账，有租户与
  question_hash 聚类口径，管理端 /admin/unanswered/* 同源）。原型「低置信」
  徽章对应真实数据类别 sql_empty（前端显示「查询空结果」，TASK 已回写）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)

router = APIRouter(tags=["智能客服-知识助手"])

logger = logging.getLogger("rag_system.cs_knowledge")

_SOURCE_ZH = {"rag_miss": "拒答", "sql_empty": "查询空结果"}

# A7（cs-capability-gap）：坐席知识检索的 RAG 扩展通道——FAQ 速查层之外，
# 按客户视角检索知识库文档（与用户对话同一授权语义，subject_type=customer）。
_RAG_SEARCH_KBS = ("cs_faq", "cs_policy")
_RAG_TOP_K = 3
_RAG_SNIPPET_MAX_CHARS = 500


class QaFaqDraftBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    variants: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("question", "variants")
    @classmethod
    def _not_blank(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("问题不能为空白")
            return v
        cleaned = [item.strip() for item in v if item and item.strip()]
        if not cleaned and v:
            raise ValueError("变体不能全为空白")
        return cleaned


class UnresolvedItem(BaseModel):
    question: str
    variants: dict[str, int]
    hits: int
    last_seen: str | None = None
    source: str
    source_display: str


class UnresolvedResponse(BaseModel):
    items: list[UnresolvedItem]
    total: int


class FaqDraftResponse(BaseModel):
    faq_id: int
    faq_key: str
    status: str


class SearchItem(BaseModel):
    question: str
    answer: str
    score: float
    matched: bool
    # faq=速查层命中；rag=知识库文档命中（B1/A7 扩展通道，默认 faq 保持
    # 既有响应契约，旧前端不读该字段不受影响）
    source: str = "faq"


class SearchResponse(BaseModel):
    items: list[SearchItem]
    query: str


@router.get("/cs/knowledge/unresolved", response_model=UnresolvedResponse)
async def unresolved_questions(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> UnresolvedResponse:
    """未解决问题聚合（按 question_hash 聚类，含原始查询变体分布）。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    params = {"tenant": context.tenant_id, "since": since, "limit": limit}
    rows = (
        await session.execute(text("""
            SELECT u.question_hash,
                   (array_agg(u.question ORDER BY u.created_at DESC))[1]
                       AS latest_question,
                   count(*)                                              AS hits,
                   max(u.created_at)                                     AS last_seen,
                   (array_agg(u.source ORDER BY u.created_at DESC))[1]   AS source,
                   (SELECT jsonb_object_agg(v.question, v.cnt)
                      FROM (SELECT u2.question, count(*) AS cnt
                              FROM ai.unanswered_questions u2
                             WHERE u2.tenant_id = u.tenant_id
                               AND u2.question_hash = u.question_hash
                               AND u2.created_at >= :since
                             GROUP BY u2.question
                             ORDER BY cnt DESC) v)                     AS variants
            FROM ai.unanswered_questions u
            WHERE u.tenant_id = :tenant AND u.created_at >= :since
            GROUP BY u.question_hash, u.tenant_id
            ORDER BY hits DESC
            LIMIT :limit
        """), params)
    ).mappings().all()
    items = [
        UnresolvedItem(
            question=row["latest_question"],
            variants=row["variants"] or {},
            hits=int(row["hits"] or 0),
            last_seen=row["last_seen"].isoformat() if row["last_seen"] else None,
            source=str(row["source"] or "rag_miss"),
            source_display=_SOURCE_ZH.get(
                str(row["source"] or "rag_miss"), "拒答",
            ),
        )
        for row in rows
    ]
    return UnresolvedResponse(items=items, total=len(items))


@router.post("/cs/knowledge/faq-draft", response_model=FaqDraftResponse)
async def create_faq_draft(
    body: QaFaqDraftBody,
    context: AgentContext = Depends(require_agent),
) -> FaqDraftResponse:
    """生成 FAQ 草稿：写 ai.cs_faq status=draft（faq_key 幂等，不重复建行）。

    草稿不进线上（load_published 只取 published）；答案留占位由知识运营
    在管理端补全后发布——即「缺口 → 草稿 → 审核」闭环的入口。
    """
    from backend.customer_service.faq import get_faq_store

    variants = [v for v in body.variants if v != body.question]
    faq_id = get_faq_store().upsert_faq(
        body.question,
        "（草稿）待知识运营补充答案。",
        variants=variants,
        status="draft",
        tenant_id=context.tenant_id or "default",
        kb_refs=f"cs-knowledge-gap:{context.tenant_id}",
    )
    from backend.customer_service.faq import normalize_question

    return FaqDraftResponse(
        faq_id=faq_id,
        faq_key=normalize_question(body.question),
        status="draft",
    )


async def _rag_channel_items(query: str, tenant_id: str) -> list[SearchItem]:
    """RAG 扩展通道：知识库文档级检索（只读增强，失败逐库降级为空）。

    与用户对话同一授权语义（subject_type=customer，KB keep-set 服务端
    强制）；同步 HTTP 经 to_thread 执行，不阻塞事件循环。检索失败只记
    日志并跳过该库——FAQ 命中照常返回，不 fail 整个接口。
    """
    from backend.rag.client import get_rag_proxy

    proxy = get_rag_proxy()
    items: list[SearchItem] = []
    for kb_id in _RAG_SEARCH_KBS:
        try:
            docs = await asyncio.to_thread(
                proxy.retrieve_documents, query,
                kb_id=kb_id, top_k=_RAG_TOP_K,
                subject_type="customer", tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.warning(
                "[CSKnowledge] RAG 通道降级 kb=%s tenant=%s: %s",
                kb_id, tenant_id, exc,
            )
            continue
        for doc in docs:
            meta = doc.metadata or {}
            content = str(doc.page_content or "").strip()
            if not content:
                continue
            title = (
                meta.get("source_file")
                or meta.get("section_title")
                or meta.get("doc_id")
                or kb_id
            )
            distance = meta.get("score")
            # metadata.score 为向量距离（越小越相关），转相似度语义对齐
            # FAQ 分值方向；BM25 补充结果无 score，落 0.5 固定档
            if isinstance(distance, (int, float)):
                similarity = max(0.0, min(1.0, 1.0 - float(distance)))
            else:
                similarity = 0.5
            items.append(SearchItem(
                question=f"【知识库】{title}",
                answer=content[:_RAG_SNIPPET_MAX_CHARS],
                score=round(similarity, 4),
                matched=False,
                source="rag",
            ))
    return items


@router.get("/cs/knowledge/search", response_model=SearchResponse)
async def knowledge_search(
    q: str = Query(min_length=1, max_length=500),
    context: AgentContext = Depends(require_agent),
) -> SearchResponse:
    """知识检索：FAQ 只读命中（精确/模糊）+ RAG 知识库文档命中，零台账。"""
    from backend.customer_service.faq import get_faq_store

    query = q.strip()
    if not query:
        raise HTTPException(422, detail="检索词不能为空")
    results = get_faq_store().search(
        query, k=5, min_score=0.30, tenant_id=context.tenant_id or "default")
    items = [
        SearchItem(
            question=item["question"],
            answer=item["answer"],
            score=item["score"],
            matched=item["matched"],
        )
        for item in results
    ]
    rag_items = await _rag_channel_items(query, context.tenant_id)
    return SearchResponse(items=items + rag_items, query=query)
